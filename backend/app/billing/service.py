"""
Subscription lifecycle + plan-limit enforcement.

* Every account starts on FREE (auto-assigned) with a rolling billing period.
* `refresh_subscription()` is called on each authenticated request: when the
  period ends, FREE renews (tokens reset to the allowance); a paid plan that was
  not renewed is downgraded to FREE.
* `apply_plan()` is the ONE place a plan changes — used by admin approval,
  admin overrides and (later) payment-provider webhooks.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import ApiKey, Connector, Payment, Plan, UpgradeRequest, User, Webhook

settings = get_settings()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    """SQLite returns naive datetimes; treat them as UTC."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def get_plan(db: AsyncSession, plan_id: str) -> Plan:
    plan = await db.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown plan {plan_id}")
    return plan


async def start_period(db: AsyncSession, user: User, plan: Plan, months: int = 1) -> None:
    """Begin a fresh billing period on `plan` and grant its monthly tokens."""
    now = _now()
    user.plan_id = plan.id
    user.plan = plan
    user.subscription_status = "ACTIVE"
    user.current_period_start = now
    user.current_period_end = now + timedelta(days=settings.billing_period_days * max(1, months))
    user.tokens_used_period = 0
    user.token_balance = max(user.token_balance or 0, plan.monthly_tokens)


async def refresh_subscription(db: AsyncSession, user: User) -> bool:
    """Roll the period / expire paid plans. Returns True if the user row changed."""
    end = _aware(user.current_period_end)
    if end is not None and end > _now():
        return False
    if user.plan_id != "FREE" and end is not None:
        free = await get_plan(db, "FREE")
        user.token_balance = min(user.token_balance, free.monthly_tokens)
        await start_period(db, user, free)
        user.subscription_status = "PAST_DUE"  # signals "your paid plan lapsed" in the UI
        return True
    plan = await get_plan(db, user.plan_id or "FREE")
    await start_period(db, user, plan)
    if end is not None:  # renewal of an existing period: reset (no roll-over)
        user.token_balance = plan.monthly_tokens
    return True


async def apply_plan(db: AsyncSession, user: User, plan_id: str, months: int = 1, *, reset_tokens: bool = False) -> Plan:
    """Switch the user's plan now and start a new period."""
    plan = await get_plan(db, plan_id)
    await start_period(db, user, plan, months)
    if reset_tokens:
        user.token_balance = plan.monthly_tokens
    return plan


async def record_payment(db: AsyncSession, user: User, plan: Plan, amount_cents: int, provider: str,
                         reference: Optional[str], upgrade_request_id: Optional[uuid.UUID] = None) -> Payment:
    pay = Payment(user_id=user.id, plan_id=plan.id, amount_cents=amount_cents, currency=plan.currency,
                  provider=provider, reference=reference, upgrade_request_id=upgrade_request_id)
    db.add(pay)
    return pay


async def approve_upgrade(db: AsyncSession, req: UpgradeRequest, admin_id: Optional[uuid.UUID], note: Optional[str] = None) -> User:
    """Approve an upgrade request: change plan, record revenue."""
    if req.status != "PENDING":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Request is {req.status}")
    user = await db.get(User, req.user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    plan = await apply_plan(db, user, req.requested_plan_id, req.months)
    if req.amount_cents > 0:
        await record_payment(db, user, plan, req.amount_cents, req.payment_provider, req.payment_reference, req.id)
    req.status, req.decided_by, req.decided_at, req.admin_note = "APPROVED", admin_id, _now(), note
    return user


# --------------------------------------------------------------------------- limits
def _limit_reached(limit: int, used: int) -> bool:
    return limit != -1 and used >= limit


async def usage(db: AsyncSession, user: User) -> Dict[str, Any]:
    machines = await db.scalar(select(func.count()).select_from(Connector).where(Connector.user_id == user.id)) or 0
    webhooks = await db.scalar(select(func.count()).select_from(Webhook).where(Webhook.user_id == user.id)) or 0
    keys = await db.scalar(select(func.count()).select_from(ApiKey).where(ApiKey.user_id == user.id, ApiKey.revoked_at.is_(None))) or 0
    today = _now().strftime("%Y-%m-%d")
    return {
        "machines": int(machines),
        "webhooks": int(webhooks),
        "api_keys": int(keys),
        "tasks_today": user.tasks_today if user.tasks_day == today else 0,
        "tokens_used_period": user.tokens_used_period,
        "token_balance": user.token_balance,
    }


async def enforce_machine_limit(db: AsyncSession, user: User) -> None:
    used = await db.scalar(select(func.count()).select_from(Connector).where(Connector.user_id == user.id)) or 0
    if _limit_reached(user.plan.max_machines, used):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, f"{user.plan.name} plan allows {user.plan.max_machines} machine(s). Upgrade to add more.")


async def enforce_webhook_limit(db: AsyncSession, user: User) -> None:
    used = await db.scalar(select(func.count()).select_from(Webhook).where(Webhook.user_id == user.id)) or 0
    if user.plan.max_webhooks == 0 or _limit_reached(user.plan.max_webhooks, used):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, f"{user.plan.name} plan allows {user.plan.max_webhooks} webhook(s).")


async def enforce_api_key_limit(db: AsyncSession, user: User) -> None:
    used = await db.scalar(select(func.count()).select_from(ApiKey).where(ApiKey.user_id == user.id, ApiKey.revoked_at.is_(None))) or 0
    if user.plan.max_api_keys == 0 or _limit_reached(user.plan.max_api_keys, used):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, f"{user.plan.name} plan allows {user.plan.max_api_keys} API key(s).")


def consume_task_quota(user: User) -> None:
    """Count a remote task against today's quota (raises 402/429 when exhausted)."""
    limit = user.plan.max_tasks_per_day
    if limit == 0:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, f"Remote tasks are not included in the {user.plan.name} plan.")
    today = _now().strftime("%Y-%m-%d")
    if user.tasks_day != today:
        user.tasks_day, user.tasks_today = today, 0
    if _limit_reached(limit, user.tasks_today):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"Daily task limit reached ({limit}/day on {user.plan.name}). Upgrade for more.")
    user.tasks_today += 1


def require_inbound_webhooks(user: User) -> None:
    if not user.plan.inbound_webhooks:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, f"Inbound webhooks require Pro or Business (you are on {user.plan.name}).")


def require_training(user: User) -> None:
    if not user.plan.training_access:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, f"Training is not included in the {user.plan.name} plan.")


async def enforce_bot_limit(db: AsyncSession, user: User) -> None:
    from ..models import Bot
    used = await db.scalar(select(func.count()).select_from(Bot).where(Bot.user_id == user.id)) or 0
    if _limit_reached(user.plan.max_bots, used):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, f"{user.plan.name} plan allows {user.plan.max_bots} linked bot(s). Upgrade to link more.")


def require_plan_at_least(user: User, plan: Plan) -> None:
    """Marketplace gate: the user's plan tier (sort_order) must be >= the listing's min plan."""
    if user.plan.sort_order < plan.sort_order:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, f"This Master Bot requires the {plan.name} plan or higher. Upgrade on /billing.")


async def enforce_tunnel_limit(db: AsyncSession, user: User) -> None:
    from ..models import Tunnel
    used = await db.scalar(select(func.count()).select_from(Tunnel).where(Tunnel.user_id == user.id)) or 0
    if _limit_reached(user.plan.max_tunnels, used):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, f"{user.plan.name} plan allows {user.plan.max_tunnels} tunnel(s). Upgrade to add more.")
