"""Personal REST API keys (bbu_...). Use as `Authorization: Bearer bbu_...` or `X-API-Key`."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..billing.service import enforce_api_key_limit
from ..database import get_db
from ..deps import get_current_user
from ..models import ApiKey, User
from ..schemas import ApiKeyCreatedOut, ApiKeyIn, ApiKeyOut
from ..security import generate_user_api_key, hash_api_key

router = APIRouter(prefix="/api/api-keys", tags=["api-keys"])


@router.get("", response_model=List[ApiKeyOut])
async def list_keys(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[ApiKey]:
    return list(await db.scalars(select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at.desc())))


@router.post("", response_model=ApiKeyCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_key(body: ApiKeyIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> ApiKeyCreatedOut:
    """Create a key (plan limit enforced). The raw key is returned ONLY in this response."""
    await enforce_api_key_limit(db, user)
    raw = generate_user_api_key()
    key = ApiKey(user_id=user.id, name=body.name.strip(), prefix=raw[:12], key_hash=hash_api_key(raw))
    db.add(key)
    await db.commit()
    await db.refresh(key)
    return ApiKeyCreatedOut(**ApiKeyOut.model_validate(key).model_dump(), api_key=raw)


@router.delete("/{key_id}", response_model=ApiKeyOut)
async def revoke_key(key_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> ApiKey:
    key = await db.get(ApiKey, key_id)
    if key is None or key.user_id != user.id:
        raise HTTPException(404, "API key not found")
    key.revoked_at = key.revoked_at or datetime.now(timezone.utc)
    await db.commit()
    return key
