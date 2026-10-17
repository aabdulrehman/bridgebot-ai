"""Pydantic request/response models."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: uuid.UUID
    email: str
    token_balance: int
    role: str = "user"
    plan_id: str = "FREE"


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    price_monthly_cents: int
    currency: str
    monthly_tokens: int
    max_machines: int
    max_tasks_per_day: int
    max_webhooks: int
    max_api_keys: int
    training_access: bool
    inbound_webhooks: bool
    features_json: Any
    sort_order: int


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    token_balance: int
    created_at: datetime
    role: str
    status: str
    plan_id: str
    subscription_status: str
    current_period_start: Optional[datetime] = None
    current_period_end: Optional[datetime] = None
    tokens_used_period: int = 0
    last_login_at: Optional[datetime] = None
    plan: Optional[PlanOut] = None


class UpgradeRequestIn(BaseModel):
    plan_id: str = Field(pattern="^(FREE|PRO|BUSINESS)$")
    # How the user paid / will pay: "paypal" (PayPal.Me, transaction id in payment_reference) or "manual" (WhatsApp/bank).
    payment_method: str = Field(default="manual", pattern="^(manual|paypal)$")
    months: int = Field(default=1, ge=1, le=24)
    payment_reference: Optional[str] = Field(default=None, max_length=200)
    note: Optional[str] = Field(default=None, max_length=2000)


class UpgradeRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    user_id: uuid.UUID
    from_plan_id: str
    requested_plan_id: str
    status: str
    payment_provider: str
    payment_reference: Optional[str]
    amount_cents: int
    months: int
    user_note: Optional[str]
    admin_note: Optional[str]
    created_at: datetime
    decided_at: Optional[datetime]
    user_email: Optional[str] = None


class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    prefix: str
    created_at: datetime
    last_used_at: Optional[datetime]
    revoked_at: Optional[datetime]


class ApiKeyCreatedOut(ApiKeyOut):
    api_key: str


class WebhookIn(BaseModel):
    url: str = Field(min_length=8, max_length=1000)
    events: List[str] = Field(min_length=1)
    description: Optional[str] = Field(default=None, max_length=200)
    is_active: bool = True


class WebhookUpdateIn(BaseModel):
    url: Optional[str] = Field(default=None, min_length=8, max_length=1000)
    events: Optional[List[str]] = None
    description: Optional[str] = Field(default=None, max_length=200)
    is_active: Optional[bool] = None


class WebhookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    url: str
    events_json: Any
    description: Optional[str]
    is_active: bool
    created_at: datetime
    secret: str


class DeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    webhook_id: uuid.UUID
    event: str
    status: str
    attempts: int
    response_code: Optional[int]
    response_body: Optional[str]
    error: Optional[str]
    payload_json: Any
    created_at: datetime
    delivered_at: Optional[datetime]
    next_attempt_at: Optional[datetime]


class InboundHookIn(BaseModel):
    """Body for POST /api/hooks/in/{token}."""
    command: Union[str, List[str]]
    connector_id: Optional[uuid.UUID] = None
    machine_name: Optional[str] = None
    timeout_s: Optional[int] = Field(default=None, ge=1, le=3600)
    cwd: Optional[str] = None


class AdminUserPatch(BaseModel):
    plan_id: Optional[str] = Field(default=None, pattern="^(FREE|PRO|BUSINESS)$")
    months: int = Field(default=1, ge=1, le=36)
    status: Optional[str] = Field(default=None, pattern="^(ACTIVE|SUSPENDED|BANNED)$")
    token_adjust: Optional[int] = Field(default=None, ge=-10_000_000, le=10_000_000)
    note: Optional[str] = Field(default=None, max_length=500)


class AdminDecisionIn(BaseModel):
    note: Optional[str] = Field(default=None, max_length=1000)


class ConnectorCreateIn(BaseModel):
    machine_name: str = Field(min_length=1, max_length=120)


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    agent_name: str
    agent_type: str
    current_injected_skills_json: Any


class ConnectorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    machine_name: str
    status: Literal["ONLINE", "OFFLINE"]
    last_heartbeat: Optional[datetime]
    created_at: datetime
    agents: List[AgentOut] = []


class ConnectorCreatedOut(ConnectorOut):
    api_key: str = Field(description="Raw API key — shown ONCE, store it in the connector config.")


class TrainingSubmitIn(BaseModel):
    niche_requirements_text: str = Field(min_length=10, max_length=8000)
    business_name: Optional[str] = Field(default=None, max_length=120)
    goals: List[str] = Field(default_factory=list, max_length=20)
    tone: Optional[str] = Field(default=None, max_length=60)
    target_connector_id: Optional[uuid.UUID] = None
    target_agent_name: str = Field(default="default", max_length=120)


class TrainingApproveIn(BaseModel):
    training_request_id: uuid.UUID
    target_connector_id: Optional[uuid.UUID] = None
    target_agent_name: Optional[str] = Field(default=None, max_length=120)


class TrainingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    status: str
    token_cost: int
    niche_requirements_text: str
    target_connector_id: Optional[uuid.UUID]
    target_agent_name: str
    generated_payload_json: Optional[Any]
    generator: Optional[str]
    error_message: Optional[str]
    created_at: datetime
    approved_at: Optional[datetime]


class TriggerTaskIn(BaseModel):
    connector_id: uuid.UUID
    command: Union[str, List[str]] = Field(description="Command string or argv list executed on the local machine.")
    agent_name: Optional[str] = None
    timeout_s: Optional[int] = Field(default=None, ge=1, le=3600)
    cwd: Optional[str] = None


class TriggerTaskOut(BaseModel):
    task_id: str
    connector_id: uuid.UUID
    dispatched: bool


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    connector_id: Optional[uuid.UUID]
    event_type: str
    payload_json: Any
    created_at: datetime
