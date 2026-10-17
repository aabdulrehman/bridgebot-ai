"""
Async SQLAlchemy engine/session factory + startup bootstrap.

Works with PostgreSQL (asyncpg; Supabase pooler compatible) and SQLite
(aiosqlite) so the whole platform can run for free on a laptop.

`init_db()`:
  1. create missing tables,
  2. lightweight auto-migration: ALTER TABLE ADD COLUMN for model columns that
     an older database lacks (safe, additive only),
  3. upsert the plan catalogue, seed the Genesis master bot,
  4. bootstrap the admin account (ADMIN_EMAIL + ADMIN_PASSWORD).
"""
from __future__ import annotations

import logging
from typing import AsyncIterator

from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings

settings = get_settings()
log = logging.getLogger("bridgebot.db")

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


def _add_missing_columns(sync_conn) -> None:  # type: ignore[no-untyped-def]
    """Additive migration: add columns present in the models but not in the DB."""
    insp = inspect(sync_conn)
    existing_tables = set(insp.get_table_names())
    dialect = sync_conn.dialect
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in have:
                continue
            ddl = f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(dialect=dialect)}'
            if col.server_default is not None:
                ddl += f" DEFAULT {col.server_default.arg.text}"  # type: ignore[union-attr]
            if not col.nullable and col.server_default is not None:
                ddl += " NOT NULL"
            log.warning("auto-migrate: %s", ddl)
            sync_conn.execute(text(ddl))


async def init_db() -> None:
    """Create/migrate tables and seed reference data."""
    from . import models
    from .billing.plans import PLANS
    from .security import hash_password

    if settings.auto_create_tables:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(_add_missing_columns)

    async with SessionLocal() as session:
        # Plans: upsert from code so pricing changes ship with deploys.
        for p in PLANS:
            row = await session.get(models.Plan, p["id"])
            if row is None:
                session.add(models.Plan(**p))
            else:
                for k, v in p.items():
                    setattr(row, k, v)
        await session.flush()

        if await session.scalar(select(models.MasterBot).where(models.MasterBot.bot_name == "Genesis")) is None:
            session.add(models.MasterBot(
                bot_name="Genesis",
                description="Default platform master bot that compiles niche skill payloads.",
                pricing_tier="FREE",
                skill_payload_json={"system_prompt_style": "expert-operator", "version": 1},
            ))

        # Admin bootstrap (only when both env vars are set).
        if settings.admin_email and settings.admin_password:
            email = settings.admin_email_normalized
            admin = await session.scalar(select(models.User).where(models.User.email == email))
            if admin is None:
                session.add(models.User(email=email, password_hash=hash_password(settings.admin_password),
                                        role="admin", plan_id="BUSINESS", token_balance=500_000))
                log.info("bootstrapped admin account %s", email)
            else:
                admin.role = "admin"
        await session.commit()
