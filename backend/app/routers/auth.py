"""User registration / login (JWT). Free plan auto-assigned; admin role derived from ADMIN_EMAIL."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..billing.service import get_plan, start_period
from ..config import get_settings
from ..database import get_db
from ..deps import assert_account_usable, get_current_user, sync_role
from ..models import User
from ..schemas import LoginIn, RegisterIn, TokenOut, UserOut
from ..security import create_access_token, generate_hook_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()


def _token_out(user: User) -> TokenOut:
    return TokenOut(access_token=create_access_token(user.id, user.email), user_id=user.id, email=user.email,
                    token_balance=user.token_balance, role=user.role, plan_id=user.plan_id)


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterIn, db: AsyncSession = Depends(get_db)) -> TokenOut:
    """Create an account on the FREE plan and return a JWT."""
    email = body.email.strip().lower()
    if await db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    free = await get_plan(db, "FREE")
    user = User(email=email, password_hash=hash_password(body.password), token_balance=free.monthly_tokens,
                plan_id="FREE", inbound_hook_token=generate_hook_token(), last_login_at=datetime.now(timezone.utc))
    sync_role(user)
    await start_period(db, user, free)
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    await db.refresh(user)
    return _token_out(user)


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, db: AsyncSession = Depends(get_db)) -> TokenOut:
    """Exchange email/password for a JWT."""
    user = await db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    assert_account_usable(user)
    sync_role(user)
    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()
    return _token_out(user)


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    """Current user profile (role, plan, live token balance)."""
    return user
