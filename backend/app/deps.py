"""
Shared FastAPI dependencies.

Authentication accepts either
  * a dashboard JWT                 (`Authorization: Bearer eyJ...`), or
  * a personal REST API key         (`Authorization: Bearer bbu_...` or `X-API-Key: bbu_...`).
Admin endpoints require a JWT session (API keys can never act as admin).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .billing.service import refresh_subscription
from .config import get_settings
from .database import get_db
from .models import ApiKey, User
from .security import USER_API_KEY_PREFIX, decode_access_token, hash_api_key

_bearer = HTTPBearer(auto_error=False)
settings = get_settings()


def sync_role(user: User) -> bool:
    """Role is derived from ADMIN_EMAIL only. Returns True if it changed."""
    desired = "admin" if settings.is_admin_email(user.email) else "user"
    if user.role != desired:
        user.role = desired
        return True
    return False


def assert_account_usable(user: User) -> None:
    if user.status == "BANNED":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account banned. Contact support.")
    if user.status == "SUSPENDED":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account suspended. Contact support on WhatsApp.")


async def _user_from_api_key(db: AsyncSession, raw: str) -> User | None:
    key = await db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_api_key(raw), ApiKey.revoked_at.is_(None)))
    if key is None:
        return None
    key.last_used_at = datetime.now(timezone.utc)
    return await db.get(User, key.user_id)


async def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve JWT or personal API key to an active User (or raise 401/403)."""
    raw = (creds.credentials if creds else None) or request.headers.get("x-api-key")
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token or API key")
    user: User | None
    if raw.startswith(USER_API_KEY_PREFIX):
        user = await _user_from_api_key(db, raw)
        request.state.auth_method = "api_key"
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or revoked API key")
    else:
        user_id = decode_access_token(raw)
        if user_id is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
        user = await db.get(User, user_id)
        request.state.auth_method = "jwt"
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User no longer exists")
    assert_account_usable(user)
    changed = sync_role(user)
    changed = await refresh_subscription(db, user) or changed
    if changed or request.state.auth_method == "api_key":
        await db.commit()
    return user


async def require_admin(request: Request, user: User = Depends(get_current_user)) -> User:
    """Admin-only guard (JWT session + role admin)."""
    if user.role != "admin" or getattr(request.state, "auth_method", "jwt") != "jwt":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only")
    return user
