"""
ORM models mirroring db/schema.sql.

Types are chosen to be portable: `Uuid` maps to native UUID on Postgres and
CHAR(32) on SQLite; `JSON().with_variant(JSONB)` gives JSONB on Postgres.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    """Timezone-aware UTC now."""
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("token_balance >= 0", name="users_token_balance_nonneg"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    token_balance: Mapped[int] = mapped_column(Integer, nullable=False, default=10000)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    connectors: Mapped[list["Connector"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Connector(Base):
    __tablename__ = "connectors"
    __table_args__ = (
        CheckConstraint("status IN ('ONLINE','OFFLINE')", name="connectors_status_chk"),
        UniqueConstraint("user_id", "machine_name", name="connectors_user_machine_uq"),
        Index("idx_connectors_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    machine_name: Mapped[str] = mapped_column(String(120), nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="OFFLINE")
    last_heartbeat: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    user: Mapped[User] = relationship(back_populates="connectors")
    agents: Mapped[list["Agent"]] = relationship(back_populates="connector", cascade="all, delete-orphan")


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (UniqueConstraint("connector_id", "agent_name", name="agents_connector_name_uq"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    connector_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("connectors.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_name: Mapped[str] = mapped_column(String(120), nullable=False)
    agent_type: Mapped[str] = mapped_column(String(50), nullable=False, default="generic")
    current_injected_skills_json: Mapped[Any] = mapped_column(JSONType, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    connector: Mapped[Connector] = relationship(back_populates="agents")


class MasterBot(Base):
    __tablename__ = "master_bots"
    __table_args__ = (CheckConstraint("pricing_tier IN ('FREE','STANDARD','PREMIUM')", name="master_bots_tier_chk"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    bot_name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    pricing_tier: Mapped[str] = mapped_column(String(20), nullable=False, default="FREE")
    skill_payload_json: Mapped[Any] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class TrainingRequest(Base):
    __tablename__ = "training_requests"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING','TRAINING','READY','APPROVED','FAILED')", name="training_status_chk"),
        CheckConstraint("token_cost >= 0", name="training_token_cost_nonneg"),
        Index("idx_training_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    master_bot_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("master_bots.id", ondelete="SET NULL"), nullable=True)
    target_connector_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("connectors.id", ondelete="SET NULL"), nullable=True)
    target_agent_name: Mapped[str] = mapped_column(String(120), nullable=False, default="default")
    niche_requirements_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="PENDING")
    token_cost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    generated_payload_json: Mapped[Optional[Any]] = mapped_column(JSONType, nullable=True)
    generator: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("idx_audit_connector_created", "connector_id", "created_at"),
        Index("idx_audit_event_type", "event_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    connector_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("connectors.id", ondelete="CASCADE"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload_json: Mapped[Any] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
