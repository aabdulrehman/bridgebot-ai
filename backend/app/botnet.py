"""
Universal Bot Bridge + Master Bot Marketplace automation.

* Every linked bot has an inbox (`bot_messages`, direction='in'). New items are
  delivered instantly over /ws/bot when the bot is connected, pushed to its
  webhook (HMAC-signed, retried) when delivery='webhook', and always remain
  available to long-polling via GET /api/bot/updates.
* Connector bots (delivery='connector') receive skills as SKILL_INJECT over the
  existing local-connector tunnel and are tested with a real TASK.
* Joining a Master Bot's messenger group starts the automated onboarding:
      welcome  ->  skill injection  ->  follow-up test  ->  GRADUATED
  with no manual steps. The master also answers questions posted in its group.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import httpx
from sqlalchemy import select

from . import webhooks
from .config import get_settings
from .database import SessionLocal
from .hub import hub
from .models import Bot, BotMessage, Connector, GroupMember, GroupMessage, MarketplaceMaster, User

log = logging.getLogger("bridgebot.botnet")
settings = get_settings()
_tasks: Set[asyncio.Task] = set()


def spawn(coro) -> asyncio.Task:  # type: ignore[no-untyped-def]
    """Fire-and-forget background task that is not garbage-collected early."""
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return t


async def drain(timeout: float = 10.0) -> None:
    """Wait for in-flight background work (used by tests / shutdown)."""
    if _tasks:
        await asyncio.wait(list(_tasks), timeout=timeout)


def now() -> datetime:
    return datetime.now(timezone.utc)


def serialize(m: BotMessage) -> Dict[str, Any]:
    return {
        "update_id": m.id, "kind": m.kind, "chat": m.chat, "group_id": str(m.group_id) if m.group_id else None,
        "sender": m.sender, "text": m.text, "payload": m.payload_json, "reply_to": m.reply_to,
        "direction": m.direction, "created_at": m.created_at.isoformat() if m.created_at else None,
        "acked": m.acked_at is not None,
    }


def master_skill_payload(master: MarketplaceMaster) -> Dict[str, Any]:
    """The payload that is cloned into a subscriber's bot."""
    base = dict(master.skill_payload_json or {})
    tools = list(base.get("tools") or [])
    names = {t.get("name") for t in tools if isinstance(t, dict)}
    tools += [t for t in (master.tools_json or []) if isinstance(t, dict) and t.get("name") not in names]
    base.update({
        "skill_id": base.get("skill_id") or f"master_{re.sub(r'[^a-z0-9_]+', '_', master.slug.lower())}",
        "name": base.get("name") or master.name,
        "persona": master.persona,
        "tools": tools,
        "master": {"id": str(master.id), "slug": master.slug, "name": master.name},
        "source": "bridgebot-marketplace",
    })
    return base


# --------------------------------------------------------------------------- delivery
async def _push_webhook(bot_id: uuid.UUID, msg_id: int) -> None:
    """POST an update to the bot's webhook; a JSON {"text": ...} response counts as the bot's reply."""
    delays = [0, 1, 4]
    for i, delay in enumerate(delays):
        if delay:
            await asyncio.sleep(delay * max(0.2, min(1.0, settings.webhook_retry_base_s / 30)))
        async with SessionLocal() as db:
            bot = await db.get(Bot, bot_id)
            m = await db.get(BotMessage, msg_id)
            if bot is None or m is None or not bot.webhook_url or bot.status != "ACTIVE":
                return
            body = json.dumps({"event": "bot.update", "bot_id": str(bot.id), "update": serialize(m)},
                              default=str, separators=(",", ":")).encode()
            headers = {"Content-Type": "application/json", "User-Agent": "BridgeBot-BotAPI/1.0",
                       "X-BridgeBot-Event": "bot.update", "X-BridgeBot-Bot": str(bot.id),
                       "X-BridgeBot-Signature": webhooks.sign(bot.secret, body)}
            try:
                webhooks.validate_webhook_url(bot.webhook_url)
                async with httpx.AsyncClient(timeout=settings.webhook_timeout_s, follow_redirects=False) as client:
                    resp = await client.post(bot.webhook_url, content=body, headers=headers)
            except Exception as exc:  # noqa: BLE001
                log.warning("bot %s webhook push failed (attempt %d): %s", bot.id, i + 1, exc)
                continue
            if not 200 <= resp.status_code < 300:
                log.warning("bot %s webhook HTTP %s (attempt %d)", bot.id, resp.status_code, i + 1)
                continue
            m.delivered_at = now()
            bot.last_seen_at = now()
            reply: Optional[str] = None
            try:
                data = resp.json()
                if isinstance(data, dict):
                    reply = data.get("text") or data.get("reply") or data.get("output")
            except ValueError:
                pass
            await db.commit()
        if m.kind == "skill_inject":
            await handle_ack(bot_id, msg_id, True, None)
        if reply and m.kind != "skill_inject":
            async with SessionLocal() as db:
                bot = await db.get(Bot, bot_id)
                await handle_bot_post(db, bot, str(reply), reply_to=msg_id, chat=None, group_id=None)
        return


async def deliver(bot_id: uuid.UUID, msg_id: int) -> None:
    """Route a fresh inbox item: WebSocket now, else webhook push; always wake long-pollers."""
    hub.wake_bot(bot_id)
    async with SessionLocal() as db:
        bot = await db.get(Bot, bot_id)
        m = await db.get(BotMessage, msg_id)
        if bot is None or m is None:
            return
        if hub.bot_online(bot_id):
            if await hub.send_to_bot(bot_id, {"type": "UPDATE", **serialize(m)}):
                m.delivered_at = now()
                await db.commit()
                return
        delivery, has_url = bot.delivery, bool(bot.webhook_url)
    if delivery == "webhook" and has_url:
        spawn(_push_webhook(bot_id, msg_id))


async def post_to_bot(db, bot: Bot, text: str, *, kind: str = "message", sender: str = "owner", chat: str = "direct",  # type: ignore[no-untyped-def]
                      group_id: Optional[uuid.UUID] = None, payload: Any = None, reply_to: Optional[int] = None,
                      defer: bool = False) -> BotMessage:
    """Queue an inbox item for the bot. defer=True: caller commits its own state first, then calls `release()`."""
    m = BotMessage(bot_id=bot.id, direction="in", kind=kind, chat=chat, group_id=group_id, sender=sender,
                   text=text, payload_json=payload, reply_to=reply_to)
    db.add(m)
    await db.commit()
    await db.refresh(m)
    await hub.broadcast_to_user(bot.user_id, {"type": "BOT_MESSAGE", "bot_id": str(bot.id), **serialize(m)})
    if not defer:
        spawn(deliver(bot.id, m.id))
    return m


def release(m: BotMessage) -> None:
    spawn(deliver(m.bot_id, m.id))


async def group_post(db, master: MarketplaceMaster, text: str, *, sender_type: str = "master",  # type: ignore[no-untyped-def]
                     sender_bot: Optional[Bot] = None, fan_out: bool = True) -> GroupMessage:
    name = master.name if sender_type == "master" else (sender_bot.name if sender_bot else "system")
    gm = GroupMessage(master_id=master.id, sender_type=sender_type, sender_bot_id=sender_bot.id if sender_bot else None,
                      sender_name=name, text=text)
    db.add(gm)
    await db.commit()
    if fan_out:
        members = list(await db.scalars(select(GroupMember).where(GroupMember.master_id == master.id)))
        for mem in members:
            if sender_bot and mem.bot_id == sender_bot.id:
                continue
            b = await db.get(Bot, mem.bot_id)
            if b and b.status == "ACTIVE" and b.delivery != "connector":
                await post_to_bot(db, b, text, kind="message", chat="group", group_id=master.id,
                                  sender=f"{sender_type}:{name}")
    return gm


async def _set_member(db, mem: GroupMember, status: str, error: Optional[str] = None) -> None:  # type: ignore[no-untyped-def]
    mem.status = status
    mem.last_error = error
    if status == "INJECTED":
        mem.injected_at = now()
    if status == "GRADUATED":
        mem.graduated_at = now()
    await db.commit()
    await hub.broadcast_to_user(mem.user_id, {"type": "ONBOARDING_STATUS", "member_id": str(mem.id), "bot_id": str(mem.bot_id),
                                              "master_id": str(mem.master_id), "status": status, "error": error})


# --------------------------------------------------------------------------- onboarding
async def onboard(member_id: uuid.UUID, welcome: bool = True) -> None:
    """Automated flow: welcome -> inject skill -> (after ack) follow-up test."""
    try:
        async with SessionLocal() as db:
            mem = await db.get(GroupMember, member_id)
            if mem is None:
                return
            master = await db.get(MarketplaceMaster, mem.master_id)
            bot = await db.get(Bot, mem.bot_id)
            if master is None or bot is None:
                return
            payload = master_skill_payload(master)
            mem.skill_id = payload["skill_id"]
            if welcome:
                text = (master.welcome_message or f"Welcome {bot.name}! I'm {master.name}. I'll install my skills into you now.")
                text = text.replace("{bot}", bot.name).replace("{master}", master.name)
                await group_post(db, master, f"👋 {text}", fan_out=False)
                if bot.delivery != "connector":
                    await post_to_bot(db, bot, text, kind="welcome", chat="group", group_id=master.id, sender=f"master:{master.name}")

            if bot.delivery == "connector":
                if bot.connector_id is None or not hub.is_online(bot.connector_id):
                    await _set_member(db, mem, "WAITING", "Connector offline — onboarding resumes automatically when it connects")
                    return
                req = f"mkt:{mem.id}"
                ok = await hub.send_to_connector(bot.connector_id, {"type": "SKILL_INJECT", "request_id": req,
                                                                    "agent_name": bot.agent_name, "payload": payload})
                if not ok:
                    await _set_member(db, mem, "WAITING", "Connector unreachable — will retry on reconnect")
                    return
                mem.inject_ref = req
                await _set_member(db, mem, "INJECTING")
            else:
                m = await post_to_bot(db, bot, f"Skill package '{payload.get('name')}' from {master.name}. Install it and ACK.",
                                      kind="skill_inject", chat="group", group_id=master.id, sender=f"master:{master.name}",
                                      payload={"skill": payload, "ack": "POST /api/bot/ack {update_id, ok}"}, defer=True)
                mem.inject_ref = str(m.id)
                await _set_member(db, mem, "INJECTING")
                release(m)
            await group_post(db, master, f"🧠 Injecting skill `{payload['skill_id']}` into {bot.name}…", fan_out=False)
    except Exception as exc:  # noqa: BLE001
        log.exception("onboarding %s failed: %s", member_id, exc)


async def _after_injected(member_id: uuid.UUID) -> None:
    """Skill acknowledged -> record it on the bot and send the follow-up test."""
    async with SessionLocal() as db:
        mem = await db.get(GroupMember, member_id)
        if mem is None:
            return
        master = await db.get(MarketplaceMaster, mem.master_id)
        bot = await db.get(Bot, mem.bot_id)
        payload = master_skill_payload(master)
        skills = [s for s in (bot.skills_json or []) if s.get("skill_id") != payload["skill_id"]]
        skills.append({"skill_id": payload["skill_id"], "name": payload.get("name"), "master": master.slug, "installed_at": now().isoformat()})
        bot.skills_json = skills
        if mem.is_master_clone:
            bot.persona = master.persona
        await _set_member(db, mem, "INJECTED")
        await group_post(db, master, f"✅ {bot.name} installed `{payload['skill_id']}`. Running the follow-up test…", fan_out=False)
        await webhooks.emit(bot.user_id, "skill.injected", {"bot_id": str(bot.id), "bot_name": bot.name, "skill_id": payload["skill_id"],
                                                            "master": master.slug, "via": bot.delivery})
        if bot.delivery == "connector":
            from fastapi import HTTPException
            from .routers.tasks import dispatch_task
            user = await db.get(User, bot.user_id)
            conn = await db.get(Connector, bot.connector_id) if bot.connector_id else None
            task_id = uuid.uuid4().hex
            mem.test_ref = task_id
            await _set_member(db, mem, "TESTING")
            try:
                if conn is None:
                    raise HTTPException(409, "connector missing")
                await dispatch_task(db, user, conn, ["echo", f"BridgeBot skill {payload['skill_id']} active on {bot.agent_name}"],
                                    agent_name=bot.agent_name, timeout_s=30, source="marketplace-test", task_id=task_id)
            except HTTPException as exc:
                await _set_member(db, mem, "FAILED", f"Test task not dispatched: {exc.detail}")
                return
        else:
            prompt = master.test_prompt or f"Test: reply to this message to confirm the {master.name} skill is active."
            m = await post_to_bot(db, bot, prompt, kind="task", chat="group", group_id=master.id, sender=f"master:{master.name}",
                                  payload={"skill_id": payload["skill_id"], "expect": master.test_expect,
                                           "reply": "POST /api/bot/messages {reply_to: update_id, text}"}, defer=True)
            mem.test_ref = str(m.id)
            await _set_member(db, mem, "TESTING")
            release(m)


async def _graduate(db, mem: GroupMember, evidence: str) -> None:  # type: ignore[no-untyped-def]
    master = await db.get(MarketplaceMaster, mem.master_id)
    bot = await db.get(Bot, mem.bot_id)
    await _set_member(db, mem, "GRADUATED")
    title = "is now a certified master clone" if mem.is_master_clone else "passed onboarding"
    await group_post(db, master, f"🎓 {bot.name} {title} of {master.name}! Test evidence: {evidence[:200]}")
    await webhooks.emit(bot.user_id, "bot.onboarded", {"bot_id": str(bot.id), "bot_name": bot.name, "master": master.slug,
                                                       "skill_id": mem.skill_id, "master_clone": mem.is_master_clone})


async def handle_ack(bot_id: uuid.UUID, update_id: int, ok: bool, error: Optional[str]) -> None:
    async with SessionLocal() as db:
        m = await db.get(BotMessage, update_id)
        if m is None or m.bot_id != bot_id:
            return
        if m.acked_at is None:
            m.acked_at = now()
            await db.commit()
        if m.kind != "skill_inject":
            return
        mem = await db.scalar(select(GroupMember).where(GroupMember.bot_id == bot_id, GroupMember.inject_ref == str(update_id)))
        if mem is None or mem.status != "INJECTING":
            return
        if not ok:
            await _set_member(db, mem, "FAILED", error or "bot rejected the skill")
            return
        member_id = mem.id
    await _after_injected(member_id)


async def handle_bot_post(db, bot: Bot, text: str, *, reply_to: Optional[int], chat: Optional[str],  # type: ignore[no-untyped-def]
                          group_id: Optional[uuid.UUID], payload: Any = None) -> BotMessage:
    """A bot posted a message (REST, WS or webhook response)."""
    parent = await db.get(BotMessage, reply_to) if reply_to else None
    if parent is not None and parent.bot_id != bot.id:
        parent = None
    if chat is None:
        chat = parent.chat if parent else "direct"
    if group_id is None and parent is not None:
        group_id = parent.group_id
    out = BotMessage(bot_id=bot.id, direction="out", kind="message", chat=chat, group_id=group_id if chat == "group" else None,
                     sender=f"bot:{bot.name}", text=text[:20000], payload_json=payload, reply_to=parent.id if parent else None)
    bot.last_seen_at = now()
    db.add(out)
    if parent is not None and parent.acked_at is None:
        parent.acked_at = now()
    await db.commit()
    await db.refresh(out)
    await hub.broadcast_to_user(bot.user_id, {"type": "BOT_MESSAGE", "bot_id": str(bot.id), **serialize(out)})
    await webhooks.emit(bot.user_id, "bot.message", {"bot_id": str(bot.id), "bot_name": bot.name, "chat": chat,
                                                     "group_id": str(group_id) if group_id else None, "text": text[:2000]})

    # Group chat: relay to other members; the master auto-guides questions.
    if chat == "group" and group_id is not None:
        master = await db.get(MarketplaceMaster, group_id)
        is_member = await db.scalar(select(GroupMember).where(GroupMember.bot_id == bot.id, GroupMember.master_id == group_id))
        if master is not None and is_member is not None:
            if parent is not None and parent.kind == "task":
                await group_post(db, master, text, sender_type="bot", sender_bot=bot, fan_out=False)
            else:
                await group_post(db, master, text, sender_type="bot", sender_bot=bot)
                if "?" in text or "@master" in text.lower() or "help" in text.lower():
                    answer = guide_answer(master, text)
                    await post_to_bot(db, bot, answer, kind="message", chat="group", group_id=master.id,
                                      sender=f"master:{master.name}", reply_to=out.id)
                    await group_post(db, master, answer, fan_out=False)
    # Follow-up test answer?
    if parent is not None and parent.kind == "task":
        mem = await db.scalar(select(GroupMember).where(GroupMember.bot_id == bot.id, GroupMember.test_ref == str(parent.id)))
        if mem is not None and mem.status == "TESTING":
            expect = (parent.payload_json or {}).get("expect")
            if not expect or expect.lower() in text.lower():
                await _graduate(db, mem, text)
            else:
                master = await db.get(MarketplaceMaster, mem.master_id)
                retry = await post_to_bot(db, bot, f"Almost! Your answer should mention “{expect}”. Try again by replying to this message.",
                                          kind="task", chat="group", group_id=mem.master_id, sender=f"master:{master.name}",
                                          payload=parent.payload_json, defer=True)
                mem.test_ref = str(retry.id)
                await db.commit()
                release(retry)
    return out


def guide_answer(master: MarketplaceMaster, question: str) -> str:
    """Deterministic guidance from the master's own skill package (always free)."""
    p = master.skill_payload_json or {}
    tools = ", ".join(t.get("name", "") for t in (p.get("tools") or []) + (master.tools_json or []) if isinstance(t, dict))
    rails = "; ".join((p.get("guardrails") or [])[:3])
    tmpl = ", ".join(t.get("name", "") for t in (p.get("prompt_templates") or []) if isinstance(t, dict))
    return (f"{master.name} here. Follow the injected system prompt of `{p.get('skill_id') or master.slug}`. "
            f"Tools: {tools or '—'}. Templates: {tmpl or '—'}. Guardrails: {rails or '—'}.")


# --------------------------------------------------------------------------- connector-bot hooks (called from ws.py)
async def on_connector_skill_ack(msg: Dict[str, Any]) -> None:
    req = str(msg.get("request_id") or "")
    if not req.startswith("mkt:"):
        return
    try:
        member_id = uuid.UUID(req[4:])
    except ValueError:
        return
    async with SessionLocal() as db:
        mem = await db.get(GroupMember, member_id)
        if mem is None or mem.status != "INJECTING":
            return
        if not msg.get("ok"):
            await _set_member(db, mem, "FAILED", str(msg.get("error") or "connector rejected the skill"))
            return
    await _after_injected(member_id)


async def on_connector_task_result(msg: Dict[str, Any]) -> None:
    task_id = str(msg.get("task_id") or "")
    if not task_id:
        return
    async with SessionLocal() as db:
        mem = await db.scalar(select(GroupMember).where(GroupMember.test_ref == task_id))
        if mem is None or mem.status != "TESTING":
            return
        if msg.get("exit_code") == 0:
            await _graduate(db, mem, " ".join(msg.get("stdout_tail") or []) or "exit 0")
        else:
            await _set_member(db, mem, "FAILED", f"Test task exit {msg.get('exit_code')}: {msg.get('error') or ''}")


async def resume_for_connector(connector_id: uuid.UUID) -> None:
    """Connector came online: continue onboarding that was waiting for it."""
    async with SessionLocal() as db:
        bots = list(await db.scalars(select(Bot).where(Bot.connector_id == connector_id)))
        ids: List[uuid.UUID] = []
        for b in bots:
            ids += [m.id for m in await db.scalars(select(GroupMember).where(GroupMember.bot_id == b.id, GroupMember.status == "WAITING"))]
    for mid in ids:
        await onboard(mid, welcome=False)


async def flush_pending(bot_id: uuid.UUID) -> int:
    """Send undelivered inbox items to a freshly connected bot WebSocket."""
    async with SessionLocal() as db:
        rows = list(await db.scalars(select(BotMessage).where(BotMessage.bot_id == bot_id, BotMessage.direction == "in",
                                                              BotMessage.delivered_at.is_(None)).order_by(BotMessage.id).limit(200)))
        n = 0
        for m in rows:
            if await hub.send_to_bot(bot_id, {"type": "UPDATE", **serialize(m)}):
                m.delivered_at = now()
                n += 1
        await db.commit()
        return n
