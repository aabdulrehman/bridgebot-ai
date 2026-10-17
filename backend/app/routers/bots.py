"""
Universal Bot Bridge.

Owner side (JWT or bbu_ API key)          Bot side (bot token bbt_…)
  POST   /api/bots            register     GET  /api/bot/me
  GET    /api/bots            list         GET  /api/bot/updates?offset=&timeout=   (long-poll)
  GET    /api/bots/{id}       detail       POST /api/bot/messages {text, reply_to?, chat?, group_id?}
  PATCH  /api/bots/{id}       update       POST /api/bot/ack {update_id, ok, error?}
  DELETE /api/bots/{id}       remove       GET  /api/bot/skills
  POST   /api/bots/{id}/rotate-token       WS   /ws/bot?token=bbt_…
  POST   /api/bots/{id}/messages  (owner -> bot)
  GET    /api/bots/{id}/messages  (conversation)

Bot auth: `Authorization: Bot bbt_…` (or `Bearer bbt_…`, or `X-Bot-Token`).
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import botnet, webhooks
from ..billing.service import enforce_bot_limit
from ..database import SessionLocal, get_db
from ..deps import get_current_user
from ..hub import hub
from ..models import Bot, BotMessage, Connector, GroupMember, MarketplaceMaster, User
from ..security import BOT_TOKEN_PREFIX, generate_bot_token, generate_webhook_secret, hash_api_key

router = APIRouter(tags=["bots"])

FRAMEWORKS = ["hermes", "openclaw", "n8n", "langchain", "http", "grok", "cli", "custom"]


# --------------------------------------------------------------------------- schemas
class BotCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    framework: str = "custom"
    delivery: Literal["poll", "webhook", "websocket", "connector"] = "poll"
    webhook_url: Optional[str] = Field(default=None, max_length=1000)
    connector_id: Optional[uuid.UUID] = None
    agent_name: str = Field(default="default", max_length=120)


class BotPatchIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    delivery: Optional[Literal["poll", "webhook", "websocket", "connector"]] = None
    webhook_url: Optional[str] = Field(default=None, max_length=1000)
    status: Optional[Literal["ACTIVE", "DISABLED"]] = None
    agent_name: Optional[str] = Field(default=None, max_length=120)


class OwnerMessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    payload: Optional[Dict[str, Any]] = None


class BotPostIn(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    reply_to: Optional[int] = None
    chat: Optional[Literal["direct", "group"]] = None
    group_id: Optional[uuid.UUID] = None
    payload: Optional[Dict[str, Any]] = None


class AckIn(BaseModel):
    update_id: int
    ok: bool = True
    error: Optional[str] = None


def bot_out(b: Bot) -> Dict[str, Any]:
    return {
        "id": str(b.id), "name": b.name, "framework": b.framework, "delivery": b.delivery, "webhook_url": b.webhook_url,
        "connector_id": str(b.connector_id) if b.connector_id else None, "agent_name": b.agent_name,
        "token_prefix": b.token_prefix, "status": b.status, "persona": b.persona, "skills": b.skills_json or [],
        "online": hub.bot_online(b.id) or (b.delivery == "connector" and b.connector_id is not None and hub.is_online(b.connector_id)),
        "last_seen_at": b.last_seen_at.isoformat() if b.last_seen_at else None, "created_at": b.created_at.isoformat(),
    }


async def _owned(db: AsyncSession, user: User, bot_id: uuid.UUID) -> Bot:
    b = await db.get(Bot, bot_id)
    if b is None or b.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bot not found")
    return b


def _check_delivery(delivery: str, url: Optional[str]) -> None:
    if delivery == "webhook":
        if not url:
            raise HTTPException(422, "webhook_url is required for delivery=webhook")
        try:
            webhooks.validate_webhook_url(url)
        except ValueError as exc:
            raise HTTPException(422, str(exc))


# --------------------------------------------------------------------------- owner endpoints
@router.post("/api/bots", status_code=201)
async def create_bot(body: BotCreateIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """Link a bot. Returns the bot token + webhook signing secret ONCE."""
    await enforce_bot_limit(db, user)
    framework = body.framework.lower() if body.framework.lower() in FRAMEWORKS else "custom"
    _check_delivery(body.delivery, body.webhook_url)
    if body.delivery == "connector":
        if body.connector_id is None:
            raise HTTPException(422, "connector_id is required for delivery=connector")
        conn = await db.get(Connector, body.connector_id)
        if conn is None or conn.user_id != user.id:
            raise HTTPException(404, "Connector not found")
    token = generate_bot_token()
    b = Bot(user_id=user.id, name=body.name.strip(), framework=framework, delivery=body.delivery, webhook_url=body.webhook_url,
            connector_id=body.connector_id if body.delivery == "connector" else None, agent_name=body.agent_name or "default",
            token_hash=hash_api_key(token), token_prefix=token[:12], secret=generate_webhook_secret(), skills_json=[])
    db.add(b)
    await db.commit()
    await db.refresh(b)
    return {"bot": bot_out(b), "token": token, "secret": b.secret}


@router.get("/api/bots")
async def list_bots(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> List[Dict[str, Any]]:
    rows = await db.scalars(select(Bot).where(Bot.user_id == user.id).order_by(Bot.created_at))
    out = []
    for b in rows:
        d = bot_out(b)
        mems = list(await db.scalars(select(GroupMember).where(GroupMember.bot_id == b.id)))
        d["groups"] = []
        for m in mems:
            master = await db.get(MarketplaceMaster, m.master_id)
            d["groups"].append({"master_id": str(m.master_id), "master": master.name if master else "?", "status": m.status,
                                "master_clone": m.is_master_clone, "error": m.last_error})
        out.append(d)
    return out


@router.get("/api/bots/{bot_id}")
async def get_bot(bot_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    return bot_out(await _owned(db, user, bot_id))


@router.patch("/api/bots/{bot_id}")
async def patch_bot(bot_id: uuid.UUID, body: BotPatchIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    b = await _owned(db, user, bot_id)
    data = body.model_dump(exclude_unset=True)
    _check_delivery(data.get("delivery", b.delivery), data.get("webhook_url", b.webhook_url))
    for k, v in data.items():
        setattr(b, k, v)
    await db.commit()
    return bot_out(b)


@router.delete("/api/bots/{bot_id}", status_code=204)
async def delete_bot(bot_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> None:
    b = await _owned(db, user, bot_id)
    ws = hub.bots.get(b.id)
    if ws is not None:
        try:
            await ws.close(code=4001, reason="Bot deleted")
        except Exception:  # noqa: BLE001
            pass
    await db.delete(b)
    await db.commit()


@router.post("/api/bots/{bot_id}/rotate-token")
async def rotate_token(bot_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    b = await _owned(db, user, bot_id)
    token = generate_bot_token()
    b.token_hash, b.token_prefix = hash_api_key(token), token[:12]
    await db.commit()
    return {"bot": bot_out(b), "token": token}


@router.post("/api/bots/{bot_id}/messages", status_code=201)
async def owner_message(bot_id: uuid.UUID, body: OwnerMessageIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """Send a message from the owner to the bot (delivered via WS / webhook / polling)."""
    b = await _owned(db, user, bot_id)
    if b.delivery == "connector":
        raise HTTPException(409, "Connector bots take tasks — use POST /api/trigger-task")
    m = await botnet.post_to_bot(db, b, body.text, kind="message", sender="owner", payload=body.payload)
    return botnet.serialize(m)


@router.get("/api/bots/{bot_id}/messages")
async def conversation(bot_id: uuid.UUID, limit: int = Query(100, ge=1, le=500), user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_db)) -> List[Dict[str, Any]]:
    b = await _owned(db, user, bot_id)
    rows = list(await db.scalars(select(BotMessage).where(BotMessage.bot_id == b.id).order_by(BotMessage.id.desc()).limit(limit)))
    return [botnet.serialize(m) for m in reversed(rows)]


# --------------------------------------------------------------------------- bot side
def _raw_bot_token(authorization: Optional[str], x_bot_token: Optional[str], query: Optional[str] = None) -> Optional[str]:
    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() in ("bot", "bearer"):
            return parts[1].strip()
    return x_bot_token or query


async def _bot_from_token(db: AsyncSession, raw: Optional[str]) -> Optional[Bot]:
    if not raw or not raw.startswith(BOT_TOKEN_PREFIX):
        return None
    b = await db.scalar(select(Bot).where(Bot.token_hash == hash_api_key(raw)))
    if b is None or b.status != "ACTIVE":
        return None
    owner = await db.get(User, b.user_id)
    if owner is None or owner.status != "ACTIVE":
        return None
    return b


async def current_bot(authorization: Optional[str] = Header(default=None), x_bot_token: Optional[str] = Header(default=None),
                      db: AsyncSession = Depends(get_db)) -> Bot:
    b = await _bot_from_token(db, _raw_bot_token(authorization, x_bot_token))
    if b is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or disabled bot token")
    b.last_seen_at = datetime.now(timezone.utc)
    return b


@router.get("/api/bot/me")
async def bot_me(bot: Bot = Depends(current_bot), db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    await db.commit()
    d = bot_out(bot)
    d["groups"] = [str(m.master_id) for m in await db.scalars(select(GroupMember).where(GroupMember.bot_id == bot.id))]
    return d


@router.get("/api/bot/updates")
async def bot_updates(offset: int = Query(0, ge=0), timeout: int = Query(0, ge=0, le=50), limit: int = Query(100, ge=1, le=200),
                      bot: Bot = Depends(current_bot), db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """Telegram-style long polling: items with update_id > offset. Pass the last update_id you processed."""
    bot_id = bot.id
    await db.commit()

    async def fetch() -> List[BotMessage]:
        async with SessionLocal() as s:
            rows = list(await s.scalars(select(BotMessage).where(BotMessage.bot_id == bot_id, BotMessage.direction == "in",
                                                                 BotMessage.id > offset).order_by(BotMessage.id).limit(limit)))
            for m in rows:
                if m.delivered_at is None:
                    m.delivered_at = datetime.now(timezone.utc)
            await s.commit()
            return rows

    rows = await fetch()
    if not rows and timeout:
        ev = hub.bot_events[bot_id]
        ev.clear()
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        rows = await fetch()
    return {"ok": True, "updates": [botnet.serialize(m) for m in rows],
            "next_offset": rows[-1].id if rows else offset}


@router.post("/api/bot/messages", status_code=201)
async def bot_post(body: BotPostIn, bot: Bot = Depends(current_bot), db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """Reply to an update (`reply_to`), message your owner (chat=direct) or post in a master group (chat=group, group_id)."""
    if body.chat == "group":
        if body.group_id is None:
            raise HTTPException(422, "group_id is required for chat=group")
        if await db.scalar(select(GroupMember).where(GroupMember.bot_id == bot.id, GroupMember.master_id == body.group_id)) is None:
            raise HTTPException(403, "Bot is not a member of that group")
    m = await botnet.handle_bot_post(db, bot, body.text, reply_to=body.reply_to, chat=body.chat, group_id=body.group_id, payload=body.payload)
    return botnet.serialize(m)


@router.post("/api/bot/ack")
async def bot_ack(body: AckIn, bot: Bot = Depends(current_bot), db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """Acknowledge an update. For kind=skill_inject this confirms the skill was installed (ok=false = rejected)."""
    bot_id = bot.id
    await db.commit()
    await botnet.handle_ack(bot_id, body.update_id, body.ok, body.error)
    return {"ok": True}


@router.get("/api/bot/skills")
async def bot_skills(bot: Bot = Depends(current_bot), db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """Full skill payloads of every master this bot has been trained by (re-sync after restarts)."""
    out = []
    for mem in await db.scalars(select(GroupMember).where(GroupMember.bot_id == bot.id)):
        master = await db.get(MarketplaceMaster, mem.master_id)
        if master and mem.status in ("INJECTED", "TESTING", "GRADUATED"):
            out.append({"status": mem.status, "master_clone": mem.is_master_clone, "skill": botnet.master_skill_payload(master)})
    await db.commit()
    return {"persona": bot.persona, "skills": out}


@router.websocket("/ws/bot")
async def bot_ws(ws: WebSocket) -> None:
    """
    Realtime Bot API. Auth: ?token=bbt_… or `Authorization: Bot bbt_…`.
    Server -> bot: READY, UPDATE {update_id, kind, text, payload…}, PONG, OK, ERROR
    Bot -> server: MESSAGE {text, reply_to?, chat?, group_id?}, ACK {update_id, ok?, error?}, PING
    """
    raw = _raw_bot_token(ws.headers.get("authorization"), ws.headers.get("x-bot-token"), ws.query_params.get("token"))
    async with SessionLocal() as db:
        bot = await _bot_from_token(db, raw)
        if bot is not None:
            bot.last_seen_at = datetime.now(timezone.utc)
            await db.commit()
            bot_id, name = bot.id, bot.name
    if bot is None:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid bot token")
        return
    await ws.accept()
    await hub.register_bot(bot_id, ws)
    await ws.send_json({"type": "READY", "bot_id": str(bot_id), "name": name})
    await botnet.flush_pending(bot_id)
    try:
        while True:
            try:
                msg = json.loads(await ws.receive_text())
                if not isinstance(msg, dict):
                    raise ValueError
            except ValueError:
                await ws.send_json({"type": "ERROR", "error": "messages must be JSON objects"})
                continue
            mtype = str(msg.get("type", "")).upper()
            if mtype == "PING":
                await ws.send_json({"type": "PONG", "ts": msg.get("ts")})
            elif mtype == "ACK":
                try:
                    await botnet.handle_ack(bot_id, int(msg.get("update_id")), bool(msg.get("ok", True)), msg.get("error"))
                    await ws.send_json({"type": "OK", "ack": msg.get("update_id")})
                except (TypeError, ValueError):
                    await ws.send_json({"type": "ERROR", "error": "ACK needs update_id"})
            elif mtype == "MESSAGE":
                try:
                    body = BotPostIn(**{k: v for k, v in msg.items() if k != "type"})
                except Exception as exc:  # noqa: BLE001
                    await ws.send_json({"type": "ERROR", "error": f"bad MESSAGE: {exc}"})
                    continue
                async with SessionLocal() as db:
                    b = await db.get(Bot, bot_id)
                    if body.chat == "group" and (body.group_id is None or await db.scalar(
                            select(GroupMember).where(GroupMember.bot_id == bot_id, GroupMember.master_id == body.group_id)) is None):
                        await ws.send_json({"type": "ERROR", "error": "not a member of that group"})
                        continue
                    out = await botnet.handle_bot_post(db, b, body.text, reply_to=body.reply_to, chat=body.chat, group_id=body.group_id, payload=body.payload)
                    await ws.send_json({"type": "OK", "message_id": out.id})
            else:
                await ws.send_json({"type": "ERROR", "error": f"unknown type {mtype}"})
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        pass
    finally:
        hub.unregister_bot(bot_id, ws)
