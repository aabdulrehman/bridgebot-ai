"""On-Demand Training & Skill Sync (Service 2)."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import webhooks
from ..audit import write_audit
from ..billing.service import require_training
from ..config import get_settings
from ..database import SessionLocal, get_db
from ..deps import get_current_user
from ..hub import hub
from ..master_bot import generate_skill_payload
from ..models import MasterBot, TrainingRequest, User
from ..schemas import TrainingApproveIn, TrainingOut, TrainingSubmitIn
from .connectors import get_owned_connector

router = APIRouter(prefix="/api/training", tags=["training"])
settings = get_settings()
log = logging.getLogger("bridgebot.training")


async def _run_training(request_id: uuid.UUID, business_name: Optional[str], goals: List[str], tone: Optional[str]) -> None:
    """Background job: PENDING -> TRAINING -> READY (or FAILED). Charges platform tokens."""
    async with SessionLocal() as db:
        tr = await db.get(TrainingRequest, request_id)
        if tr is None:
            return
        tr.status = "TRAINING"
        await db.commit()
        await hub.broadcast_to_user(tr.user_id, {"type": "TRAINING_STATUS", "training_request_id": str(tr.id), "status": "TRAINING"})
        try:
            payload, generator, cost = await generate_skill_payload(tr.niche_requirements_text, business_name, goals, tone)
            user = await db.get(User, tr.user_id)
            charged = min(cost, user.token_balance) if user else 0
            if user:
                user.token_balance -= charged
                user.tokens_used_period = (user.tokens_used_period or 0) + charged
            tr.generated_payload_json = payload
            tr.generator = generator
            tr.token_cost = charged
            tr.status = "READY"
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            log.exception("training %s failed", request_id)
            await db.rollback()
            tr = await db.get(TrainingRequest, request_id)
            if tr:
                tr.status = "FAILED"
                tr.error_message = str(exc)[:1000]
                await db.commit()
        if tr:
            await hub.broadcast_to_user(tr.user_id, {"type": "TRAINING_STATUS", "training_request_id": str(tr.id), "status": tr.status})
            if tr.status == "READY":
                await webhooks.emit(tr.user_id, "training.ready", {
                    "training_request_id": str(tr.id), "skill_id": (tr.generated_payload_json or {}).get("skill_id"),
                    "name": (tr.generated_payload_json or {}).get("name"), "token_cost": tr.token_cost, "generator": tr.generator})


@router.post("/submit", response_model=TrainingOut, status_code=status.HTTP_202_ACCEPTED)
async def submit_training(
    body: TrainingSubmitIn,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TrainingRequest:
    """Queue a niche for Master Bot compilation. Poll GET /api/training/{id} or watch /ws/dashboard."""
    require_training(user)
    if user.token_balance < settings.min_training_cost:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "Insufficient platform token balance — monthly allowance used up. Upgrade or wait for renewal.")
    if body.target_connector_id:
        await get_owned_connector(db, user, body.target_connector_id)
    genesis = await db.scalar(select(MasterBot).where(MasterBot.bot_name == "Genesis"))
    tr = TrainingRequest(
        user_id=user.id,
        master_bot_id=genesis.id if genesis else None,
        target_connector_id=body.target_connector_id,
        target_agent_name=body.target_agent_name or "default",
        niche_requirements_text=body.niche_requirements_text.strip(),
        status="PENDING",
    )
    db.add(tr)
    await db.commit()
    await db.refresh(tr)
    background.add_task(_run_training, tr.id, body.business_name, body.goals, body.tone)
    return tr


@router.get("", response_model=List[TrainingOut])
async def list_training(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[TrainingRequest]:
    rows = await db.scalars(select(TrainingRequest).where(TrainingRequest.user_id == user.id).order_by(TrainingRequest.created_at.desc()).limit(50))
    return list(rows)


@router.get("/{request_id}", response_model=TrainingOut)
async def get_training(request_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> TrainingRequest:
    tr = await db.get(TrainingRequest, request_id)
    if tr is None or tr.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Training request not found")
    return tr


@router.post("/approve", response_model=TrainingOut)
async def approve_training(body: TrainingApproveIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> TrainingRequest:
    """Approve a READY payload and inject it (SKILL_INJECT) into the target connector."""
    tr = await db.get(TrainingRequest, body.training_request_id)
    if tr is None or tr.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Training request not found")
    if tr.status not in ("READY", "APPROVED"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Request is {tr.status}; must be READY")
    connector_id = body.target_connector_id or tr.target_connector_id
    if connector_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "target_connector_id is required")
    conn = await get_owned_connector(db, user, connector_id)
    if not hub.is_online(conn.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Target connector is OFFLINE — start local_connector.py first")
    agent_name = body.target_agent_name or tr.target_agent_name or "default"
    message = {
        "type": "SKILL_INJECT",
        "request_id": str(tr.id),
        "agent_name": agent_name,
        "payload": tr.generated_payload_json,
    }
    if not await hub.send_to_connector(conn.id, message):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Failed to push payload to connector")
    tr.status = "APPROVED"
    tr.target_connector_id = conn.id
    tr.target_agent_name = agent_name
    tr.approved_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(tr)
    skill_id = (tr.generated_payload_json or {}).get("skill_id")
    await write_audit(conn.id, "SKILL_INJECT_SENT", {"request_id": str(tr.id), "agent_name": agent_name, "skill_id": skill_id})
    await hub.broadcast_to_user(user.id, {"type": "SKILL_INJECT_SENT", "connector_id": str(conn.id), "training_request_id": str(tr.id), "agent_name": agent_name, "skill_id": skill_id})
    return tr
