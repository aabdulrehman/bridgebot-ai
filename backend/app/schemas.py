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


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    token_balance: int
    created_at: datetime


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
