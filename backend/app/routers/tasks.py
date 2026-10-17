"""Remote task dispatch (Service 1) — shared by the REST API and inbound webhooks."""
from __future__ import annotations

import uuid
from typing import List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import write_audit
from ..billing.service import consume_task_quota
from ..config import get_settings
from ..database import get_db
from ..deps import get_current_user
from ..hub import hub
from ..models import Connector, User
from ..schemas import TriggerTaskIn, TriggerTaskOut
from .connectors import get_owned_connector

router = APIRouter(prefix="/api", tags=["tasks"])
settings = get_settings()


async def dispatch_task(db: AsyncSession, user: User, conn: Connector, command: Union[str, List[str]],
                        agent_name: Optional[str] = None, timeout_s: Optional[int] = None,
                        cwd: Optional[str] = None, source: str = "api", task_id: Optional[str] = None) -> str:
    """Enforce quota, send TASK over the bridge, notify dashboards, audit. Returns task_id."""
    if not hub.is_online(conn.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Connector is OFFLINE")
    consume_task_quota(user)
    task_id = task_id or uuid.uuid4().hex
    message = {"type": "TASK", "task_id": task_id, "command": command, "agent_name": agent_name,
               "timeout_s": timeout_s or settings.default_task_timeout_s, "cwd": cwd}
    if not await hub.send_to_connector(conn.id, message):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Failed to reach connector")
    await db.commit()  # persist quota usage only after a successful dispatch
    event = {"type": "TASK_DISPATCHED", "connector_id": str(conn.id), "task_id": task_id, "command": command, "source": source}
    await hub.broadcast_to_user(user.id, event)
    await write_audit(conn.id, "TASK_DISPATCHED", event)
    return task_id


@router.post("/trigger-task", response_model=TriggerTaskOut)
async def trigger_task(body: TriggerTaskIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> TriggerTaskOut:
    """
    Send a TASK to a live connector. Output arrives asynchronously as LOG /
    TASK_RESULT events on /ws/dashboard (and `task.completed` webhooks).
    The connector enforces its own local allowlist before executing anything.
    """
    conn = await get_owned_connector(db, user, body.connector_id)
    task_id = await dispatch_task(db, user, conn, body.command, body.agent_name, body.timeout_s, body.cwd)
    return TriggerTaskOut(task_id=task_id, connector_id=conn.id, dispatched=True)
