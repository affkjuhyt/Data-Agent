from __future__ import annotations

import datetime
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentCreate(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=500)
    kind: Literal["analysis", "monitor", "report", "reconcile", "data_prep", "data_quality", "document", "custom"]
    account_user_id: uuid.UUID


class AgentView(BaseModel):
    id: uuid.UUID
    data_space_id: uuid.UUID
    code: str
    name: str
    kind: str
    owner_user_id: uuid.UUID
    account_user_id: uuid.UUID
    current_config_version: int | None
    status: str


class ConfigCreate(BaseModel):
    goal: str = Field(min_length=1, max_length=4_000)
    data_scope: dict[str, Any] = Field(default_factory=dict)
    autonomy: Literal["suggest_only", "propose_approve", "act_then_report"] = "propose_approve"
    checkpoints: list[Any] = Field(default_factory=list)
    denied_actions: list[str] = Field(default_factory=list)
    limits: dict[str, Any] = Field(default_factory=dict)
    model_by_task: dict[str, Any] = Field(default_factory=dict)
    change_note: str | None = None


class ConfigView(ConfigCreate):
    id: uuid.UUID
    agent_id: uuid.UUID
    version: int
    status: str


class TriggerCreate(BaseModel):
    kind: Literal["schedule", "event", "threshold", "agent_call", "manual"]
    cron: str | None = None
    event_type: str | None = None
    condition: dict[str, Any] = Field(default_factory=dict)
    combine: Literal["any", "all"] = "any"
    enabled: bool = True


class RunView(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    config_version: int
    data_space_id: uuid.UUID
    trigger_kind: str
    status: str
    current_step: int | None
    narrative: Any
    is_test: bool
    stop_reason: str | None
    error: str | None


class RunPlan(BaseModel):
    plan: dict[str, Any]


class MemoryCreate(BaseModel):
    scope: Literal["agent", "user", "data_space"]
    kind: Literal["decision", "preference", "data_insight", "domain_context"]
    content: str = Field(min_length=1, max_length=8_000)
    source_kind: Literal["hitl", "run", "manual", "pack"] = "manual"
    agent_id: uuid.UUID | None = None
    expires_at: datetime.datetime | None = None


class MemoryPatch(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=8_000)
    status: Literal["pending_review", "active", "disabled", "conflict"] | None = None


class MemoryView(MemoryCreate):
    id: uuid.UUID
    data_space_id: uuid.UUID
    user_id: uuid.UUID | None
    status: str
