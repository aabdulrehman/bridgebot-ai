"""
ORM models mirroring db/schema.sql.

Types are chosen to be portable: `Uuid` maps to native UUID on Postgres and
CHAR(32) on SQLite; `JSON().with_variant(JSONB)` gives JSONB on Postgres.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    """Timezone-aware UTC now."""
    return datetime.now(timezone.utc)


class Plan(Base):
    """Subscription plan catalogue (seeded from app/billing/plans.py)."""

    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)          # FREE / PRO / BUSINESS
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    price_monthly_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    monthly_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_machines: Mapped[int] = mapped_column(Integer, nullable=False, default=1)       # -1 = unlimited
    max_tasks_per_day: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 0 = no access, -1 = unlimited
    max_webhooks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_api_keys: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    training_access: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    inbound_webhooks: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    max_bots: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))  # Bot API / marketplace bots
    features_json: Mapped[Any] = mapped_column(JSONType, nullable=False, default=list)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("token_balance >= 0", name="users_token_balance_nonneg"),
        CheckConstraint("role IN ('user','admin')", name="users_role_chk"),
        CheckConstraint("status IN ('ACTIVE','SUSPENDED','BANNED')", name="users_status_chk"),
        CheckConstraint("subscription_status IN ('ACTIVE','PENDING_UPGRADE','PAST_DUE','CANCELED')", name="users_sub_status_chk"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    token_balance: Mapped[int] = mapped_column(Integer, nullable=False, default=10000)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    # --- account / subscription (server_default so ALTER TABLE ADD works on existing rows)
    role: Mapped[str] = mapped_column(String(10), nullable=False, default="user", server_default=text("'user'"))
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="ACTIVE", server_default=text("'ACTIVE'"))
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"), nullable=False, default="FREE", server_default=text("'FREE'"), index=True)
    subscription_status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE", server_default=text("'ACTIVE'"))
    current_period_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    current_period_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    tokens_used_period: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    tasks_today: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    tasks_day: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)          # YYYY-MM-DD (UTC)
    inbound_hook_token: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)
    billing_customer_ref: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)  # Stripe/Paddle customer id
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    plan: Mapped[Plan] = relationship(lazy="joined")
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


class UpgradeRequest(Base):
    """A user's request to change plan; approved by an admin or a payment provider webhook."""

    __tablename__ = "upgrade_requests"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING','APPROVED','REJECTED','CANCELED')", name="upgrade_status_chk"),
        Index("idx_upgrade_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    from_plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"), nullable=False)
    requested_plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="PENDING")
    payment_provider: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    payment_reference: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    months: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    user_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    admin_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    decided_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(foreign_keys=[user_id], lazy="joined")


class Payment(Base):
    """Recorded revenue (manual approval or provider webhook)."""

    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"), nullable=False)
    upgrade_request_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("upgrade_requests.id", ondelete="SET NULL"), nullable=True)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    reference: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="SUCCEEDED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)


class ApiKey(Base):
    """Personal REST API key (raw key shown once; SHA-256 stored)."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Webhook(Base):
    """Outbound webhook endpoint registered by a user."""

    __tablename__ = "webhooks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    events_json: Mapped[Any] = mapped_column(JSONType, nullable=False, default=list)
    secret: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class WebhookDelivery(Base):
    """One attempt-tracked delivery of an event to a webhook."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING','SUCCESS','FAILED')", name="delivery_status_chk"),
        Index("idx_delivery_pending", "status", "next_attempt_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    webhook_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False, index=True)
    event: Mapped[str] = mapped_column(String(50), nullable=False)
    payload_json: Mapped[Any] = mapped_column(JSONType, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    response_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    response_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


# =============================================================================
# Universal Bot Bridge + Master Bot Marketplace
# =============================================================================
class Bot(Base):
    """Any agent bot linked to the platform: via the local connector or the generic Bot API."""

    __tablename__ = "bots"
    __table_args__ = (
        CheckConstraint("delivery IN ('poll','webhook','websocket','connector')", name="bots_delivery_chk"),
        CheckConstraint("status IN ('ACTIVE','DISABLED')", name="bots_status_chk"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    framework: Mapped[str] = mapped_column(String(30), nullable=False, default="custom")   # hermes, openclaw, n8n, langchain, http, grok, custom
    delivery: Mapped[str] = mapped_column(String(12), nullable=False, default="poll")
    webhook_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    connector_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("connectors.id", ondelete="SET NULL"), nullable=True)
    agent_name: Mapped[str] = mapped_column(String(120), nullable=False, default="default")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    secret: Mapped[str] = mapped_column(String(80), nullable=False)                       # signs webhook pushes
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="ACTIVE")
    persona: Mapped[Optional[str]] = mapped_column(Text, nullable=True)                   # set by "Make my bot a master"
    skills_json: Mapped[Any] = mapped_column(JSONType, nullable=False, default=list)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class BotMessage(Base):
    """Bot inbox/outbox. direction 'in' = to the bot (polled as updates), 'out' = posted by the bot."""

    __tablename__ = "bot_messages"
    __table_args__ = (
        CheckConstraint("direction IN ('in','out')", name="bot_msg_direction_chk"),
        Index("idx_bot_msg_poll", "bot_id", "direction", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)        # monotonically increasing => poll offset
    bot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(3), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="message")     # message, welcome, skill_inject, task, system
    chat: Mapped[str] = mapped_column(String(10), nullable=False, default="direct")      # direct (owner) | group
    group_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("marketplace_masters.id", ondelete="SET NULL"), nullable=True)
    sender: Mapped[str] = mapped_column(String(160), nullable=False, default="owner")
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload_json: Mapped[Optional[Any]] = mapped_column(JSONType, nullable=True)
    reply_to: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    acked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class MarketplaceMaster(Base):
    """A Master Bot published by the platform owner: skill payload + persona + tools + its messenger group."""

    __tablename__ = "marketplace_masters"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    avatar: Mapped[str] = mapped_column(String(16), nullable=False, default="🤖")
    tagline: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    persona: Mapped[str] = mapped_column(Text, nullable=False, default="")
    skill_payload_json: Mapped[Any] = mapped_column(JSONType, nullable=False, default=dict)
    tools_json: Mapped[Any] = mapped_column(JSONType, nullable=False, default=list)
    welcome_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    test_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    test_expect: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)      # keyword the reply must contain
    min_plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"), nullable=False, default="FREE")
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class GroupMember(Base):
    """A bot inside a Master Bot's messenger group + its automated onboarding state."""

    __tablename__ = "master_group_members"
    __table_args__ = (
        UniqueConstraint("master_id", "bot_id", name="uq_group_member"),
        CheckConstraint("status IN ('JOINED','WAITING','INJECTING','INJECTED','TESTING','GRADUATED','FAILED')", name="group_member_status_chk"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    master_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("marketplace_masters.id", ondelete="CASCADE"), nullable=False, index=True)
    bot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="JOINED")
    is_master_clone: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    skill_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    inject_ref: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)        # bot_message id or SKILL_INJECT request_id
    test_ref: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)          # bot_message id or task_id
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    injected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    graduated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class GroupMessage(Base):
    """The messenger-group feed of a Master Bot."""

    __tablename__ = "master_group_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    master_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("marketplace_masters.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_type: Mapped[str] = mapped_column(String(10), nullable=False)                # master | bot | system
    sender_bot_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("bots.id", ondelete="SET NULL"), nullable=True)
    sender_name: Mapped[str] = mapped_column(String(160), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
