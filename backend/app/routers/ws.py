"""
WebSocket endpoints.

/ws/connect    — local connector daemons (API-key auth).
/ws/dashboard  — PWA dashboards (JWT auth via ?token=), receive live events.

Connector -> cloud message types:
  HELLO {machine, agents:[{name,type}], version, platform}
  PING {ts}                       -> server replies PONG
  TASK_STARTED {task_id, argv}
  LOG {task_id, stream: stdout|stderr, line, ts}
  TASK_RESULT {task_id, exit_code, duration_ms, error?, stdout_tail?}
  SKILL_ACK {request_id, agent_name, skill_id, path, ok, error?}
Cloud -> connector: WELCOME, PONG, TASK, CANCEL, SKILL_INJECT
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.orm import attributes

from .. import botnet, webhooks
from ..audit import write_audit
from ..config import get_settings
from ..database import SessionLocal
from ..hub import ConnectorSession, hub, kick
from ..models import Agent, Connector, User
from ..security import decode_access_token, hash_api_key

router = APIRouter(tags=["websocket"])
log = logging.getLogger("bridgebot.ws")
settings = get_settings()

VALID_AGENT_TYPES = {"generic", "n8n", "hermes", "langchain", "ollama", "llamacpp", "custom"}
AUDITED_TYPES = {"HELLO", "TASK_STARTED", "TASK_RESULT", "SKILL_ACK", "ERROR"}


def _extract_api_key(ws: WebSocket) -> Optional[str]:
    """API key from `Authorization: Bearer`, `X-API-Key` header, or `?api_key=` (browsers/tests)."""
    auth = ws.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ws.headers.get("x-api-key") or ws.query_params.get("api_key")


async def _set_status(connector_id: uuid.UUID, status_value: str, heartbeat: bool = True) -> None:
    async with SessionLocal() as db:
        conn = await db.get(Connector, connector_id)
        if conn:
            conn.status = status_value
            if heartbeat:
                conn.last_heartbeat = datetime.now(timezone.utc)
            await db.commit()


async def _upsert_agents(connector_id: uuid.UUID, agents: Any) -> None:
    """Sync the agent list announced in HELLO."""
    if not isinstance(agents, list):
        return
    async with SessionLocal() as db:
        existing = {a.agent_name: a for a in await db.scalars(select(Agent).where(Agent.connector_id == connector_id))}
        for item in agents[:50]:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            name = str(item["name"])[:120]
            a_type = str(item.get("type", "generic")).lower()
            a_type = a_type if a_type in VALID_AGENT_TYPES else "custom"
            if name in existing:
                existing[name].agent_type = a_type
            else:
                db.add(Agent(connector_id=connector_id, agent_name=name, agent_type=a_type, current_injected_skills_json=[]))
        await db.commit()


async def _record_skill(connector_id: uuid.UUID, msg: Dict[str, Any]) -> None:
    """On a successful SKILL_ACK, persist the injected skill on the agent row."""
    if not msg.get("ok"):
        return
    name = str(msg.get("agent_name") or "default")[:120]
    async with SessionLocal() as db:
        agent = await db.scalar(select(Agent).where(Agent.connector_id == connector_id, Agent.agent_name == name))
        if agent is None:
            agent = Agent(connector_id=connector_id, agent_name=name, agent_type="generic", current_injected_skills_json=[])
            db.add(agent)
        skills = [s for s in (agent.current_injected_skills_json or []) if s.get("skill_id") != msg.get("skill_id")]
        skills.append({
            "skill_id": msg.get("skill_id"),
            "request_id": msg.get("request_id"),
            "path": msg.get("path"),
            "injected_at": datetime.now(timezone.utc).isoformat(),
        })
        agent.current_injected_skills_json = skills
        attributes.flag_modified(agent, "current_injected_skills_json")
        await db.commit()


async def _db_worker(jobs: "asyncio.Queue[Any]", connector_id: uuid.UUID) -> None:
    """Run one connection's database side effects sequentially; a failing job is logged, never fatal."""
    while True:
        job = await jobs.get()
        if job is None:
            return
        try:
            await job()
        except Exception as exc:  # noqa: BLE001
            log.warning("connector %s background db job failed: %s", connector_id, exc)


@router.websocket("/ws/connect")
async def connector_ws(ws: WebSocket) -> None:
    """Persistent tunnel for a local connector daemon."""
    raw_key = _extract_api_key(ws)
    conn = owner = None
    if raw_key:
        async with SessionLocal() as db:   # one round-trip: connector + owner
            row = (await db.execute(select(Connector, User).join(User, User.id == Connector.user_id)
                                    .where(Connector.api_key_hash == hash_api_key(raw_key)))).first()
            conn, owner = (row[0], row[1]) if row else (None, None)
    # Auth failures: accept, then close with an explicit 44xx code + reason so the connector can show a clear
    # error and back off for minutes (a pre-accept close only surfaces as a bare "HTTP 403").
    if conn is None or owner is None or owner.status != "ACTIVE":
        if not raw_key:
            code, reason = 4401, "Missing API key"
        elif conn is None or owner is None:
            code, reason = 4401, "Invalid API key - regenerate it in the dashboard"
        else:
            code, reason = 4403, f"Account {owner.status.lower()}"
        await ws.accept()
        await kick(ws, code, reason)
        return

    await ws.accept()
    connector_id, user_id = conn.id, conn.user_id
    session = ConnectorSession(connector_id=connector_id, user_id=user_id, websocket=ws)
    # Database side effects run on a per-connection background worker (in order), never on the receive path:
    # with a remote Postgres each round-trip costs 100s of ms and would otherwise delay live LOG streaming.
    jobs: "asyncio.Queue[Any]" = asyncio.Queue()
    worker = asyncio.create_task(_db_worker(jobs, connector_id))
    await ws.send_json({"type": "WELCOME", "connector_id": str(connector_id), "machine_name": conn.machine_name,
                        "heartbeat_timeout_s": settings.heartbeat_timeout_s, "acks": True})
    await hub.register_connector(session)
    await hub.broadcast_to_user(user_id, {"type": "CONNECTOR_STATUS", "connector_id": str(connector_id), "status": "ONLINE", "machine_name": conn.machine_name})
    jobs.put_nowait(lambda: _set_status(connector_id, "ONLINE"))
    client_ip = ws.client.host if ws.client else None
    jobs.put_nowait(lambda: write_audit(connector_id, "CONNECTED", {"client": client_ip}))
    jobs.put_nowait(lambda: webhooks.emit(user_id, "connector.online", {"connector_id": str(connector_id), "machine_name": conn.machine_name}))
    log.info("connector %s (%s) online", conn.machine_name, connector_id)
    last_hb_write = asyncio.get_running_loop().time()

    try:
        while True:
            try:
                text = await asyncio.wait_for(ws.receive_text(), timeout=settings.heartbeat_timeout_s)
            except asyncio.TimeoutError:
                log.warning("connector %s heartbeat timeout", connector_id)
                await kick(ws, 4008, "Heartbeat timeout")
                break
            try:
                msg: Dict[str, Any] = json.loads(text)
                if not isinstance(msg, dict):
                    raise ValueError("message must be an object")
            except ValueError as exc:
                await ws.send_json({"type": "ERROR", "error": f"bad message: {exc}"})
                continue

            mtype = str(msg.get("type", "")).upper()
            seq = msg.get("seq")
            if isinstance(seq, int) and not isinstance(seq, bool):
                st = hub.streams.get(connector_id)
                if st is not None:
                    if seq <= st[1]:
                        continue                      # duplicate re-sent after a reconnect
                    st[1] = seq
            if mtype == "PING":
                st = hub.streams.get(connector_id)
                await hub.send_to_connector(connector_id, {"type": "PONG", "ts": msg.get("ts"), "ack": st[1] if st else None})
                now = asyncio.get_running_loop().time()
                if now - last_hb_write >= settings.heartbeat_db_interval_s:   # throttle last_heartbeat writes
                    last_hb_write = now
                    jobs.put_nowait(lambda: _set_status(connector_id, "ONLINE"))
                continue
            if mtype.startswith("RD_"):          # remote desktop reply: only to the requesting viewer, never stored
                vid = str(msg.get("viewer") or "")
                if vid:
                    await hub.send_to_viewer(vid, user_id, {**msg, "type": mtype, "connector_id": str(connector_id)})
                continue
            if mtype == "TUNNEL_RESPONSE":
                hub.resolve_tunnel(connector_id, msg)   # never relayed/audited: bodies can be large
                continue
            # Relay to the owner's dashboards first (live logs must not wait for the database).
            await hub.broadcast_to_user(user_id, {**msg, "type": mtype, "connector_id": str(connector_id)})
            if mtype == "HELLO":
                session.features = {str(f) for f in (msg.get("features") or []) if isinstance(f, str)}
                stream = msg.get("stream")
                if isinstance(stream, str) and stream:
                    cur = hub.streams.get(connector_id)
                    if cur is None or cur[0] != stream:   # new connector process → new sequence space
                        hub.streams[connector_id] = [stream, 0]
                agents = msg.get("agents")

                async def _hello(agents: Any = agents) -> None:
                    await _upsert_agents(connector_id, agents)
                    botnet.spawn(botnet.resume_for_connector(connector_id))
                jobs.put_nowait(_hello)
            elif mtype == "SKILL_ACK":
                async def _ack(m: Dict[str, Any] = msg) -> None:
                    await _record_skill(connector_id, m)
                    if m.get("ok"):
                        await webhooks.emit(user_id, "skill.injected", {
                            "connector_id": str(connector_id), "machine_name": conn.machine_name, "agent_name": m.get("agent_name"),
                            "skill_id": m.get("skill_id"), "training_request_id": m.get("request_id"), "path": m.get("path")})
                    botnet.spawn(botnet.on_connector_skill_ack(m))
                jobs.put_nowait(_ack)
            elif mtype == "TASK_RESULT":
                if isinstance(seq, int):
                    await hub.send_to_connector(connector_id, {"type": "ACK", "ack": seq})
                async def _result(m: Dict[str, Any] = msg) -> None:
                    await webhooks.emit(user_id, "task.completed", {
                        "connector_id": str(connector_id), "machine_name": conn.machine_name, "task_id": m.get("task_id"),
                        "exit_code": m.get("exit_code"), "duration_ms": m.get("duration_ms"), "error": m.get("error"),
                        "stdout_tail": m.get("stdout_tail", [])})
                    botnet.spawn(botnet.on_connector_task_result(m))
                jobs.put_nowait(_result)
            if mtype in AUDITED_TYPES:
                jobs.put_nowait(lambda m=msg, t=mtype: write_audit(connector_id, t, m))
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.exception("connector %s socket error: %s", connector_id, exc)
    finally:
        hub.fail_tunnels(connector_id)
        jobs.put_nowait(None)          # drain pending DB work (in order) before writing OFFLINE
        try:
            await asyncio.wait_for(asyncio.shield(worker), timeout=30)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            pass
        if await hub.unregister_connector(connector_id, ws):
            await hub.broadcast_to_user(user_id, {"type": "CONNECTOR_STATUS", "connector_id": str(connector_id), "status": "OFFLINE"})
            if not hub.is_online(connector_id):          # a newer session may have connected meanwhile
                await _set_status(connector_id, "OFFLINE", heartbeat=False)
            await write_audit(connector_id, "DISCONNECTED", {})
            await webhooks.emit(user_id, "connector.offline", {"connector_id": str(connector_id), "machine_name": conn.machine_name})
            log.info("connector %s offline", connector_id)


@router.websocket("/ws/dashboard")
async def dashboard_ws(ws: WebSocket) -> None:
    """Live event stream for the PWA. Auth: ?token=<JWT>."""
    user_id = decode_access_token(ws.query_params.get("token", ""))
    if user_id is None:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid token")
        return
    async with SessionLocal() as db:
        user = await db.get(User, user_id)
        conns = list(await db.scalars(select(Connector).where(Connector.user_id == user_id))) if user else []
    if user is None or user.status != "ACTIVE":
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="Unknown or inactive user")
        return

    await ws.accept()
    await hub.add_dashboard(user_id, ws)
    viewer_id = uuid.uuid4().hex
    hub.add_viewer(viewer_id, user_id, ws)
    owned = {str(c.id) for c in conns}
    await ws.send_json({
        "type": "SNAPSHOT",
        "connectors": [{"connector_id": str(c.id), "machine_name": c.machine_name, "status": "ONLINE" if hub.is_online(c.id) else "OFFLINE"} for c in conns],
    })
    try:
        while True:
            text = await ws.receive_text()
            if text.strip().upper() in ("PING", '{"TYPE":"PING"}'):
                await ws.send_json({"type": "PONG"})
                continue
            try:
                msg = json.loads(text)
            except ValueError:
                continue
            if not isinstance(msg, dict) or not str(msg.get("type", "")).upper().startswith("RD_"):
                continue
            cid = str(msg.get("connector_id") or "")
            if cid not in owned:            # machine added after this socket opened? re-check ownership once
                try:
                    async with SessionLocal() as db:
                        c = await db.get(Connector, uuid.UUID(cid))
                    if c is not None and c.user_id == user_id:
                        owned.add(cid)
                except (ValueError, Exception):  # noqa: BLE001
                    pass
            if cid not in owned:
                await ws.send_json({"type": "RD_ERROR", "connector_id": cid, "error": "Not your machine"})
                continue
            out = {k: v for k, v in msg.items() if k != "connector_id"}
            out["type"] = str(msg["type"]).upper()
            out["viewer"] = viewer_id
            if not await hub.send_to_connector(uuid.UUID(cid), out):
                await ws.send_json({"type": "RD_ERROR", "connector_id": cid, "error": "Machine is offline"})
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.debug("dashboard socket closed: %s", exc)
    finally:
        hub.remove_viewer(viewer_id)
        for cid in owned:
            await hub.send_to_connector(uuid.UUID(cid), {"type": "RD_STOP", "viewer": viewer_id})
        await hub.remove_dashboard(user_id, ws)
