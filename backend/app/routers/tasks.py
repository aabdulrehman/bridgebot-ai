"""Remote task dispatch (Service 1)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import write_audit
from ..config import get_settings
from ..database import get_db
from ..deps import get_current_user
from ..hub import hub
from ..models import User
from ..schemas import TriggerTaskIn, TriggerTaskOut
from .connectors import get_owned_connector

router = APIRouter(prefix="/api", tags=["tasks"])
settings = get_settings()


@router.post("/trigger-task", response_model=TriggerTaskOut)
async def trigger_task(body: TriggerTaskIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> TriggerTaskOut:
    """
    Send a TASK to a live connector. Output arrives asynchronously as LOG /
    TASK_RESULT events on the dashboard WebSocket (/ws/dashboard).
    The connector enforces its own local allowlist before executing anything.
    """
    conn = await get_owned_connector(db, user, body.connector_id)
    if not hub.is_online(conn.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Connector is OFFLINE")
    task_id = uuid.uuid4().hex
    message = {
        "type": "TASK",
        "task_id": task_id,
        "command": body.command,
        "agent_name": body.agent_name,
        "timeout_s": body.timeout_s or settings.default_task_timeout_s,
        "cwd": body.cwd,
    }
    if not await hub.send_to_connector(conn.id, message):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Failed to reach connector")
    event = {"type": "TASK_DISPATCHED", "connector_id": str(conn.id), "task_id": task_id, "command": body.command}
    await hub.broadcast_to_user(user.id, event)
    await write_audit(conn.id, "TASK_DISPATCHED", event)
    return TriggerTaskOut(task_id=task_id, connector_id=conn.id, dispatched=True)
