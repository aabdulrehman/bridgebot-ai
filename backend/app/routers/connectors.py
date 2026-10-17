"""Connector (local machine) management."""
from __future__ import annotations

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..billing.service import enforce_machine_limit
from ..database import get_db
from ..deps import get_current_user
from ..hub import hub, kick
from ..models import AuditLog, Connector, User
from ..schemas import AuditOut, ConnectorCreatedOut, ConnectorCreateIn, ConnectorOut
from ..security import generate_api_key, hash_api_key

router = APIRouter(prefix="/api/connectors", tags=["connectors"])


async def get_owned_connector(db: AsyncSession, user: User, connector_id: uuid.UUID) -> Connector:
    """Load a connector owned by `user` (with agents) or raise 404."""
    conn = await db.scalar(
        select(Connector).options(selectinload(Connector.agents)).where(Connector.id == connector_id, Connector.user_id == user.id)
    )
    if conn is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connector not found")
    return conn


@router.post("", response_model=ConnectorCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_connector(body: ConnectorCreateIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> ConnectorCreatedOut:
    """Register a machine (plan machine limit enforced). The raw API key is returned ONLY here."""
    await enforce_machine_limit(db, user)
    raw_key = generate_api_key()
    conn = Connector(user_id=user.id, machine_name=body.machine_name.strip(), api_key_hash=hash_api_key(raw_key), status="OFFLINE")
    db.add(conn)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "You already have a connector with that machine name")
    await db.refresh(conn, attribute_names=["agents"])
    return ConnectorCreatedOut(**ConnectorOut.model_validate(conn).model_dump(), api_key=raw_key)


@router.get("", response_model=List[ConnectorOut])
async def list_connectors(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[Connector]:
    """All of the user's machines with live status and agents."""
    rows = await db.scalars(
        select(Connector).options(selectinload(Connector.agents)).where(Connector.user_id == user.id).order_by(Connector.created_at)
    )
    result = list(rows)
    for c in result:  # the in-memory pool is the source of truth for liveness
        c.status = "ONLINE" if hub.is_online(c.id) else "OFFLINE"
    return result


@router.post("/{connector_id}/rotate-key", response_model=ConnectorCreatedOut)
async def rotate_key(connector_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> ConnectorCreatedOut:
    """Issue a new API key (old key stops working immediately)."""
    conn = await get_owned_connector(db, user, connector_id)
    raw_key = generate_api_key()
    conn.api_key_hash = hash_api_key(raw_key)
    await db.commit()
    session = hub.connectors.get(conn.id)
    if session:
        await kick(session.websocket, 4001, "API key rotated")
    return ConnectorCreatedOut(**ConnectorOut.model_validate(conn).model_dump(), api_key=raw_key)


@router.delete("/{connector_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connector(connector_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> None:
    """Delete a connector and disconnect it."""
    conn = await get_owned_connector(db, user, connector_id)
    session = hub.connectors.get(conn.id)
    if session:
        await kick(session.websocket, 4002, "Connector deleted")
    await db.delete(conn)
    await db.commit()


@router.get("/{connector_id}/audit", response_model=List[AuditOut])
async def connector_audit(
    connector_id: uuid.UUID,
    limit: int = Query(100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[AuditLog]:
    """Recent audit events for a connector (newest first)."""
    await get_owned_connector(db, user, connector_id)
    rows = await db.scalars(select(AuditLog).where(AuditLog.connector_id == connector_id).order_by(AuditLog.created_at.desc()).limit(limit))
    return list(rows)
