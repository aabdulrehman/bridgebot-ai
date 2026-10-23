"""Simulate an Android phone connector + a dashboard viewer; assert owner-only RD_* relay."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import uuid
from pathlib import Path

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

BASE = os.environ.get("BRIDGEBOT_TEST_URL", "http://127.0.0.1:8765")
WS = BASE.replace("http://", "ws://").replace("https://", "wss://")


async def main() -> None:
    email = f"mobile-{uuid.uuid4().hex[:8]}@example.com"
    password = "TestPass123!"
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as c:
        r = await c.post("/api/auth/register", json={"email": email, "password": password})
        assert r.status_code in (200, 201), r.text
        token = (await c.post("/api/auth/login", json={"email": email, "password": password})).json()["access_token"]
        h = {"Authorization": f"Bearer {token}"}

        pc = (await c.post("/api/mobile/pair-code", headers=h, json={"device_name": "Pixel Test"})).json()
        assert len(pc["code"]) == 6, pc
        paired = (await c.post("/api/mobile/pair", json={"code": pc["code"]})).json()
        api_key = paired["api_key"]
        assert api_key.startswith("bbk_"), api_key
        bad = await c.post("/api/mobile/pair", json={"code": pc["code"]})
        assert bad.status_code == 404

        email2 = f"other-{uuid.uuid4().hex[:8]}@example.com"
        await c.post("/api/auth/register", json={"email": email2, "password": password})
        token2 = (await c.post("/api/auth/login", json={"email": email2, "password": password})).json()["access_token"]

    fake_jpg = base64.b64encode(b"\xff\xd8\xff\xd9fake").decode()

    async with websockets.connect(f"{WS}/ws/connect?api_key={api_key}", max_size=8_000_000) as phone:
        welcome = json.loads(await asyncio.wait_for(phone.recv(), timeout=5))
        assert welcome["type"] == "WELCOME", welcome
        await phone.send(json.dumps({
            "type": "HELLO", "machine": "Pixel Test", "version": "android-1.0.0",
            "platform": "Android 14", "agents": [], "features": ["android", "remote_desktop"],
            "stream": "teststream",
        }))

        async def phone_loop():
            async for raw in phone:
                m = json.loads(raw)
                t = str(m.get("type", "")).upper()
                if t == "RD_START":
                    await phone.send(json.dumps({
                        "type": "RD_FRAME", "viewer": m["viewer"], "jpg": fake_jpg, "sw": 1080, "sh": 1920,
                    }))
                elif t == "RD_INPUT":
                    await phone.send(json.dumps({
                        "type": "RD_OK", "viewer": m["viewer"], "what": "input", "detail": m.get("action"),
                    }))

        loop_task = asyncio.create_task(phone_loop())
        await asyncio.sleep(0.3)  # let HELLO land

        async with websockets.connect(f"{WS}/ws/dashboard?token={token}", max_size=8_000_000) as dash:
            snap = json.loads(await asyncio.wait_for(dash.recv(), timeout=5))
            assert snap["type"] == "SNAPSHOT", snap
            cid = next(x["connector_id"] for x in snap["connectors"] if x["machine_name"] == "Pixel Test")
            await dash.send(json.dumps({"type": "RD_START", "connector_id": cid, "quality": 40, "width": 540, "fps": 4}))
            frame = None
            for _ in range(20):
                m = json.loads(await asyncio.wait_for(dash.recv(), timeout=5))
                if m.get("type") == "RD_FRAME":
                    frame = m
                    break
            assert frame and frame.get("jpg") == fake_jpg and frame.get("sw") == 1080, frame
            await dash.send(json.dumps({"type": "RD_INPUT", "connector_id": cid, "action": "tap", "x": 0.5, "y": 0.5}))
            ok = None
            for _ in range(20):
                m = json.loads(await asyncio.wait_for(dash.recv(), timeout=5))
                if m.get("type") == "RD_OK":
                    ok = m
                    break
            assert ok and ok.get("detail") == "tap", ok

        async with websockets.connect(f"{WS}/ws/dashboard?token={token2}", max_size=8_000_000) as dash2:
            await dash2.recv()
            await dash2.send(json.dumps({"type": "RD_START", "connector_id": cid, "quality": 40, "width": 540, "fps": 4}))
            refused = json.loads(await asyncio.wait_for(dash2.recv(), timeout=5))
            assert refused.get("type") == "RD_ERROR" and "Not your machine" in refused.get("error", ""), refused

        loop_task.cancel()
        async with httpx.AsyncClient(base_url=BASE, timeout=30) as c:
            rows = (await c.get("/api/connectors", headers={"Authorization": f"Bearer {token}"})).json()
            phone_row = next(r for r in rows if r["machine_name"] == "Pixel Test")
            assert "android" in phone_row.get("features", []), phone_row

    async with httpx.AsyncClient(base_url=BASE, timeout=30, follow_redirects=False) as c:
        r = await c.get("/downloads/live-mobilelty.apk")
        assert r.status_code in (302, 307), r.status_code
        assert "live-mobilelty.apk" in r.headers.get("location", ""), r.headers.get("location")

    print("PASS: Live Mobilelty relay + pairing + owner-only + APK redirect")


if __name__ == "__main__":
    asyncio.run(main())
