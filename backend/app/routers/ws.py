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
from ..hub import ConnectorSession, hub
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


@router.websocket("/ws/connect")
async def connector_ws(ws: WebSocket) -> None:
    """Persistent tunnel for a local connector daemon."""
    raw_key = _extract_api_key(ws)
    if not raw_key:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="Missing API key")
        return
    async with SessionLocal() as db:
        conn = await db.scalar(select(Connector).where(Connector.api_key_hash == hash_api_key(raw_key)))
        owner = await db.get(User, conn.user_id) if conn else None
    if conn is None or owner is None:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid API key")
        return
    if owner.status != "ACTIVE":
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason=f"Account {owner.status.lower()}")
        return

    await ws.accept()
    connector_id, user_id = conn.id, conn.user_id
    session = ConnectorSession(connector_id=connector_id, user_id=user_id, websocket=ws)
    await hub.register_connector(session)
    await _set_status(connector_id, "ONLINE")
    await hub.broadcast_to_user(user_id, {"type": "CONNECTOR_STATUS", "connector_id": str(connector_id), "status": "ONLINE", "machine_name": conn.machine_name})
    await write_audit(connector_id, "CONNECTED", {"client": ws.client.host if ws.client else None})
    await webhooks.emit(user_id, "connector.online", {"connector_id": str(connector_id), "machine_name": conn.machine_name})
    await ws.send_json({"type": "WELCOME", "connector_id": str(connector_id), "machine_name": conn.machine_name, "heartbeat_timeout_s": settings.heartbeat_timeout_s})
    log.info("connector %s (%s) online", conn.machine_name, connector_id)

    try:
        while True:
            try:
                text = await asyncio.wait_for(ws.receive_text(), timeout=settings.heartbeat_timeout_s)
            except asyncio.TimeoutError:
                log.warning("connector %s heartbeat timeout", connector_id)
                await ws.close(code=4008, reason="Heartbeat timeout")
                break
            try:
                msg: Dict[str, Any] = json.loads(text)
                if not isinstance(msg, dict):
                    raise ValueError("message must be an object")
            except ValueError as exc:
                await ws.send_json({"type": "ERROR", "error": f"bad message: {exc}"})
                continue

            mtype = str(msg.get("type", "")).upper()
            if mtype == "PING":
                await hub.send_to_connector(connector_id, {"type": "PONG", "ts": msg.get("ts")})
                await _set_status(connector_id, "ONLINE")
                continue
            if mtype == "TUNNEL_RESPONSE":
                hub.resolve_tunnel(connector_id, msg)   # never relayed/audited: bodies can be large
                continue
            if mtype == "HELLO":
                session.features = {str(f) for f in (msg.get("features") or []) if isinstance(f, str)}
                await _upsert_agents(connector_id, msg.get("agents"))
                botnet.spawn(botnet.resume_for_connector(connector_id))
            elif mtype == "SKILL_ACK":
                await _record_skill(connector_id, msg)
                if msg.get("ok"):
                    await webhooks.emit(user_id, "skill.injected", {
                        "connector_id": str(connector_id), "machine_name": conn.machine_name, "agent_name": msg.get("agent_name"),
                        "skill_id": msg.get("skill_id"), "training_request_id": msg.get("request_id"), "path": msg.get("path")})
                botnet.spawn(botnet.on_connector_skill_ack(msg))
            elif mtype == "TASK_RESULT":
                await webhooks.emit(user_id, "task.completed", {
                    "connector_id": str(connector_id), "machine_name": conn.machine_name, "task_id": msg.get("task_id"),
                    "exit_code": msg.get("exit_code"), "duration_ms": msg.get("duration_ms"), "error": msg.get("error"),
                    "stdout_tail": msg.get("stdout_tail", [])})
                botnet.spawn(botnet.on_connector_task_result(msg))

            # Relay everything else to the owner's dashboards.
            await hub.broadcast_to_user(user_id, {**msg, "type": mtype, "connector_id": str(connector_id)})
            if mtype in AUDITED_TYPES:
                await write_audit(connector_id, mtype, msg)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.exception("connector %s socket error: %s", connector_id, exc)
    finally:
        hub.fail_tunnels(connector_id)
        if await hub.unregister_connector(connector_id, ws):
            await _set_status(connector_id, "OFFLINE", heartbeat=False)
            await hub.broadcast_to_user(user_id, {"type": "CONNECTOR_STATUS", "connector_id": str(connector_id), "status": "OFFLINE"})
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
    await ws.send_json({
        "type": "SNAPSHOT",
        "connectors": [{"connector_id": str(c.id), "machine_name": c.machine_name, "status": "ONLINE" if hub.is_online(c.id) else "OFFLINE"} for c in conns],
    })
    try:
        while True:
            text = await ws.receive_text()
            if text.strip().upper() in ("PING", '{"TYPE":"PING"}'):
                await ws.send_json({"type": "PONG"})
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.debug("dashboard socket closed: %s", exc)
    finally:
        await hub.remove_dashboard(user_id, ws)
