"""Subscription self-service: current plan/usage, request upgrade, cancel request, provider webhooks."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..billing.providers import get_provider
from ..billing.service import apply_plan, get_plan, record_payment, usage
from ..config import get_settings
from ..database import get_db
from ..deps import get_current_user
from ..models import UpgradeRequest, User
from ..schemas import PlanOut, UpgradeRequestIn, UpgradeRequestOut, UserOut

router = APIRouter(prefix="/api/billing", tags=["billing"])
settings = get_settings()


def whatsapp_link(user: User, plan_name: str, ref: str | None = None) -> str:
    from urllib.parse import quote

    msg = f"Hello BridgeBot AI, I want to upgrade to the {plan_name} plan. Account email: {user.email}."
    if ref:
        msg += f" Upgrade request: {ref}."
    return f"https://wa.me/{settings.support_whatsapp}?text={quote(msg)}"


@router.get("/me")
async def my_subscription(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """Plan, limits, usage, period and any pending upgrade request."""
    pending = await db.scalar(select(UpgradeRequest).where(UpgradeRequest.user_id == user.id, UpgradeRequest.status == "PENDING")
                              .order_by(UpgradeRequest.created_at.desc()))
    return {
        "user": UserOut.model_validate(user).model_dump(mode="json"),
        "plan": PlanOut.model_validate(user.plan).model_dump(mode="json"),
        "usage": await usage(db, user),
        "pending_request": UpgradeRequestOut.model_validate(pending).model_dump(mode="json") if pending else None,
        "support_whatsapp": settings.support_whatsapp,
        "whatsapp_url": whatsapp_link(user, pending.requested_plan_id if pending else user.plan.name),
    }


@router.post("/upgrade-request", status_code=status.HTTP_201_CREATED)
async def request_upgrade(body: UpgradeRequestIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """
    Ask to change plan. With the manual provider an admin approves it after the
    user pays/confirms via WhatsApp. (Stripe/Paddle would return a checkout URL.)
    """
    plan = await get_plan(db, body.plan_id)
    if plan.id == user.plan_id and user.subscription_status == "ACTIVE" and body.plan_id == "FREE":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You are already on this plan")
    existing = await db.scalar(select(UpgradeRequest).where(UpgradeRequest.user_id == user.id, UpgradeRequest.status == "PENDING"))
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "You already have a pending request — cancel it first or wait for approval")
    provider = get_provider()
    checkout = await provider.create_checkout(user, plan, body.months)
    if body.payment_method == "paypal" and plan.price_monthly_cents and not (body.payment_reference or "").strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter your PayPal transaction ID or PayPal email so the admin can verify the payment")
    method = body.payment_method if provider.name == "manual" else provider.name
    req = UpgradeRequest(user_id=user.id, from_plan_id=user.plan_id, requested_plan_id=plan.id, payment_provider=method,
                         payment_reference=body.payment_reference, user_note=body.note, months=body.months,
                         amount_cents=plan.price_monthly_cents * body.months)
    db.add(req)
    if plan.price_monthly_cents == 0:  # downgrade to Free needs no payment: apply now
        await apply_plan(db, user, plan.id)
        req.status, req.decided_at, req.admin_note = "APPROVED", datetime.now(timezone.utc), "auto: free plan"
    else:
        user.subscription_status = "PENDING_UPGRADE" if user.subscription_status == "ACTIVE" else user.subscription_status
    await db.commit()
    await db.refresh(req)
    return {
        "request": UpgradeRequestOut.model_validate(req).model_dump(mode="json"),
        "checkout": checkout.__dict__,
        "whatsapp_url": whatsapp_link(user, plan.name, str(req.id)[:8]),
        "paypal_url": checkout.paypal_url,
        "message": ("PayPal payment submitted — an admin will verify the transaction and approve your upgrade."
                    if body.payment_method == "paypal" else "Pay/confirm via WhatsApp — an admin will approve your upgrade.")
                   if plan.price_monthly_cents else "Switched to Free.",
    }


@router.get("/requests", response_model=List[UpgradeRequestOut])
async def my_requests(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[UpgradeRequest]:
    return list(await db.scalars(select(UpgradeRequest).where(UpgradeRequest.user_id == user.id).order_by(UpgradeRequest.created_at.desc()).limit(50)))


@router.post("/requests/{request_id}/cancel", response_model=UpgradeRequestOut)
async def cancel_request(request_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> UpgradeRequest:
    import uuid as _uuid

    req = await db.get(UpgradeRequest, _uuid.UUID(request_id))
    if req is None or req.user_id != user.id:
        raise HTTPException(404, "Request not found")
    if req.status != "PENDING":
        raise HTTPException(409, f"Request is {req.status}")
    req.status, req.decided_at = "CANCELED", datetime.now(timezone.utc)
    if user.subscription_status == "PENDING_UPGRADE":
        user.subscription_status = "ACTIVE"
    await db.commit()
    return req


@router.post("/webhook/{provider_name}", include_in_schema=True)
async def provider_webhook(provider_name: str, request: Request, db: AsyncSession = Depends(get_db)) -> Dict[str, str]:
    """Entry point for Stripe/Paddle webhooks (activates plans automatically once implemented)."""
    import uuid as _uuid

    event = await get_provider(provider_name).parse_webhook(request)
    user = await db.get(User, _uuid.UUID(event.user_ref))
    if user is None:
        raise HTTPException(404, "Unknown user")
    if event.kind == "subscription.activated":
        plan = await apply_plan(db, user, event.plan_id, event.months)
        await record_payment(db, user, plan, event.amount_cents, provider_name, event.reference)
    elif event.kind == "subscription.canceled":
        await apply_plan(db, user, "FREE")
    await db.commit()
    return {"ok": "true"}
