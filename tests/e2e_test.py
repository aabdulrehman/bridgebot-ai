#!/usr/bin/env python3
"""
End-to-end test for BridgeBot AI.

Spins up the real FastAPI backend (fresh SQLite DB, template fallback LLM),
the real local_connector.py daemon, and a dashboard WebSocket client, then:

 1. registers a user + logs in
 2. creates a connector (raw key returned once)
 3. starts local_connector.py and waits for ONLINE
 4. triggers `echo` task and asserts LOG lines stream to the dashboard
 5. triggers a non-allowlisted command and asserts it is rejected
 6. submits training -> polls READY -> approves -> asserts SKILL_ACK and the
    skill file on disk + agent record updated
 7. kills the connector and asserts OFFLINE

Usage:  python tests/e2e_test.py            (from repo root, venv active)
        BASE_URL=http://host:port python tests/e2e_test.py --external   (against a running server)
        E2E_DATABASE_URL=postgresql://user:pw@host/db python tests/e2e_test.py   (Postgres instead of SQLite)
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def step(msg: str) -> None:
    print(f"\n=== {msg}", flush=True)


class Dashboard:
    """Collects every event from /ws/dashboard."""

    def __init__(self, ws_base: str, token: str) -> None:
        self.url = f"{ws_base}/ws/dashboard?token={token}"
        self.events: List[Dict[str, Any]] = []
        self._task: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        self.ws = await websockets.connect(self.url)
        self._task = asyncio.create_task(self._reader())

    async def _reader(self) -> None:
        try:
            async for raw in self.ws:
                self.events.append(json.loads(raw))
        except websockets.ConnectionClosed:
            pass

    async def wait_for(self, pred, timeout: float = 20.0) -> Dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for ev in list(self.events):  # list is small; full re-scan is cheap
                if pred(ev):
                    return ev
            await asyncio.sleep(0.1)
        raise AssertionError(f"timed out waiting for event; last events: {self.events[-5:]}")

    async def close(self) -> None:
        await self.ws.close()
        if self._task:
            await self._task


async def main() -> int:
    external = "--external" in sys.argv
    tmp = Path(tempfile.mkdtemp(prefix="bridgebot_e2e_"))
    procs: List[subprocess.Popen] = []
    try:
        if external:
            base = os.environ["BASE_URL"].rstrip("/")
        else:
            port = free_port()
            base = f"http://127.0.0.1:{port}"
            env = {**os.environ, "DATABASE_URL": os.getenv("E2E_DATABASE_URL", f"sqlite+aiosqlite:///{tmp/'e2e.db'}"), "OPENROUTER_API_KEY": "",
                   "JWT_SECRET": "e2e-secret-" + uuid.uuid4().hex, "MIN_TRAINING_COST": "250"}
            step(f"starting backend on {base}")
            procs.append(subprocess.Popen([PY, "-m", "uvicorn", "server:app", "--port", str(port), "--log-level", "warning"],
                                          cwd=ROOT / "backend", env=env, stdout=open(tmp / "backend.log", "w"), stderr=subprocess.STDOUT))
        ws_base = base.replace("https://", "wss://").replace("http://", "ws://")

        async with httpx.AsyncClient(base_url=base, timeout=30) as http:
            for _ in range(100):
                try:
                    if (await http.get("/healthz")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.2)
            else:
                raise AssertionError("backend never became healthy")
            print("health:", (await http.get("/healthz")).json())

            step("register + login")
            email = f"e2e_{uuid.uuid4().hex[:8]}@example.com"
            r = await http.post("/api/auth/register", json={"email": email, "password": "SuperSecret123"})
            assert r.status_code == 201, r.text
            r = await http.post("/api/auth/login", json={"email": email, "password": "SuperSecret123"})
            assert r.status_code == 200, r.text
            token = r.json()["access_token"]
            print("login ok, balance =", r.json()["token_balance"])
            assert (await http.post("/api/auth/login", json={"email": email, "password": "wrong-pass"})).status_code == 401
            H = {"Authorization": f"Bearer {token}"}

            step("create connector")
            r = await http.post("/api/connectors", json={"machine_name": "e2e-pc"}, headers=H)
            assert r.status_code == 201, r.text
            conn = r.json()
            cid, api_key = conn["id"], conn["api_key"]
            print("connector", cid, "key prefix", api_key[:8], "status", conn["status"])
            assert "api_key" not in (await http.get("/api/connectors", headers=H)).json()[0]

            dash = Dashboard(ws_base, token)
            await dash.start()
            snap = await dash.wait_for(lambda e: e["type"] == "SNAPSHOT")
            print("dashboard snapshot:", snap)

            step("bad API key is refused")
            try:
                async with websockets.connect(f"{ws_base}/ws/connect", additional_headers={"Authorization": "Bearer bbk_wrong"}) as bad:
                    await bad.recv()
                raise AssertionError("bad key accepted")
            except (websockets.InvalidStatus, websockets.ConnectionClosed) as exc:
                print("refused as expected:", type(exc).__name__)

            step("start local_connector.py")
            skills_dir = tmp / "skills"
            cenv = {**os.environ, "BRIDGEBOT_API_KEY": api_key, "BRIDGEBOT_SERVER_URL": base,
                    "BRIDGEBOT_MACHINE_NAME": "e2e-pc", "BRIDGEBOT_SKILLS_DIR": str(skills_dir)}
            conn_proc = subprocess.Popen([PY, str(ROOT / "connector" / "local_connector.py"), "--config", str(tmp / "none.json")],
                                         env=cenv, stdout=open(tmp / "connector.log", "w"), stderr=subprocess.STDOUT)
            procs.append(conn_proc)
            ev = await dash.wait_for(lambda e: e["type"] == "CONNECTOR_STATUS" and e["status"] == "ONLINE")
            print("event:", ev)
            hello = await dash.wait_for(lambda e: e["type"] == "HELLO")
            print("HELLO from", hello["machine"], hello["platform"])
            await asyncio.sleep(0.3)
            lst = (await http.get("/api/connectors", headers=H)).json()
            assert lst[0]["status"] == "ONLINE", lst
            print("REST status:", lst[0]["status"], "agents:", [a["agent_name"] for a in lst[0]["agents"]])

            step("trigger task: echo")
            r = await http.post("/api/trigger-task", json={"connector_id": cid, "command": "echo Hello from BridgeBot e2e"}, headers=H)
            assert r.status_code == 200, r.text
            tid = r.json()["task_id"]
            log_ev = await dash.wait_for(lambda e: e["type"] == "LOG" and e.get("task_id") == tid)
            res_ev = await dash.wait_for(lambda e: e["type"] == "TASK_RESULT" and e.get("task_id") == tid)
            print("LOG:", log_ev["stream"], "|", log_ev["line"])
            print("RESULT: exit", res_ev["exit_code"], "in", res_ev["duration_ms"], "ms")
            assert log_ev["line"] == "Hello from BridgeBot e2e" and res_ev["exit_code"] == 0

            step("trigger task: multi-line + stderr (ls on missing path)")
            r = await http.post("/api/trigger-task", json={"connector_id": cid, "command": ["ls", "/definitely/missing/path"]}, headers=H)
            tid2 = r.json()["task_id"]
            err_ev = await dash.wait_for(lambda e: e["type"] == "LOG" and e.get("task_id") == tid2 and e["stream"] == "stderr")
            res2 = await dash.wait_for(lambda e: e["type"] == "TASK_RESULT" and e.get("task_id") == tid2)
            print("STDERR:", err_ev["line"], "| exit", res2["exit_code"])
            assert res2["exit_code"] != 0

            step("trigger task: blocked command (python not allowlisted)")
            r = await http.post("/api/trigger-task", json={"connector_id": cid, "command": "python3 -c 'print(1)'"}, headers=H)
            tid3 = r.json()["task_id"]
            res3 = await dash.wait_for(lambda e: e["type"] == "TASK_RESULT" and e.get("task_id") == tid3)
            print("RESULT:", res3["exit_code"], res3["error"])
            assert res3["exit_code"] == -1 and "not in allowed_commands" in res3["error"]

            step("training: submit -> READY")
            r = await http.post("/api/training/submit", headers=H, json={
                "niche_requirements_text": "Real estate agency in Riyadh selling villas and apartments; qualify buyers, book viewings, answer in Arabic and English.",
                "business_name": "Riyadh Homes", "goals": ["qualify buyers", "book property viewings"], "tone": "warm and professional",
                "target_connector_id": cid, "target_agent_name": "default"})
            assert r.status_code == 202, r.text
            trid = r.json()["id"]
            for _ in range(150):
                tr = (await http.get(f"/api/training/{trid}", headers=H)).json()
                if tr["status"] in ("READY", "FAILED"):
                    break
                await asyncio.sleep(0.2)
            assert tr["status"] == "READY", tr
            p = tr["generated_payload_json"]
            print("status READY via", tr["generator"], "cost", tr["token_cost"], "skill_id", p["skill_id"])
            print("tools:", [t["name"] for t in p["tools"]])
            me = (await http.get("/api/auth/me", headers=H)).json()
            print("balance after training:", me["token_balance"])

            step("training: approve -> SKILL_INJECT -> SKILL_ACK")
            r = await http.post("/api/training/approve", headers=H, json={"training_request_id": trid})
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "APPROVED"
            ack = await dash.wait_for(lambda e: e["type"] == "SKILL_ACK" and e.get("request_id") == trid)
            print("SKILL_ACK:", {k: ack[k] for k in ("ok", "agent_name", "skill_id", "path")})
            assert ack["ok"] is True
            skill_file = Path(ack["path"])
            assert skill_file.is_file() and json.loads(skill_file.read_text())["skill_id"] == p["skill_id"]
            print("skill file on disk:", skill_file, f"({skill_file.stat().st_size} bytes)")
            await asyncio.sleep(0.3)
            agents = (await http.get("/api/connectors", headers=H)).json()[0]["agents"]
            injected = [a["current_injected_skills_json"] for a in agents if a["agent_name"] == "default"][0]
            print("agent.current_injected_skills_json:", injected)
            assert injected and injected[-1]["skill_id"] == p["skill_id"]

            audit = (await http.get(f"/api/connectors/{cid}/audit", headers=H)).json()
            print("audit events:", sorted({a["event_type"] for a in audit}))

            step("stop connector -> OFFLINE")
            conn_proc.terminate()
            ev = await dash.wait_for(lambda e: e["type"] == "CONNECTOR_STATUS" and e["status"] == "OFFLINE", timeout=15)
            print("event:", ev)
            r = await http.post("/api/trigger-task", json={"connector_id": cid, "command": "echo hi"}, headers=H)
            assert r.status_code == 409, r.text
            print("task to offline connector ->", r.status_code, r.json()["detail"])
            await dash.close()

        print("\n✅ ALL E2E CHECKS PASSED")
        return 0
    except Exception:
        for name in ("backend.log", "connector.log"):
            f = tmp / name
            if f.exists():
                print(f"\n----- {name} -----\n{f.read_text()[-4000:]}")
        raise
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    p.kill()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
