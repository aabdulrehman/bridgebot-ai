#!/usr/bin/env python3
"""
End-to-end test for BridgeBot AI (full SaaS).

Spins up the real FastAPI backend (fresh SQLite DB by default, template LLM),
the real local_connector.py daemon, a dashboard WebSocket client and a local
HTTP webhook receiver, then exercises:

  Bridge      register/login · connector create · daemon ONLINE · echo task streams
              LOG lines · stderr task · blocked command · OFFLINE · 409 when offline
  Billing     FREE auto-assigned · machine/API-key/webhook limits (402) · inbound hook
              blocked on FREE · upgrade request + WhatsApp link · admin approval →
              PRO · payment/revenue stats · limits lifted
  API keys    bbu_ key auth on REST · cannot reach admin · revoke → 401
  Webhooks    HMAC-signed connector.online / task.completed / training.ready /
              skill.injected / connector.offline · delivery log · retry after 500
  Inbound     POST /api/hooks/in/{token} triggers a task on the machine
  Training    submit → READY → approve → SKILL_INJECT → SKILL_ACK → file on disk
  Admin       ADMIN_EMAIL (case-insensitive) is the ONLY admin · stats · users ·
              change plan · token adjust · suspend (kicks connector) · reactivate ·
              connectors · audit

Usage:  python tests/e2e_test.py                         (from repo root, venv active)
        E2E_DATABASE_URL=postgresql://u:p@host/db python tests/e2e_test.py
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
ADMIN_EMAIL = "Admin.E2E@Example.com"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def step(msg: str) -> None:
    print(f"\n=== {msg}", flush=True)


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)
    print(f"  ✔ {msg}")


# ----------------------------------------------------------------------------- webhook receiver
class Receiver:
    """Local HTTP server recording webhook POSTs. /fail-once returns 500 the first time."""

    def __init__(self) -> None:
        self.port = free_port()
        self.hits: List[Dict[str, Any]] = []
        self._failed: set[str] = set()
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                body = self.rfile.read(int(self.headers.get("content-length", 0)))
                rec = {"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body,
                       "json": json.loads(body or b"{}")}
                if self.path == "/fail-once" and rec["json"].get("id") not in outer._failed:
                    outer._failed.add(rec["json"].get("id"))
                    rec["answered"] = 500
                    outer.hits.append(rec)
                    self.send_response(500); self.end_headers(); self.wfile.write(b"boom")
                    return
                rec["answered"] = 200
                outer.hits.append(rec)
                self.send_response(200); self.end_headers(); self.wfile.write(b"ok")

            def log_message(self, *a: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    async def wait(self, pred: Callable[[Dict[str, Any]], bool], timeout: float = 20) -> Dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for h in list(self.hits):
                if pred(h):
                    return h
            await asyncio.sleep(0.1)
        raise AssertionError(f"webhook not received; got {[h['json'].get('event') for h in self.hits]}")


def verify_sig(secret: str, body: bytes, header: str) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(","))
    mac = hmac.new(secret.encode(), f"{parts['t']}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, parts["v1"])


# ----------------------------------------------------------------------------- dashboard socket
class Dashboard:
    def __init__(self, ws_base: str, token: str) -> None:
        self.url = f"{ws_base}/ws/dashboard?token={token}"
        self.events: List[Dict[str, Any]] = []

    async def start(self) -> None:
        self.ws = await websockets.connect(self.url)
        self._task = asyncio.create_task(self._reader())

    async def _reader(self) -> None:
        try:
            async for raw in self.ws:
                self.events.append(json.loads(raw))
        except websockets.ConnectionClosed:
            pass

    async def wait_for(self, pred: Callable[[Dict[str, Any]], bool], timeout: float = 20.0) -> Dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for ev in list(self.events):
                if pred(ev):
                    return ev
            await asyncio.sleep(0.1)
        raise AssertionError(f"timed out waiting for event; last events: {self.events[-5:]}")

    async def close(self) -> None:
        await self.ws.close()
        await self._task


# ----------------------------------------------------------------------------- main
async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="bridgebot_e2e_"))
    procs: List[subprocess.Popen] = []
    rx = Receiver()
    try:
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        env = {**os.environ,
               "DATABASE_URL": os.getenv("E2E_DATABASE_URL", f"sqlite+aiosqlite:///{tmp/'e2e.db'}"),
               "OPENROUTER_API_KEY": "", "JWT_SECRET": "e2e-secret-" + uuid.uuid4().hex,
               "ADMIN_EMAIL": ADMIN_EMAIL, "ADMIN_PASSWORD": "", "ENVIRONMENT": "development",
               "WEBHOOK_ALLOW_PRIVATE": "true", "WEBHOOK_RETRY_BASE_S": "1"}
        step(f"starting backend on {base}")
        procs.append(subprocess.Popen([PY, "-m", "uvicorn", "server:app", "--port", str(port), "--log-level", "warning"],
                                      cwd=ROOT / "backend", env=env, stdout=open(tmp / "backend.log", "w"), stderr=subprocess.STDOUT))
        ws_base = base.replace("http://", "ws://")
        conn_proc: Optional[subprocess.Popen] = None

        async with httpx.AsyncClient(base_url=base, timeout=30) as http:
            for _ in range(150):
                try:
                    if (await http.get("/healthz")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.2)
            else:
                raise AssertionError("backend never became healthy")

            # ---------------------------------------------------------------- public
            step("public endpoints")
            plans = (await http.get("/api/plans")).json()
            check([p["id"] for p in plans] == ["FREE", "PRO", "BUSINESS"], "plans FREE/PRO/BUSINESS published")
            cfg = (await http.get("/api/public-config")).json()
            check(cfg["support_whatsapp"] == "966567009846", "support WhatsApp in public config")
            dl = await http.get("/api/downloads/local_connector.py")
            check(dl.status_code == 200 and b"LocalConnector" in dl.content, "local_connector.py downloadable")

            # ---------------------------------------------------------------- user
            step("register user → FREE plan auto-assigned")
            email = f"e2e_{uuid.uuid4().hex[:8]}@example.com"
            r = await http.post("/api/auth/register", json={"email": email, "password": "SuperSecret123"})
            check(r.status_code == 201 and r.json()["role"] == "user" and r.json()["plan_id"] == "FREE", "registered as FREE user")
            r = await http.post("/api/auth/login", json={"email": email.upper(), "password": "SuperSecret123"})
            check(r.status_code == 200, "login (case-insensitive email)")
            token = r.json()["access_token"]
            check((await http.post("/api/auth/login", json={"email": email, "password": "nope-nope"})).status_code == 401, "wrong password → 401")
            H = {"Authorization": f"Bearer {token}"}
            sub = (await http.get("/api/billing/me", headers=H)).json()
            check(sub["plan"]["id"] == "FREE" and sub["usage"]["token_balance"] == 5000 and sub["user"]["current_period_end"], "FREE: 5,000 tokens, period set")
            check((await http.get("/api/admin/stats", headers=H)).status_code == 403, "normal user cannot open admin API")

            step("plan limits on FREE")
            r = await http.post("/api/connectors", json={"machine_name": "e2e-pc"}, headers=H)
            check(r.status_code == 201, "machine #1 created")
            cid, conn_key = r.json()["id"], r.json()["api_key"]
            r = await http.post("/api/connectors", json={"machine_name": "e2e-pc-2"}, headers=H)
            check(r.status_code == 402, f"machine #2 blocked on FREE (402: {r.json()['detail']})")
            r = await http.post("/api/api-keys", json={"name": "ci"}, headers=H)
            check(r.status_code == 201 and r.json()["api_key"].startswith("bbu_"), "personal API key #1 created")
            ukey, ukey_id = r.json()["api_key"], r.json()["id"]
            check((await http.post("/api/api-keys", json={"name": "ci2"}, headers=H)).status_code == 402, "API key #2 blocked on FREE")
            r = await http.post("/api/webhooks", headers=H, json={"url": rx.url("/hook"), "events": ["connector.online", "connector.offline", "task.completed", "training.ready", "skill.injected"]})
            check(r.status_code == 201 and r.json()["secret"].startswith("whsec_"), "outbound webhook #1 registered")
            hook_id, secret = r.json()["id"], r.json()["secret"]
            check((await http.post("/api/webhooks", headers=H, json={"url": rx.url("/x"), "events": ["task.completed"]})).status_code == 402, "webhook #2 blocked on FREE")
            check((await http.post("/api/webhooks", headers=H, json={"url": rx.url("/x"), "events": ["bogus"]})).status_code in (402, 422), "invalid event rejected")

            step("personal API key auth")
            K = {"Authorization": f"Bearer {ukey}"}
            check((await http.get("/api/connectors", headers=K)).status_code == 200, "bbu_ key works on REST API")
            check((await http.get("/api/connectors", headers={"X-API-Key": ukey})).status_code == 200, "X-API-Key header works")
            check((await http.get("/api/admin/stats", headers=K)).status_code == 403, "API key cannot reach admin")

            dash = Dashboard(ws_base, token)
            await dash.start()
            await dash.wait_for(lambda e: e["type"] == "SNAPSHOT")

            step("bridge: start local_connector.py")
            try:
                async with websockets.connect(f"{ws_base}/ws/connect", additional_headers={"Authorization": "Bearer bbk_wrong"}) as bad:
                    await bad.recv()
                raise AssertionError("bad key accepted")
            except (websockets.InvalidStatus, websockets.ConnectionClosed):
                print("  ✔ bad connector key refused")
            skills_dir = tmp / "skills"
            cenv = {**os.environ, "BRIDGEBOT_API_KEY": conn_key, "BRIDGEBOT_SERVER_URL": base,
                    "BRIDGEBOT_MACHINE_NAME": "e2e-pc", "BRIDGEBOT_SKILLS_DIR": str(skills_dir)}

            def start_connector() -> subprocess.Popen:
                p = subprocess.Popen([PY, str(ROOT / "connector" / "local_connector.py"), "--config", str(tmp / "none.json")],
                                     env=cenv, stdout=open(tmp / "connector.log", "a"), stderr=subprocess.STDOUT)
                procs.append(p)
                return p

            conn_proc = start_connector()
            await dash.wait_for(lambda e: e["type"] == "CONNECTOR_STATUS" and e["status"] == "ONLINE")
            print("  ✔ dashboard got CONNECTOR_STATUS ONLINE")
            hit = await rx.wait(lambda h: h["json"].get("event") == "connector.online")
            check(verify_sig(secret, hit["body"], hit["headers"]["x-bridgebot-signature"]), "connector.online webhook HMAC signature valid")

            step("bridge: tasks (via personal API key)")
            r = await http.post("/api/trigger-task", json={"connector_id": cid, "command": "echo Hello from BridgeBot e2e"}, headers=K)
            check(r.status_code == 200, "task dispatched with API key")
            tid = r.json()["task_id"]
            log_ev = await dash.wait_for(lambda e: e["type"] == "LOG" and e.get("task_id") == tid)
            res = await dash.wait_for(lambda e: e["type"] == "TASK_RESULT" and e.get("task_id") == tid)
            print(f"  LOG: {log_ev['stream']} | {log_ev['line']}   RESULT: exit {res['exit_code']} in {res['duration_ms']} ms")
            check(log_ev["line"] == "Hello from BridgeBot e2e" and res["exit_code"] == 0, "stdout streamed live, exit 0")
            hit = await rx.wait(lambda h: h["json"].get("event") == "task.completed" and h["json"]["data"]["task_id"] == tid)
            check(hit["json"]["data"]["stdout_tail"] == ["Hello from BridgeBot e2e"], "task.completed webhook carries stdout_tail")
            r = await http.post("/api/trigger-task", json={"connector_id": cid, "command": ["ls", "/definitely/missing"]}, headers=H)
            tid2 = r.json()["task_id"]
            err = await dash.wait_for(lambda e: e["type"] == "LOG" and e.get("task_id") == tid2 and e["stream"] == "stderr")
            check("No such file" in err["line"], "stderr streamed")
            r = await http.post("/api/trigger-task", json={"connector_id": cid, "command": "python3 -c 'print(1)'"}, headers=H)
            res3 = await dash.wait_for(lambda e: e["type"] == "TASK_RESULT" and e.get("task_id") == r.json()["task_id"])
            check(res3["exit_code"] == -1 and "not in allowed_commands" in res3["error"], "non-allowlisted command blocked by daemon")

            step("inbound webhook blocked on FREE")
            inbound = (await http.get("/api/webhooks/inbound", headers=H)).json()
            r = await http.post(inbound["path"], json={"command": "echo from-zapier"})
            check(r.status_code == 402, f"inbound hook on FREE → 402 ({r.json()['detail']})")

            step("upgrade request → WhatsApp → admin approval")
            r = await http.post("/api/billing/upgrade-request", headers=H, json={"plan_id": "PRO", "payment_method": "paypal"})
            check(r.status_code == 422, "PayPal request without transaction id rejected")
            r = await http.post("/api/billing/upgrade-request", headers=H, json={"plan_id": "PRO", "payment_method": "paypal", "payment_reference": "PAYPAL-TXN-9XY12345"})
            check(r.status_code == 201 and r.json()["request"]["status"] == "PENDING" and r.json()["request"]["payment_provider"] == "paypal", "PayPal upgrade request PENDING")
            check(r.json()["paypal_url"] == "https://paypal.me/BunnyOnMoney/19USD", f"PayPal.Me link {r.json()['paypal_url']}")
            wa = r.json()["whatsapp_url"]
            check(wa.startswith("https://wa.me/966567009846?text=") and "Pro" in wa and email.split("@")[0] in wa, "WhatsApp link has plan + email")
            req_id = r.json()["request"]["id"]
            check((await http.post("/api/billing/upgrade-request", headers=H, json={"plan_id": "BUSINESS"})).status_code == 409, "duplicate pending request rejected")
            check((await http.get("/api/billing/me", headers=H)).json()["user"]["subscription_status"] == "PENDING_UPGRADE", "status PENDING_UPGRADE")
            check((await http.post(f"/api/admin/upgrade-requests/{req_id}/approve", headers=H, json={})).status_code == 403, "user cannot self-approve")

            step("admin: only ADMIN_EMAIL (case-insensitive) is admin")
            other = await http.post("/api/auth/register", json={"email": f"other_{uuid.uuid4().hex[:6]}@example.com", "password": "Password123"})
            check(other.json()["role"] == "user", "another new account is NOT admin")
            r = await http.post("/api/auth/register", json={"email": ADMIN_EMAIL.lower(), "password": "AdminPass123"})
            check(r.status_code == 201 and r.json()["role"] == "admin", "ADMIN_EMAIL account got role=admin")
            r = await http.post("/api/auth/login", json={"email": ADMIN_EMAIL.upper(), "password": "AdminPass123"})
            A = {"Authorization": f"Bearer {r.json()['access_token']}"}
            pend = (await http.get("/api/admin/upgrade-requests", headers=A)).json()
            check(any(p["id"] == req_id and p["user_email"] == email and p["payment_reference"] == "PAYPAL-TXN-9XY12345" for p in pend), "admin sees pending PayPal request + txn id")
            r = await http.post(f"/api/admin/upgrade-requests/{req_id}/approve", headers=A, json={"note": "paid via WhatsApp"})
            check(r.status_code == 200 and r.json()["user"]["plan_id"] == "PRO", "admin approved → user on PRO")
            sub = (await http.get("/api/billing/me", headers=H)).json()
            check(sub["plan"]["id"] == "PRO" and sub["usage"]["token_balance"] == 100000 and sub["user"]["subscription_status"] == "ACTIVE", "PRO active with 100,000 tokens")
            stats = (await http.get("/api/admin/stats", headers=A)).json()
            check(stats["revenue_total_cents"] == 1900 and stats["mrr_cents"] >= 1900 and stats["connectors_online"] >= 1, f"stats: revenue {stats['revenue_total_cents']}¢, MRR {stats['mrr_cents']}¢, online {stats['connectors_online']}")

            step("PRO limits lifted + inbound webhook")
            check((await http.post("/api/connectors", json={"machine_name": "e2e-pc-2"}, headers=H)).status_code == 201, "machine #2 allowed on PRO")
            r = await http.post("/api/webhooks", headers=H, json={"url": rx.url("/fail-once"), "events": ["task.completed"]})
            check(r.status_code == 201, "webhook #2 allowed on PRO")
            r = await http.post(inbound["path"], json={"command": "echo from-zapier", "machine_name": "e2e-pc"})
            check(r.status_code == 202, "inbound hook accepted on PRO")
            itid = r.json()["task_id"]
            ev = await dash.wait_for(lambda e: e["type"] == "LOG" and e.get("task_id") == itid)
            check(ev["line"] == "from-zapier", "inbound-triggered task output streamed")
            check((await http.post("/api/hooks/in/not-a-real-token-xxxxxxxxxxxx", json={"command": "echo x"})).status_code == 404, "unknown inbound token → 404")

            step("webhook retries + delivery log")
            hit = await rx.wait(lambda h: h["path"] == "/fail-once" and h["answered"] == 200 and h["json"]["data"]["task_id"] == itid, timeout=30)
            deliv = (await http.get("/api/webhooks/deliveries", headers=H)).json()
            d = next(x for x in deliv if x["id"] == hit["json"]["id"])
            check(d["status"] == "SUCCESS" and d["attempts"] == 2, f"failed once (500) then retried → SUCCESS after {d['attempts']} attempts")
            r = await http.post(f"/api/webhooks/{hook_id}/test", headers=H)
            await rx.wait(lambda h: h["json"].get("event") == "webhook.test")
            print("  ✔ 'send test' delivery received")

            step("training: submit → READY → approve → SKILL_ACK")
            r = await http.post("/api/training/submit", headers=H, json={
                "niche_requirements_text": "Real estate agency in Riyadh selling villas; qualify buyers and book viewings.",
                "business_name": "Riyadh Homes", "goals": ["qualify buyers"], "target_connector_id": cid, "target_agent_name": "default"})
            trid = r.json()["id"]
            for _ in range(150):
                tr = (await http.get(f"/api/training/{trid}", headers=H)).json()
                if tr["status"] in ("READY", "FAILED"):
                    break
                await asyncio.sleep(0.2)
            check(tr["status"] == "READY", f"READY via {tr['generator']} cost {tr['token_cost']}")
            await rx.wait(lambda h: h["json"].get("event") == "training.ready" and h["json"]["data"]["training_request_id"] == trid)
            print("  ✔ training.ready webhook")
            r = await http.post("/api/training/approve", headers=H, json={"training_request_id": trid})
            check(r.status_code == 200 and r.json()["status"] == "APPROVED", "approved")
            ack = await dash.wait_for(lambda e: e["type"] == "SKILL_ACK" and e.get("request_id") == trid)
            check(ack["ok"] and Path(ack["path"]).is_file(), f"SKILL_ACK ok, file {Path(ack['path']).name}")
            await rx.wait(lambda h: h["json"].get("event") == "skill.injected")
            print("  ✔ skill.injected webhook")
            check((await http.get("/api/billing/me", headers=H)).json()["usage"]["tokens_used_period"] == tr["token_cost"], "tokens_used_period tracked")

            step("admin actions")
            users = (await http.get("/api/admin/users", headers=A, params={"q": email.split("@")[0]})).json()
            uid = users["users"][0]["id"]
            check(users["users"][0]["machines_online"] == 1, "admin user list shows online machine")
            r = await http.patch(f"/api/admin/users/{uid}", headers=A, json={"token_adjust": 1234})
            check(r.status_code == 200 and r.json()["user"]["token_balance"] == 100000 - tr["token_cost"] + 1234, "token adjust +1234")
            r = await http.patch(f"/api/admin/users/{uid}", headers=A, json={"plan_id": "BUSINESS", "months": 2})
            check(r.json()["user"]["plan_id"] == "BUSINESS", "admin changed plan → BUSINESS")
            r = await http.patch(f"/api/admin/users/{uid}", headers=A, json={"status": "SUSPENDED", "note": "e2e"})
            check(r.json()["user"]["status"] == "SUSPENDED", "user suspended")
            await asyncio.sleep(1)
            check((await http.get("/api/connectors", headers=H)).status_code == 403, "suspended user's API calls → 403")
            check((await http.post("/api/auth/login", json={"email": email, "password": "SuperSecret123"})).status_code == 403, "suspended user cannot log in")
            conns = (await http.get("/api/admin/connectors", headers=A)).json()
            check(not next(c for c in conns if c["id"] == cid)["online"], "suspension kicked the live connector")
            r = await http.patch(f"/api/admin/users/{uid}", headers=A, json={"status": "ACTIVE"})
            check(r.json()["user"]["status"] == "ACTIVE", "user reactivated")
            await asyncio.sleep(4)  # daemon reconnects with backoff
            conns = (await http.get("/api/admin/connectors", headers=A)).json()
            check(next(c for c in conns if c["id"] == cid)["online"], "connector auto-reconnected after reactivation")
            audit = (await http.get("/api/admin/audit", headers=A, params={"event_type": "ADMIN"})).json()
            check(any(a["event_type"] == "ADMIN_USER_UPDATE" for a in audit), "admin actions audited")
            pays = (await http.get("/api/admin/payments", headers=A)).json()
            check(pays and pays[0]["amount_cents"] == 1900 and pays[0]["user_email"] == email and pays[0]["provider"] == "paypal", "PayPal payment recorded in revenue ledger")
            check((await http.patch(f"/api/admin/users/{r.json()['user']['id']}", headers=A, json={"status": "BANNED"})).status_code == 200, "ban works")
            check((await http.get("/api/connectors", headers=H)).status_code == 403, "banned user blocked")
            await http.patch(f"/api/admin/users/{uid}", headers=A, json={"status": "ACTIVE"})

            step("API key revoke")
            check((await http.delete(f"/api/api-keys/{ukey_id}", headers=H)).status_code == 200, "revoked")
            check((await http.get("/api/connectors", headers=K)).status_code == 401, "revoked key → 401")

            step("stop connector → OFFLINE")
            await asyncio.sleep(3)
            for p in procs[1:]:
                p.terminate()
            await rx.wait(lambda h: h["json"].get("event") == "connector.offline")
            print("  ✔ connector.offline webhook")
            r = await http.post("/api/trigger-task", json={"connector_id": cid, "command": "echo hi"}, headers=H)
            check(r.status_code == 409, "task to offline connector → 409")
            await dash.close()

        print("\n✅ ALL E2E CHECKS PASSED")
        return 0
    except Exception:
        for name in ("backend.log", "connector.log"):
            f = tmp / name
            if f.exists():
                print(f"\n----- {name} -----\n{f.read_text()[-5000:]}")
        raise
    finally:
        rx.server.shutdown()
        for p in procs:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    p.kill()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
