"""
Async SQLAlchemy engine/session factory.

Works with PostgreSQL (asyncpg; Supabase pooler compatible) and SQLite
(aiosqlite) so the whole platform can run for free on a laptop.
"""
from __future__ import annotations

from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings

settings = get_settings()

_connect_args: dict = {}
if settings.is_sqlite:
    _connect_args = {"check_same_thread": False}
else:
    # Supabase's transaction pooler (port 6543 / PgBouncer) does not support
    # prepared statements; disabling asyncpg's cache keeps both modes working.
    _connect_args = {"statement_cache_size": 0}

engine = create_async_engine(
    settings.database_url,
    connect_args=_connect_args,
    pool_pre_ping=not settings.is_sqlite,
    future=True,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session that is always closed."""
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """Create tables (if enabled) and seed the default master bot."""
    from . import models  # noqa: F401  (register models on metadata)

    if settings.auto_create_tables:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    from sqlalchemy import select

    async with SessionLocal() as session:
        existing = await session.scalar(select(models.MasterBot).where(models.MasterBot.bot_name == "Genesis"))
        if existing is None:
            session.add(
                models.MasterBot(
                    bot_name="Genesis",
                    description="Default platform master bot that compiles niche skill payloads.",
                    pricing_tier="FREE",
                    skill_payload_json={"system_prompt_style": "expert-operator", "version": 1},
                )
            )
            await session.commit()
