"""Outbound webhook management + delivery log + per-user inbound webhook token."""
from __future__ import annotations

import uuid
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import webhooks
from ..billing.service import enforce_webhook_limit
from ..config import get_settings
from ..database import get_db
from ..deps import get_current_user
from ..models import User, Webhook, WebhookDelivery
from ..schemas import DeliveryOut, WebhookIn, WebhookOut, WebhookUpdateIn
from ..security import generate_hook_token, generate_webhook_secret

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])
settings = get_settings()


def _check(url: str | None, events: List[str] | None) -> None:
    if url is not None:
        try:
            webhooks.validate_webhook_url(url)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    if events is not None:
        bad = [e for e in events if e not in webhooks.EVENTS and e != "*"]
        if bad:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown events {bad}. Valid: {webhooks.EVENTS}")


async def _owned(db: AsyncSession, user: User, hook_id: uuid.UUID) -> Webhook:
    hook = await db.get(Webhook, hook_id)
    if hook is None or hook.user_id != user.id:
        raise HTTPException(404, "Webhook not found")
    return hook


@router.get("/events")
async def list_events() -> Dict[str, Any]:
    return {"events": webhooks.EVENTS}


@router.get("", response_model=List[WebhookOut])
async def list_hooks(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[Webhook]:
    return list(await db.scalars(select(Webhook).where(Webhook.user_id == user.id).order_by(Webhook.created_at)))


@router.post("", response_model=WebhookOut, status_code=status.HTTP_201_CREATED)
async def create_hook(body: WebhookIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> Webhook:
    await enforce_webhook_limit(db, user)
    _check(body.url, body.events)
    hook = Webhook(user_id=user.id, url=body.url, events_json=sorted(set(body.events)), description=body.description,
                   is_active=body.is_active, secret=generate_webhook_secret())
    db.add(hook)
    await db.commit()
    await db.refresh(hook)
    return hook


@router.patch("/{hook_id}", response_model=WebhookOut)
async def update_hook(hook_id: uuid.UUID, body: WebhookUpdateIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> Webhook:
    hook = await _owned(db, user, hook_id)
    _check(body.url, body.events)
    if body.url is not None:
        hook.url = body.url
    if body.events is not None:
        hook.events_json = sorted(set(body.events))
    if body.description is not None:
        hook.description = body.description
    if body.is_active is not None:
        hook.is_active = body.is_active
    await db.commit()
    await db.refresh(hook)
    return hook


@router.post("/{hook_id}/rotate-secret", response_model=WebhookOut)
async def rotate_secret(hook_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> Webhook:
    hook = await _owned(db, user, hook_id)
    hook.secret = generate_webhook_secret()
    await db.commit()
    return hook


@router.delete("/{hook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_hook(hook_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> None:
    hook = await _owned(db, user, hook_id)
    await db.delete(hook)
    await db.commit()


@router.post("/{hook_id}/test")
async def test_hook(hook_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """Queue a `webhook.test` delivery to this endpoint."""
    hook = await _owned(db, user, hook_id)
    db.add(WebhookDelivery(webhook_id=hook.id, event="webhook.test", payload_json={"message": "Hello from BridgeBot AI", "user": user.email}))
    await db.commit()
    webhooks._wakeup.set()
    return {"queued": True}


@router.get("/deliveries", response_model=List[DeliveryOut])
async def deliveries(webhook_id: uuid.UUID | None = None, limit: int = Query(50, ge=1, le=200),
                     user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[WebhookDelivery]:
    """Delivery log (newest first)."""
    q = select(WebhookDelivery).join(Webhook, Webhook.id == WebhookDelivery.webhook_id).where(Webhook.user_id == user.id)
    if webhook_id:
        q = q.where(WebhookDelivery.webhook_id == webhook_id)
    return list(await db.scalars(q.order_by(WebhookDelivery.created_at.desc()).limit(limit)))


@router.post("/deliveries/{delivery_id}/retry")
async def retry_delivery(delivery_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> Dict[str, bool]:
    d = await db.get(WebhookDelivery, delivery_id)
    hook = await db.get(Webhook, d.webhook_id) if d else None
    if d is None or hook is None or hook.user_id != user.id:
        raise HTTPException(404, "Delivery not found")
    await webhooks.retry_now(d.id)
    return {"queued": True}


# ---------------------------------------------------------------- inbound token
@router.get("/inbound")
async def inbound_info(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """Your inbound webhook URL path (POST to it to trigger a task)."""
    if not user.inbound_hook_token:
        user.inbound_hook_token = generate_hook_token()
        await db.commit()
    return {"path": f"/api/hooks/in/{user.inbound_hook_token}", "token": user.inbound_hook_token,
            "enabled_on_plan": user.plan.inbound_webhooks}


@router.post("/inbound/rotate")
async def inbound_rotate(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    user.inbound_hook_token = generate_hook_token()
    await db.commit()
    return {"path": f"/api/hooks/in/{user.inbound_hook_token}", "token": user.inbound_hook_token,
            "enabled_on_plan": user.plan.inbound_webhooks}
