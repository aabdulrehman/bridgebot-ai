"""Master Admin API (role=admin via ADMIN_EMAIL, JWT session only)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import write_audit
from ..billing.service import apply_plan, approve_upgrade, record_payment, usage
from ..database import get_db
from ..deps import require_admin
from ..hub import hub, kick
from ..models import AuditLog, Connector, Payment, Plan, TrainingRequest, UpgradeRequest, User, WebhookDelivery
from ..schemas import AdminDecisionIn, AdminUserPatch, AuditOut, UpgradeRequestOut, UserOut

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _req_out(r: UpgradeRequest) -> Dict[str, Any]:
    d = UpgradeRequestOut.model_validate(r).model_dump(mode="json")
    d["user_email"] = r.user.email if r.user else None
    return d


@router.get("/stats")
async def stats(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """Users, plans, revenue, usage overview."""
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    by_plan = dict((await db.execute(select(User.plan_id, func.count()).group_by(User.plan_id))).all())
    by_status = dict((await db.execute(select(User.status, func.count()).group_by(User.status))).all())
    plans = {p.id: p for p in await db.scalars(select(Plan))}
    mrr = sum(plans[pid].price_monthly_cents * n for pid, n in
              (await db.execute(select(User.plan_id, func.count()).where(User.status == "ACTIVE").group_by(User.plan_id))).all() if pid in plans)
    rev_total = await db.scalar(select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(Payment.status == "SUCCEEDED"))
    rev_30 = await db.scalar(select(func.coalesce(func.sum(Payment.amount_cents), 0))
                             .where(Payment.status == "SUCCEEDED", Payment.created_at >= now - timedelta(days=30)))
    trainings = dict((await db.execute(select(TrainingRequest.status, func.count()).group_by(TrainingRequest.status))).all())
    deliveries = dict((await db.execute(select(WebhookDelivery.status, func.count()).group_by(WebhookDelivery.status))).all())
    return {
        "users_total": sum(by_plan.values()),
        "users_by_plan": by_plan,
        "users_by_status": by_status,
        "new_users_7d": await db.scalar(select(func.count()).select_from(User).where(User.created_at >= now - timedelta(days=7))),
        "connectors_total": await db.scalar(select(func.count()).select_from(Connector)),
        "connectors_online": len(hub.connectors),
        "dashboards_open": sum(len(v) for v in hub.dashboards.values()),
        "mrr_cents": mrr,
        "revenue_total_cents": int(rev_total or 0),
        "revenue_30d_cents": int(rev_30 or 0),
        "pending_upgrades": await db.scalar(select(func.count()).select_from(UpgradeRequest).where(UpgradeRequest.status == "PENDING")),
        "tasks_today": await db.scalar(select(func.coalesce(func.sum(User.tasks_today), 0)).where(User.tasks_day == today)),
        "tokens_used_period": await db.scalar(select(func.coalesce(func.sum(User.tokens_used_period), 0))),
        "trainings_by_status": trainings,
        "webhook_deliveries": deliveries,
        "currency": "USD",
    }


@router.get("/users")
async def users(q: Optional[str] = None, plan_id: Optional[str] = None, status: Optional[str] = None,
                limit: int = Query(100, ge=1, le=500), offset: int = 0,
                _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    stmt = select(User)
    if q:
        stmt = stmt.where(User.email.ilike(f"%{q.lower()}%"))
    if plan_id:
        stmt = stmt.where(User.plan_id == plan_id)
    if status:
        stmt = stmt.where(User.status == status)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = list(await db.scalars(stmt.order_by(User.created_at.desc()).limit(limit).offset(offset)))
    counts = dict((await db.execute(select(Connector.user_id, func.count()).group_by(Connector.user_id))).all())
    online: Dict[uuid.UUID, int] = {}
    for s in hub.connectors.values():
        online[s.user_id] = online.get(s.user_id, 0) + 1
    out = []
    for u in rows:
        d = UserOut.model_validate(u).model_dump(mode="json")
        d["machines"] = counts.get(u.id, 0)
        d["machines_online"] = online.get(u.id, 0)
        out.append(d)
    return {"total": total, "users": out}


@router.get("/users/{user_id}")
async def user_detail(user_id: uuid.UUID, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    u = await db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "User not found")
    conns = list(await db.scalars(select(Connector).where(Connector.user_id == u.id)))
    reqs = list(await db.scalars(select(UpgradeRequest).where(UpgradeRequest.user_id == u.id).order_by(UpgradeRequest.created_at.desc())))
    pays = list(await db.scalars(select(Payment).where(Payment.user_id == u.id).order_by(Payment.created_at.desc())))
    return {
        "user": UserOut.model_validate(u).model_dump(mode="json"),
        "usage": await usage(db, u),
        "connectors": [{"id": str(c.id), "machine_name": c.machine_name, "online": hub.is_online(c.id),
                        "last_heartbeat": c.last_heartbeat.isoformat() if c.last_heartbeat else None} for c in conns],
        "upgrade_requests": [_req_out(r) for r in reqs],
        "payments": [{"id": str(p.id), "plan_id": p.plan_id, "amount_cents": p.amount_cents, "provider": p.provider,
                      "reference": p.reference, "created_at": p.created_at.isoformat()} for p in pays],
    }


@router.patch("/users/{user_id}")
async def patch_user(user_id: uuid.UUID, body: AdminUserPatch, admin: User = Depends(require_admin),
                     db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """Change plan, suspend/ban/reactivate, adjust tokens."""
    u = await db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "User not found")
    changes: Dict[str, Any] = {}
    if body.status and body.status != u.status:
        if u.id == admin.id and body.status != "ACTIVE":
            raise HTTPException(400, "You cannot suspend or ban yourself")
        u.status = body.status
        changes["status"] = body.status
    if body.plan_id:
        await apply_plan(db, u, body.plan_id, body.months)
        changes["plan_id"] = body.plan_id
    if body.token_adjust:
        u.token_balance = max(0, u.token_balance + body.token_adjust)
        changes["token_adjust"] = body.token_adjust
    await db.commit()
    if u.status != "ACTIVE":  # kick live sessions
        for s in [s for s in hub.connectors.values() if s.user_id == u.id]:
            await kick(s.websocket, 4003, f"Account {u.status.lower()}")
        for ws in list(hub.dashboards.get(u.id, ())):
            await ws.close(code=4003)
    await write_audit(None, "ADMIN_USER_UPDATE", {"admin": admin.email, "user": u.email, "changes": changes, "note": body.note})
    return {"user": UserOut.model_validate(u).model_dump(mode="json"), "changes": changes}


@router.get("/upgrade-requests")
async def upgrade_requests(status: Optional[str] = "PENDING", _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> List[Dict[str, Any]]:
    stmt = select(UpgradeRequest)
    if status and status != "ALL":
        stmt = stmt.where(UpgradeRequest.status == status)
    return [_req_out(r) for r in await db.scalars(stmt.order_by(UpgradeRequest.created_at.desc()).limit(200))]


@router.post("/upgrade-requests/{request_id}/approve")
async def approve(request_id: uuid.UUID, body: AdminDecisionIn, admin: User = Depends(require_admin),
                  db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    req = await db.get(UpgradeRequest, request_id)
    if req is None:
        raise HTTPException(404, "Request not found")
    user = await approve_upgrade(db, req, admin.id, body.note)
    await db.commit()
    await write_audit(None, "ADMIN_UPGRADE_APPROVED", {"admin": admin.email, "user": user.email, "plan": req.requested_plan_id})
    return {"request": _req_out(req), "user": UserOut.model_validate(user).model_dump(mode="json")}


@router.post("/upgrade-requests/{request_id}/reject")
async def reject(request_id: uuid.UUID, body: AdminDecisionIn, admin: User = Depends(require_admin),
                 db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    req = await db.get(UpgradeRequest, request_id)
    if req is None:
        raise HTTPException(404, "Request not found")
    if req.status != "PENDING":
        raise HTTPException(409, f"Request is {req.status}")
    req.status, req.decided_by, req.decided_at, req.admin_note = "REJECTED", admin.id, datetime.now(timezone.utc), body.note
    user = await db.get(User, req.user_id)
    if user and user.subscription_status == "PENDING_UPGRADE":
        user.subscription_status = "ACTIVE"
    await db.commit()
    return {"request": _req_out(req)}


@router.post("/payments")
async def add_payment(user_id: uuid.UUID, plan_id: str, amount_cents: int, reference: Optional[str] = None,
                      admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> Dict[str, str]:
    """Record an off-platform payment (e.g. renewal paid via WhatsApp/bank)."""
    u = await db.get(User, user_id)
    plan = await db.get(Plan, plan_id)
    if u is None or plan is None:
        raise HTTPException(404, "User or plan not found")
    p = await record_payment(db, u, plan, amount_cents, "manual", reference)
    await db.commit()
    return {"payment_id": str(p.id)}


@router.get("/payments")
async def payments(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> List[Dict[str, Any]]:
    rows = (await db.execute(select(Payment, User.email).join(User, User.id == Payment.user_id).order_by(Payment.created_at.desc()).limit(200))).all()
    return [{"id": str(p.id), "user_email": e, "plan_id": p.plan_id, "amount_cents": p.amount_cents, "currency": p.currency,
             "provider": p.provider, "reference": p.reference, "created_at": p.created_at.isoformat()} for p, e in rows]


@router.get("/connectors")
async def all_connectors(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> List[Dict[str, Any]]:
    rows = (await db.execute(select(Connector, User.email).join(User, User.id == Connector.user_id).order_by(Connector.created_at.desc()))).all()
    return [{"id": str(c.id), "machine_name": c.machine_name, "user_email": e, "online": hub.is_online(c.id),
             "last_heartbeat": c.last_heartbeat.isoformat() if c.last_heartbeat else None,
             "created_at": c.created_at.isoformat()} for c, e in rows]


@router.get("/audit", response_model=List[AuditOut])
async def audit(event_type: Optional[str] = None, connector_id: Optional[uuid.UUID] = None, limit: int = Query(200, ge=1, le=1000),
                _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> list[AuditLog]:
    stmt = select(AuditLog)
    if event_type:
        stmt = stmt.where(or_(AuditLog.event_type == event_type, AuditLog.event_type.like(f"{event_type}%")))
    if connector_id:
        stmt = stmt.where(AuditLog.connector_id == connector_id)
    return list(await db.scalars(stmt.order_by(AuditLog.created_at.desc()).limit(limit)))
