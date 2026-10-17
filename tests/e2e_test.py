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
  Bot API     register bots (poll / webhook / websocket / connector) · bbt_ token auth ·
              long-poll updates · owner<->bot chat · plan bot limit
  Marketplace published Master Bots · plan gating · join → automated welcome + skill
              injection + follow-up test → GRADUATED (all 4 delivery types) · one-click
              "Make my bot a master" (persona clone) · group chat + master auto-guide ·
              bot.joined / bot.onboarded webhooks · admin publish from training
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
                if self.path == "/bot":
                    upd = rec["json"].get("update") or {}
                    reply: Dict[str, Any] = {"ok": True}
                    if upd.get("kind") == "task":
                        expect = (upd.get("payload") or {}).get("expect") or "ok"
                        reply = {"text": f"I'd build an n8n {expect}: Stripe trigger → filter → Slack node, with an error branch."}
                    out = json.dumps(reply).encode()
                    self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(out)
                    return
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


# ----------------------------------------------------------------------------- local app behind a tunnel
BINARY = bytes(range(256)) * 4096  # 1 MiB, every byte value


class LocalApp:
    """A real http.server on 127.0.0.1 that the tunnel forwards to."""

    def __init__(self) -> None:
        self.port = free_port()

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _send(self, code: int, body: bytes, ctype: str = "application/json", extra: Optional[List[tuple]] = None) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for k, v in extra or []:
                    self.send_header(k, v)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _info(self, body: bytes = b"") -> bytes:
                return json.dumps({"method": self.command, "path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                                   "body_len": len(body), "body": body.decode("utf-8", "replace")[:2000]}).encode()

            def _body(self) -> bytes:
                n = int(self.headers.get("content-length") or 0)
                return self.rfile.read(n) if n else b""

            def do_GET(self) -> None:  # noqa: N802
                if self.path.startswith("/binary"):
                    return self._send(200, BINARY, "application/octet-stream")
                if self.path.startswith("/redirect"):
                    return self._send(302, b"", "text/plain", [("Location", "/hello?from=redirect")])
                if self.path.startswith("/slow"):
                    time.sleep(4)
                    return self._send(200, b"late")
                if self.path.startswith("/cookies"):
                    return self._send(200, b"{}", extra=[("Set-Cookie", "a=1; Path=/"), ("Set-Cookie", "b=2; Path=/"), ("X-Local-App", "yes")])
                return self._send(200, self._info())

            do_HEAD = do_GET  # noqa: N815

            def do_POST(self) -> None:  # noqa: N802
                body = self._body()
                if self.path.startswith("/echo-bytes"):
                    return self._send(201, body, self.headers.get("content-type", "application/octet-stream"))
                return self._send(200, self._info(body))

            do_PUT = do_POST  # noqa: N815
            do_PATCH = do_POST  # noqa: N815

            def do_DELETE(self) -> None:  # noqa: N802
                return self._send(204, b"")

            def log_message(self, *a: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


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
    app = LocalApp()
    try:
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        env = {**os.environ,
               "DATABASE_URL": os.getenv("E2E_DATABASE_URL", f"sqlite+aiosqlite:///{tmp/'e2e.db'}"),
               "OPENROUTER_API_KEY": "", "JWT_SECRET": "e2e-secret-" + uuid.uuid4().hex,
               "ADMIN_EMAIL": ADMIN_EMAIL, "ADMIN_PASSWORD": "", "ENVIRONMENT": "development",
               "WEBHOOK_ALLOW_PRIVATE": "true", "WEBHOOK_RETRY_BASE_S": "1", "TUNNEL_TIMEOUT_S": "2"}
        step(f"starting backend on {base}")
        procs.append(subprocess.Popen([PY, "-m", "uvicorn", "server:app", "--port", str(port), "--log-level", "warning"],
                                      cwd=ROOT / "backend", env=env, stdout=open(tmp / "backend.log", "w"), stderr=subprocess.STDOUT))
        ws_base = base.replace("http://", "ws://")
        conn_proc: Optional[subprocess.Popen] = None

        async with httpx.AsyncClient(base_url=base, timeout=30) as http:
            for _ in range(600):
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
                    "BRIDGEBOT_MACHINE_NAME": "e2e-pc", "BRIDGEBOT_SKILLS_DIR": str(skills_dir), "BRIDGEBOT_AUTH_RETRY_S": "1"}

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

            step("HTTP tunnel: /t/{slug}/… → connector → real local http.server")
            r = await http.post("/api/tunnels", headers=H, json={"connector_id": cid, "local_port": app.port, "name": "local app", "slug": "e2e-app-" + uuid.uuid4().hex[:6]})
            check(r.status_code == 201 and r.json()["access_token"] is None, "tunnel created (unprotected)")
            tun = r.json()["tunnel"]
            check(tun["public_url"].endswith(f"/t/{tun['slug']}/") and tun["online"], f"public URL {tun['public_url']} · machine online")
            check((await http.post("/api/tunnels", headers=H, json={"connector_id": cid, "local_port": 9})).status_code == 402, "tunnel #2 blocked on FREE (402)")
            T = f"/t/{tun['slug']}"
            r = await http.get(f"{T}/hello/w%C3%B6rld%20x?x=1&y=two%20words", headers={"X-Custom": "abc", "Accept": "application/json"})
            info = r.json()
            check(r.status_code == 200 and info["method"] == "GET" and info["path"] == "/hello/w%C3%B6rld%20x?x=1&y=two%20words", f"GET path+query forwarded verbatim ({info['path']})")
            check(info["headers"]["x-custom"] == "abc" and info["headers"]["host"] == f"localhost:{app.port}" and info["headers"]["x-forwarded-prefix"] == T, "request headers + X-Forwarded-* forwarded, Host rewritten")
            check(r.headers.get("x-bridgebot-tunnel") == tun["slug"], "response tagged X-BridgeBot-Tunnel")
            r = await http.post(f"{T}/api/items", json={"name": "widget", "qty": 3})
            check(r.status_code == 200 and r.json()["method"] == "POST" and json.loads(r.json()["body"]) == {"name": "widget", "qty": 3}, "POST JSON body forwarded")
            blob = os.urandom(300_000) + bytes(range(256))
            r = await http.post(f"{T}/echo-bytes", content=blob, headers={"Content-Type": "application/octet-stream"})
            check(r.status_code == 201 and r.content == blob, f"binary POST round-trip byte-exact ({len(blob)} bytes)")
            r = await http.get(f"{T}/binary")
            check(r.status_code == 200 and r.content == BINARY and r.headers["content-type"] == "application/octet-stream", "binary GET 1 MiB byte-exact")
            r = await http.put(f"{T}/thing/1", content=b"put-body")
            check(r.json()["method"] == "PUT" and r.json()["body"] == "put-body", "PUT forwarded")
            check((await http.delete(f"{T}/thing/1")).status_code == 204, "DELETE → 204")
            r = await http.head(f"{T}/hello")
            check(r.status_code == 200 and r.content == b"", "HEAD → 200, no body")
            r = await http.get(f"{T}/redirect", follow_redirects=False)
            check(r.status_code == 302 and r.headers["location"] == f"{T}/hello?from=redirect", "redirect Location rewritten into the tunnel")
            r = await http.get(f"{T}/cookies")
            check(r.headers.get_list("set-cookie") == ["a=1; Path=/", "b=2; Path=/"] and r.headers["x-local-app"] == "yes", "multiple Set-Cookie + custom response headers preserved")
            r = await http.get(f"/t/{tun['slug']}", follow_redirects=False)
            check(r.status_code == 307 and r.headers["location"] == f"/t/{tun['slug']}/", "bare /t/{slug} → 307 to trailing slash")
            t0 = time.monotonic()
            r = await http.get(f"{T}/slow")
            check(r.status_code == 504 and "Tunnel timeout" in r.text, f"slow local service → 504 page after {time.monotonic() - t0:.1f}s (got {r.status_code})")
            r = await http.get("/t/does-not-exist/x")
            check(r.status_code == 404 and "Tunnel not found" in r.text, "unknown slug → 404 page")
            r = await http.post(f"{T}/echo-bytes", content=b"x" * (8 * 1024 * 1024 + 1))
            check(r.status_code == 413, "request > 8 MB → 413")

            step("HTTP tunnel: access token, pause, wrong port")
            r = await http.patch(f"/api/tunnels/{tun['id']}", headers=H, json={"require_token": True})
            ttok = r.json()["access_token"]
            check(ttok and ttok.startswith("tnl_") and r.json()["tunnel"]["protected"], "access token enabled (tnl_…)")
            r = await http.get(f"{T}/hello")
            check(r.status_code == 401 and "Access token required" in r.text, "no token → 401 page")
            check((await http.get(f"{T}/hello", headers={"X-Tunnel-Token": "tnl_wrong"})).status_code == 401, "wrong token → 401")
            r = await http.get(f"{T}/hello", headers={"X-Tunnel-Token": ttok})
            check(r.status_code == 200 and "x-tunnel-token" not in r.json()["headers"], "X-Tunnel-Token works and is not forwarded")
            r = await http.get(f"{T}/hello?a=1&tunnel_token={ttok}")
            check(r.status_code == 200 and r.json()["path"] == "/hello?a=1", "?tunnel_token= works and is stripped from the query")
            await http.patch(f"/api/tunnels/{tun['id']}", headers=H, json={"is_active": False})
            check((await http.get(f"{T}/hello", headers={"X-Tunnel-Token": ttok})).status_code == 503, "paused tunnel → 503")
            await http.patch(f"/api/tunnels/{tun['id']}", headers=H, json={"is_active": True, "require_token": False, "local_port": free_port()})
            r = await http.get(f"{T}/hello")
            check(r.status_code == 502 and "nothing is listening" in r.text, "closed local port → 502 page")
            await http.patch(f"/api/tunnels/{tun['id']}", headers=H, json={"local_port": app.port})
            lst = (await http.get("/api/tunnels", headers=H)).json()
            check(lst[0]["requests_count"] >= 10 and lst[0]["bytes_out"] > 1_000_000 and lst[0]["machine_name"] == "e2e-pc", f"usage stats: {lst[0]['requests_count']} requests, {lst[0]['bytes_out']} bytes out")
            o_conn = await http.post("/api/connectors", headers={"Authorization": f"Bearer {token}"}, json={"machine_name": "x"})
            check(o_conn.status_code == 402, "(machine limit still enforced)")

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
            if r.status_code == 409:  # re-used database: the admin account already exists
                r = await http.post("/api/auth/login", json={"email": ADMIN_EMAIL.lower(), "password": "AdminPass123"})
            check(r.status_code in (200, 201) and r.json()["role"] == "admin", "ADMIN_EMAIL account got role=admin")
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
            for _ in range(600):
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
            await asyncio.sleep(5)  # daemon reconnects (auth-failure retry = 1 s in this test)
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

            # ================================================================ Universal Bot API + Marketplace
            async def wait_member(hdr: Dict[str, str], bot_id: str, master_slug_or_name: str, want: str, timeout: float = 25) -> Dict[str, Any]:
                deadline = time.monotonic() + timeout
                last: Any = None
                while time.monotonic() < deadline:
                    ms = (await http.get("/api/marketplace/me/memberships", headers=hdr)).json()
                    last = [m for m in ms if m["bot_id"] == bot_id]
                    for m in last:
                        if m["status"] == want:
                            return m
                        if m["status"] == "FAILED" and want != "FAILED":
                            raise AssertionError(f"onboarding FAILED: {m}")
                    await asyncio.sleep(0.2)
                raise AssertionError(f"membership never reached {want}: {last}")

            step("marketplace: public catalogue + plan gating")
            mk = (await http.get("/api/marketplace")).json()
            slugs = {m["slug"]: m for m in mk}
            check({"customer-support-master", "n8n-automation-architect", "sales-closer-elite"} <= set(slugs), f"{len(mk)} Master Bots published")
            check(slugs["sales-closer-elite"]["min_plan_id"] == "BUSINESS" and slugs["n8n-automation-architect"]["min_plan_id"] == "PRO", "plan gates FREE/PRO/BUSINESS")
            cs_id = slugs["customer-support-master"]["id"]

            step("Bot API: FREE user links a polling bot (Hermes)")
            O = {"Authorization": f"Bearer {other.json()['access_token']}"}
            r = await http.post("/api/bots", headers=O, json={"name": "hermes-helper", "framework": "hermes", "delivery": "poll"})
            check(r.status_code == 201 and r.json()["token"].startswith("bbt_") and r.json()["secret"].startswith("whsec_"), "bot registered → bbt_ token + signing secret")
            pbot, ptoken = r.json()["bot"]["id"], r.json()["token"]
            check((await http.post("/api/bots", headers=O, json={"name": "second"})).status_code == 402, "bot #2 blocked on FREE (402)")
            B = {"Authorization": f"Bot {ptoken}"}
            check((await http.get("/api/bot/me", headers={"Authorization": "Bot bbt_wrong"})).status_code == 401, "bad bot token → 401")
            me = (await http.get("/api/bot/me", headers=B)).json()
            check(me["id"] == pbot and me["framework"] == "hermes", "GET /api/bot/me with bot token")
            r = await http.post(f"/api/bots/{pbot}/messages", headers=O, json={"text": "hi bot, status?"})
            check(r.status_code == 201, "owner → bot message queued")
            up = (await http.get("/api/bot/updates", headers=B, params={"offset": 0, "timeout": 5})).json()
            check(up["updates"] and up["updates"][0]["text"] == "hi bot, status?" and up["updates"][0]["sender"] == "owner", "bot long-polled the owner message")
            offset = up["next_offset"]
            r = await http.post("/api/bot/messages", headers=B, json={"text": "all systems green", "reply_to": up["updates"][0]["update_id"]})
            check(r.status_code == 201, "bot replied via REST")
            conv = (await http.get(f"/api/bots/{pbot}/messages", headers=O)).json()
            check([m["direction"] for m in conv] == ["in", "out"] and conv[1]["reply_to"] == conv[0]["update_id"], "owner sees the conversation thread")
            check((await http.post(f"/api/marketplace/sales-closer-elite/join", headers=O, json={"bot_id": pbot})).status_code == 402, "FREE user blocked from BUSINESS master (402)")

            step("marketplace: polling bot joins Customer Support Master → automated onboarding")
            r = await http.post(f"/api/marketplace/{cs_id}/join", headers=O, json={"bot_id": pbot})
            check(r.status_code == 200 and r.json()["onboarding_started"], "joined the master's group, onboarding started")
            got: List[Dict[str, Any]] = []
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not any(u["kind"] == "skill_inject" for u in got):
                up = (await http.get("/api/bot/updates", headers=B, params={"offset": offset, "timeout": 5})).json()
                got += up["updates"]; offset = up["next_offset"]
            kinds = [u["kind"] for u in got]
            check("welcome" in kinds and "skill_inject" in kinds, f"master sent welcome + skill package ({kinds})")
            inj = next(u for u in got if u["kind"] == "skill_inject")
            check(inj["payload"]["skill"]["skill_id"] == "master_customer_support_master" and inj["payload"]["skill"]["persona"], "skill payload carries skill_id + persona + tools")
            check((await http.post("/api/bot/ack", headers=B, json={"update_id": inj["update_id"], "ok": True})).status_code == 200, "bot ACKed the skill")
            test_upd = None
            for _ in range(5):
                up = (await http.get("/api/bot/updates", headers=B, params={"offset": offset, "timeout": 5})).json()
                offset = up["next_offset"]
                test_upd = next((u for u in up["updates"] if u["kind"] == "task"), None)
                if test_upd:
                    break
            check(test_upd is not None and test_upd["payload"]["expect"] == "support", "follow-up test task received")
            await http.post("/api/bot/messages", headers=B, json={"text": "Sorry for the delay!", "reply_to": test_upd["update_id"]})
            up = (await http.get("/api/bot/updates", headers=B, params={"offset": offset, "timeout": 5})).json()
            offset = up["next_offset"]
            retry = next((u for u in up["updates"] if u["kind"] == "task"), None)
            check(retry is not None and "support" in retry["text"], "wrong answer → master coaches and re-tests")
            await http.post("/api/bot/messages", headers=B, json={"text": "Hi! Customer support here — I'm checking order #1234 now.", "reply_to": retry["update_id"]})
            m = await wait_member(O, pbot, "customer-support-master", "GRADUATED")
            check(m["skill_id"] == "master_customer_support_master", "polling bot GRADUATED")
            sk = (await http.get("/api/bot/skills", headers=B)).json()
            check(sk["skills"] and sk["skills"][0]["skill"]["skill_id"] == "master_customer_support_master", "GET /api/bot/skills returns the installed skill")

            step("marketplace: webhook bot (n8n) → one-click 'Make my bot a master'")
            r = await http.post("/api/webhooks", headers=H, json={"url": rx.url("/events"), "events": ["bot.joined", "bot.onboarded", "bot.message"]})
            check(r.status_code == 201, "owner webhook for bot.* events")
            ev_secret = r.json()["secret"]
            r = await http.post("/api/bots", headers=H, json={"name": "n8n-flow-bot", "framework": "n8n", "delivery": "webhook", "webhook_url": rx.url("/bot")})
            wbot, wsecret = r.json()["bot"]["id"], r.json()["secret"]
            r = await http.post("/api/marketplace/n8n-automation-architect/make-master", headers=H, json={"bot_id": wbot})
            check(r.status_code == 200 and r.json()["membership"]["master_clone"], "make-master accepted (PRO+ master on BUSINESS plan)")
            m = await wait_member(H, wbot, "n8n", "GRADUATED")
            check(m["master_clone"], "webhook bot auto-onboarded with zero manual steps → GRADUATED master clone")
            pushes = [h for h in rx.hits if h["path"] == "/bot"]
            check({h["json"]["update"]["kind"] for h in pushes} >= {"welcome", "skill_inject", "task"}, "welcome, skill_inject and test pushed to the bot webhook")
            check(all(verify_sig(wsecret, h["body"], h["headers"]["x-bridgebot-signature"]) for h in pushes), "bot pushes HMAC-signed with the bot secret")
            b = next(x for x in (await http.get("/api/bots", headers=H)).json() if x["id"] == wbot)
            check(b["persona"] and b["skills"][0]["skill_id"] == "master_n8n_automation_architect", "bot now carries the master persona + skill")
            hit = await rx.wait(lambda h: h["path"] == "/events" and h["json"].get("event") == "bot.onboarded" and h["json"]["data"]["bot_id"] == wbot)
            check(verify_sig(ev_secret, hit["body"], hit["headers"]["x-bridgebot-signature"]) and hit["json"]["data"]["master_clone"], "bot.onboarded owner webhook (signed)")
            await rx.wait(lambda h: h["path"] == "/events" and h["json"].get("event") == "bot.joined")
            print("  ✔ bot.joined owner webhook")

            step("marketplace: WebSocket bot (OpenClaw) + group chat + master auto-guide")
            r = await http.post("/api/bots", headers=H, json={"name": "openclaw-ws", "framework": "openclaw", "delivery": "websocket"})
            wsbot, wstoken = r.json()["bot"]["id"], r.json()["token"]
            async with websockets.connect(f"{ws_base}/ws/bot?token={wstoken}") as bws:
                ready = json.loads(await bws.recv())
                check(ready["type"] == "READY" and ready["bot_id"] == wsbot, "bot WebSocket READY")

                async def next_update(pred: Callable[[Dict[str, Any]], bool], timeout: float = 15) -> Dict[str, Any]:
                    end = time.monotonic() + timeout
                    while time.monotonic() < end:
                        msg = json.loads(await asyncio.wait_for(bws.recv(), timeout=end - time.monotonic()))
                        if msg.get("type") == "UPDATE" and pred(msg):
                            return msg
                    raise AssertionError("update not received")
                await http.post(f"/api/marketplace/{cs_id}/join", headers=H, json={"bot_id": wsbot})
                await next_update(lambda u: u["kind"] == "welcome")
                inj = await next_update(lambda u: u["kind"] == "skill_inject")
                await bws.send(json.dumps({"type": "ACK", "update_id": inj["update_id"], "ok": True}))
                t = await next_update(lambda u: u["kind"] == "task")
                await bws.send(json.dumps({"type": "MESSAGE", "text": "Customer support ready to help!", "reply_to": t["update_id"]}))
                await wait_member(H, wsbot, "customer", "GRADUATED")
                print("  ✔ WebSocket bot instantly onboarded → GRADUATED")
                await bws.send(json.dumps({"type": "MESSAGE", "chat": "group", "group_id": cs_id, "text": "@master how should I handle refunds?"}))
                guide = await next_update(lambda u: u["sender"].startswith("master:") and u["chat"] == "group" and "Tools:" in u["text"])
                check("lookup_order" in guide["text"], "master auto-guides questions in the group")
            up = (await http.get("/api/bot/updates", headers=B, params={"offset": offset, "timeout": 5})).json()
            check(any("handle refunds" in u["text"] and u["sender"] == "bot:openclaw-ws" for u in up["updates"]), "group message relayed to the other member bot")
            feed = (await http.get(f"/api/marketplace/{cs_id}/group", headers=O)).json()
            texts = " | ".join(x["text"] for x in feed["messages"])
            check(len(feed["members"]) == 2 and texts.count("🎓") == 2 and "👋" in texts and "🧠" in texts, "group feed shows welcome → inject → graduation for both bots")
            check((await http.get(f"/api/marketplace/{cs_id}/group", headers={"Authorization": f"Bearer {token}"})).status_code == 200, "member owner can read the group")

            step("Python SDK adapter (bridgebot_sdk.py --exec, Hermes-style SKILL.md)")
            r = await http.post("/api/bots", headers=H, json={"name": "hermes-cli", "framework": "hermes", "delivery": "poll"})
            sbot, stoken = r.json()["bot"]["id"], r.json()["token"]
            hermes_dir = tmp / "hermes_skills"
            sdk = subprocess.Popen([PY, str(ROOT / "connector" / "bridgebot_sdk.py"), "--server", base, "--token", stoken,
                                    "--exec", "echo [hermes] {text}", "--skills-dir", str(hermes_dir), "--skill-format", "skillmd"],
                                   stdout=open(tmp / "sdk.log", "w"), stderr=subprocess.STDOUT)
            procs.append(sdk)
            await http.post(f"/api/marketplace/{cs_id}/join", headers=H, json={"bot_id": sbot})
            await wait_member(H, sbot, "customer", "GRADUATED", timeout=40)
            md = hermes_dir / "master_customer_support_master" / "SKILL.md"
            check(md.is_file() and md.read_text().startswith("---\nname: master_customer_support_master"), "SDK installed the skill as Hermes/OpenClaw SKILL.md")
            print("  ✔ SDK bot (CLI adapter) auto-onboarded → GRADUATED")
            await http.post(f"/api/bots/{sbot}/messages", headers=H, json={"text": "ping from owner"})
            for _ in range(100):
                conv = (await http.get(f"/api/bots/{sbot}/messages", headers=H)).json()
                if any(m["direction"] == "out" and m["text"] == "[hermes] ping from owner" for m in conv):
                    break
                await asyncio.sleep(0.2)
            check(any(m["direction"] == "out" and m["text"] == "[hermes] ping from owner" for m in conv), "owner message → CLI bot → reply (no shell)")
            sdk.terminate()

            step("marketplace: connector bot → SKILL_INJECT over the bridge + real test task")
            await dash.close()  # the suspension above closed the old dashboard socket; open a fresh one
            dash = Dashboard(ws_base, token)
            await dash.start()
            await dash.wait_for(lambda e: e["type"] == "SNAPSHOT")
            r = await http.post("/api/bots", headers=H, json={"name": "local-langchain", "framework": "langchain", "delivery": "connector",
                                                               "connector_id": cid, "agent_name": "support-agent"})
            cbot = r.json()["bot"]["id"]
            r = await http.post("/api/marketplace/sales-closer-elite/make-master", headers=H, json={"bot_id": cbot})
            check(r.status_code == 200, "BUSINESS master unlocked on BUSINESS plan")
            m = await wait_member(H, cbot, "sales", "GRADUATED", timeout=30)
            f = skills_dir / "support-agent" / "master_sales_closer_elite.json"
            check(f.is_file() and json.loads(f.read_text())["persona"], f"skill file written by the connector: {f.name}")
            ev = await dash.wait_for(lambda e: e["type"] == "TASK_RESULT" and any("master_sales_closer_elite" in x for x in e.get("stdout_tail") or []))
            check(ev["exit_code"] == 0, "follow-up echo test task ran on the machine → GRADUATED")
            await dash.wait_for(lambda e: e["type"] == "ONBOARDING_STATUS" and e["bot_id"] == cbot and e["status"] == "GRADUATED")
            print("  ✔ dashboard got ONBOARDING_STATUS GRADUATED")

            step("admin: publish a Master Bot from a trained payload")
            check((await http.post("/api/admin/marketplace", headers=H, json={"name": "Hack"})).status_code == 403, "non-admin cannot publish")
            r = await http.post(f"/api/admin/marketplace/from-training/{trid}", headers=A, params={"min_plan_id": "PRO"})
            check(r.status_code == 201 and r.json()["is_published"], f"published '{r.json()['name']}'")
            new_id = r.json()["id"]
            check(any(x["id"] == new_id for x in (await http.get("/api/marketplace")).json()), "visible in public marketplace")
            r = await http.post(f"/api/marketplace/{new_id}/join", headers=O, json={"bot_id": pbot})
            check(r.status_code == 402, "FREE user gated from PRO master")
            await http.patch(f"/api/admin/marketplace/{new_id}", headers=A, json={"is_published": False})
            check((await http.get(f"/api/marketplace/{new_id}")).status_code == 404, "unpublished → hidden")
            adm = (await http.get("/api/admin/marketplace", headers=A)).json()
            check(any(x["id"] == new_id and not x["is_published"] for x in adm), "admin still sees the draft")

            step("stop connector → OFFLINE")
            await asyncio.sleep(3)
            for p in procs[1:]:
                p.terminate()
            await rx.wait(lambda h: h["json"].get("event") == "connector.offline")
            print("  ✔ connector.offline webhook")
            r = await http.post("/api/trigger-task", json={"connector_id": cid, "command": "echo hi"}, headers=H)
            check(r.status_code == 409, "task to offline connector → 409")
            r = await http.get(f"{T}/hello")
            check(r.status_code == 502 and "Tunnel offline" in r.text and "e2e-pc" in r.text, "tunnel with machine offline → 502 'Tunnel offline' page")
            await dash.close()

        print("\n✅ ALL E2E CHECKS PASSED")
        return 0
    except Exception:
        for name in ("backend.log", "connector.log", "sdk.log"):
            f = tmp / name
            if f.exists():
                print(f"\n----- {name} -----\n{f.read_text()[-5000:]}")
        raise
    finally:
        rx.server.shutdown()
        app.server.shutdown()
        for p in procs:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    p.kill()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
