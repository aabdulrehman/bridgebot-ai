"""Inbound webhooks: POST /api/hooks/in/{token} → run a task on one of your machines.

The secret token in the URL is the credential (rotate it in the dashboard).
Body: {"command": "echo hi", "connector_id": "<uuid>"}  or  {"command": [...], "machine_name": "home-pc"}
If neither is given and you have exactly one ONLINE machine, it is used.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..billing.service import refresh_subscription, require_inbound_webhooks
from ..database import get_db
from ..deps import assert_account_usable
from ..hub import hub
from ..models import Connector, User
from ..schemas import InboundHookIn
from .tasks import dispatch_task

router = APIRouter(prefix="/api/hooks", tags=["inbound-webhooks"])


@router.post("/in/{token}", status_code=status.HTTP_202_ACCEPTED)
async def inbound(token: str, body: InboundHookIn, db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    if len(token) < 20:
        raise HTTPException(404, "Unknown hook")
    user = await db.scalar(select(User).where(User.inbound_hook_token == token))
    if user is None:
        raise HTTPException(404, "Unknown hook")
    assert_account_usable(user)
    await refresh_subscription(db, user)
    require_inbound_webhooks(user)

    q = select(Connector).where(Connector.user_id == user.id)
    if body.connector_id:
        q = q.where(Connector.id == body.connector_id)
    elif body.machine_name:
        q = q.where(Connector.machine_name == body.machine_name)
    candidates = [c for c in await db.scalars(q)]
    if not body.connector_id and not body.machine_name:
        candidates = [c for c in candidates if hub.is_online(c.id)]
        if len(candidates) != 1:
            raise HTTPException(400, "Specify connector_id or machine_name (you have %d online machines)" % len(candidates))
    if not candidates:
        raise HTTPException(404, "Machine not found")
    conn = candidates[0]
    task_id = await dispatch_task(db, user, conn, body.command, None, body.timeout_s, body.cwd, source="inbound_webhook")
    return {"task_id": task_id, "connector_id": str(conn.id), "machine_name": conn.machine_name, "dispatched": True}
