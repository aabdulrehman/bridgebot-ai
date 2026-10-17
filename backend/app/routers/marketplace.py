"""
Master Bot Marketplace.

Public:   GET  /api/marketplace                       published Master Bots
          GET  /api/marketplace/{id_or_slug}
User:     POST /api/marketplace/{id}/join          {bot_id}   add your bot to the master's group (auto-onboarding)
          POST /api/marketplace/{id}/make-master   {bot_id}   one click: join + clone persona/skills/tools
          POST /api/marketplace/{id}/leave         {bot_id}
          GET  /api/marketplace/{id}/group                     group feed + members (members' owners / admin)
          GET  /api/marketplace/me/memberships
Admin:    GET/POST /api/admin/marketplace, PATCH/DELETE /api/admin/marketplace/{id},
          POST /api/admin/marketplace/from-training/{training_request_id}
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import botnet
from ..audit import write_audit  # noqa: F401  (kept for parity with other routers)
from ..billing.service import require_plan_at_least
from ..database import get_db
from ..deps import get_current_user, require_admin
from ..models import Bot, GroupMember, GroupMessage, MarketplaceMaster, Plan, TrainingRequest, User

router = APIRouter(tags=["marketplace"])


class JoinIn(BaseModel):
    bot_id: uuid.UUID


class MasterIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    slug: Optional[str] = Field(default=None, max_length=80)
    avatar: str = Field(default="🤖", max_length=16)
    tagline: str = Field(default="", max_length=200)
    description: str = ""
    persona: str = ""
    skill_payload: Dict[str, Any] = Field(default_factory=dict)
    tools: List[Dict[str, Any]] = Field(default_factory=list)
    welcome_message: str = ""
    test_prompt: str = ""
    test_expect: Optional[str] = None
    min_plan_id: str = "FREE"
    is_published: bool = True


class MasterPatch(BaseModel):
    name: Optional[str] = None
    avatar: Optional[str] = None
    tagline: Optional[str] = None
    description: Optional[str] = None
    persona: Optional[str] = None
    skill_payload: Optional[Dict[str, Any]] = None
    tools: Optional[List[Dict[str, Any]]] = None
    welcome_message: Optional[str] = None
    test_prompt: Optional[str] = None
    test_expect: Optional[str] = None
    min_plan_id: Optional[str] = None
    is_published: Optional[bool] = None


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:80] or uuid.uuid4().hex[:8]


async def master_out(db: AsyncSession, m: MarketplaceMaster, full: bool = False) -> Dict[str, Any]:
    members = await db.scalar(select(func.count()).select_from(GroupMember).where(GroupMember.master_id == m.id)) or 0
    grads = await db.scalar(select(func.count()).select_from(GroupMember).where(GroupMember.master_id == m.id, GroupMember.status == "GRADUATED")) or 0
    plan = await db.get(Plan, m.min_plan_id)
    p = m.skill_payload_json or {}
    d = {
        "id": str(m.id), "slug": m.slug, "name": m.name, "avatar": m.avatar, "tagline": m.tagline, "description": m.description,
        "persona": m.persona, "min_plan_id": m.min_plan_id, "min_plan_name": plan.name if plan else m.min_plan_id,
        "is_published": m.is_published, "members": members, "graduated": grads,
        "skill_id": botnet.master_skill_payload(m)["skill_id"], "skill_name": p.get("name"),
        "tools": [t.get("name") for t in (p.get("tools") or []) + (m.tools_json or []) if isinstance(t, dict)],
        "test_prompt": m.test_prompt, "created_at": m.created_at.isoformat(),
    }
    if full:
        d.update(skill_payload=m.skill_payload_json, tools_json=m.tools_json, welcome_message=m.welcome_message, test_expect=m.test_expect)
    return d


async def _get_master(db: AsyncSession, key: str, published_only: bool = True) -> MarketplaceMaster:
    try:
        cond = or_(MarketplaceMaster.id == uuid.UUID(key), MarketplaceMaster.slug == key)
    except ValueError:
        cond = MarketplaceMaster.slug == key
    m = await db.scalar(select(MarketplaceMaster).where(cond))
    if m is None or (published_only and not m.is_published):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Master Bot not found")
    return m


# --------------------------------------------------------------------------- public
@router.get("/api/marketplace")
async def list_masters(db: AsyncSession = Depends(get_db)) -> List[Dict[str, Any]]:
    rows = await db.scalars(select(MarketplaceMaster).where(MarketplaceMaster.is_published.is_(True)).order_by(MarketplaceMaster.created_at))
    return [await master_out(db, m) for m in rows]


@router.get("/api/marketplace/me/memberships")
async def my_memberships(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> List[Dict[str, Any]]:
    out = []
    for mem in await db.scalars(select(GroupMember).where(GroupMember.user_id == user.id).order_by(GroupMember.joined_at)):
        master = await db.get(MarketplaceMaster, mem.master_id)
        bot = await db.get(Bot, mem.bot_id)
        out.append(_mem_out(mem, master, bot))
    return out


def _mem_out(mem: GroupMember, master: Optional[MarketplaceMaster], bot: Optional[Bot]) -> Dict[str, Any]:
    return {"id": str(mem.id), "master_id": str(mem.master_id), "master": master.name if master else None,
            "bot_id": str(mem.bot_id), "bot": bot.name if bot else None, "status": mem.status, "master_clone": mem.is_master_clone,
            "skill_id": mem.skill_id, "error": mem.last_error, "joined_at": mem.joined_at.isoformat(),
            "graduated_at": mem.graduated_at.isoformat() if mem.graduated_at else None}


@router.get("/api/marketplace/{key}")
async def get_master(key: str, db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    return await master_out(db, await _get_master(db, key))


async def _join(db: AsyncSession, user: User, key: str, bot_id: uuid.UUID, clone: bool) -> Dict[str, Any]:
    master = await _get_master(db, key)
    plan = await db.get(Plan, master.min_plan_id)
    if plan is not None:
        require_plan_at_least(user, plan)
    bot = await db.get(Bot, bot_id)
    if bot is None or bot.user_id != user.id:
        raise HTTPException(404, "Bot not found")
    if bot.status != "ACTIVE":
        raise HTTPException(409, "Bot is disabled")
    mem = await db.scalar(select(GroupMember).where(GroupMember.master_id == master.id, GroupMember.bot_id == bot.id))
    fresh = mem is None
    if fresh:
        mem = GroupMember(master_id=master.id, bot_id=bot.id, user_id=user.id, status="JOINED", is_master_clone=clone)
        db.add(mem)
        await db.commit()
        await db.refresh(mem)
        await botnet.group_post(db, master, f"➕ {bot.name} ({bot.framework}) joined the group.", sender_type="system")
        from .. import webhooks
        await webhooks.emit(user.id, "bot.joined", {"bot_id": str(bot.id), "bot_name": bot.name, "master": master.slug, "master_clone": clone})
    elif clone and not mem.is_master_clone:
        mem.is_master_clone = True
        await db.commit()
    run = fresh or clone or mem.status in ("FAILED", "WAITING")
    if run:
        mem.status, mem.last_error = "JOINED", None
        await db.commit()
        # The automation (welcome -> inject -> test) runs in the background; instant for live bots.
        botnet.spawn(botnet.onboard(mem.id, welcome=fresh))
    return {"membership": _mem_out(mem, master, bot), "onboarding_started": run}


@router.post("/api/marketplace/{key}/join")
async def join(key: str, body: JoinIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """Add your bot to the Master Bot's messenger group. The master welcomes, trains and tests it automatically."""
    return await _join(db, user, key, body.bot_id, clone=False)


@router.post("/api/marketplace/{key}/make-master")
async def make_master(key: str, body: JoinIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """One click 'Make my bot a master': join (if needed) and clone the master's skill payload + persona + tools into your bot."""
    return await _join(db, user, key, body.bot_id, clone=True)


@router.post("/api/marketplace/{key}/leave")
async def leave(key: str, body: JoinIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    master = await _get_master(db, key, published_only=False)
    mem = await db.scalar(select(GroupMember).where(GroupMember.master_id == master.id, GroupMember.bot_id == body.bot_id,
                                                    GroupMember.user_id == user.id))
    if mem is None:
        raise HTTPException(404, "Not a member")
    bot = await db.get(Bot, body.bot_id)
    await db.delete(mem)
    await db.commit()
    await botnet.group_post(db, master, f"➖ {bot.name if bot else 'A bot'} left the group.", sender_type="system")
    return {"ok": True}


@router.get("/api/marketplace/{key}/group")
async def group_feed(key: str, limit: int = Query(100, ge=1, le=500), user: User = Depends(get_current_user),
                     db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    master = await _get_master(db, key, published_only=user.role != "admin")
    mine = list(await db.scalars(select(GroupMember).where(GroupMember.master_id == master.id, GroupMember.user_id == user.id)))
    if not mine and user.role != "admin":
        raise HTTPException(403, "Add one of your bots to this group to see it")
    msgs = list(await db.scalars(select(GroupMessage).where(GroupMessage.master_id == master.id).order_by(GroupMessage.id.desc()).limit(limit)))
    members = []
    for mem in await db.scalars(select(GroupMember).where(GroupMember.master_id == master.id).order_by(GroupMember.joined_at)):
        bot = await db.get(Bot, mem.bot_id)
        d = _mem_out(mem, master, bot)
        d["mine"] = mem.user_id == user.id
        d["framework"] = bot.framework if bot else None
        if mem.user_id != user.id and user.role != "admin":
            d.pop("error", None)
        members.append(d)
    return {"master": await master_out(db, master), "members": members,
            "messages": [{"id": g.id, "sender_type": g.sender_type, "sender": g.sender_name, "text": g.text,
                          "created_at": g.created_at.isoformat()} for g in reversed(msgs)]}


# --------------------------------------------------------------------------- admin
@router.get("/api/admin/marketplace")
async def admin_list(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> List[Dict[str, Any]]:
    rows = await db.scalars(select(MarketplaceMaster).order_by(MarketplaceMaster.created_at))
    return [await master_out(db, m, full=True) for m in rows]


@router.post("/api/admin/marketplace", status_code=201)
async def admin_create(body: MasterIn, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    if await db.get(Plan, body.min_plan_id) is None:
        raise HTTPException(422, "Unknown min_plan_id")
    slug = slugify(body.slug or body.name)
    if await db.scalar(select(MarketplaceMaster).where(MarketplaceMaster.slug == slug)) is not None:
        raise HTTPException(409, f"Slug '{slug}' already exists")
    m = MarketplaceMaster(slug=slug, name=body.name, avatar=body.avatar, tagline=body.tagline, description=body.description,
                          persona=body.persona, skill_payload_json=body.skill_payload, tools_json=body.tools,
                          welcome_message=body.welcome_message, test_prompt=body.test_prompt, test_expect=body.test_expect or None,
                          min_plan_id=body.min_plan_id, is_published=body.is_published, created_by=admin.id)
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return await master_out(db, m, full=True)


@router.patch("/api/admin/marketplace/{master_id}")
async def admin_patch(master_id: uuid.UUID, body: MasterPatch, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    m = await db.get(MarketplaceMaster, master_id)
    if m is None:
        raise HTTPException(404, "Not found")
    data = body.model_dump(exclude_unset=True)
    if "min_plan_id" in data and await db.get(Plan, data["min_plan_id"]) is None:
        raise HTTPException(422, "Unknown min_plan_id")
    mapping = {"skill_payload": "skill_payload_json", "tools": "tools_json"}
    for k, v in data.items():
        setattr(m, mapping.get(k, k), v)
    await db.commit()
    return await master_out(db, m, full=True)


@router.delete("/api/admin/marketplace/{master_id}", status_code=204)
async def admin_delete(master_id: uuid.UUID, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> None:
    m = await db.get(MarketplaceMaster, master_id)
    if m is None:
        raise HTTPException(404, "Not found")
    await db.delete(m)
    await db.commit()


@router.post("/api/admin/marketplace/from-training/{training_id}", status_code=201)
async def admin_from_training(training_id: uuid.UUID, min_plan_id: str = "FREE", admin: User = Depends(require_admin),
                              db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """Publish a READY/APPROVED training payload (generated by the Master Bot) as a marketplace Master Bot."""
    tr = await db.get(TrainingRequest, training_id)
    if tr is None or not tr.generated_payload_json:
        raise HTTPException(404, "Training request with a payload not found")
    p = tr.generated_payload_json
    body = MasterIn(name=f"{p.get('name') or 'Skill'} Master", tagline=(p.get("niche") or "")[:200], description=tr.niche_requirements_text,
                    persona=p.get("system_prompt", "")[:4000], skill_payload=p, min_plan_id=min_plan_id,
                    welcome_message="Welcome {bot}! I'm {master}. Installing my skill package into you now.",
                    test_prompt=f"Test: in one sentence, what is your new specialty? (mention '{(p.get('niche') or p.get('name') or 'skill')[:40]}')")
    return await admin_create(body, admin, db)
