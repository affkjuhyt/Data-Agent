"""Request and response shapes of the Agent group: body fields as tab 4 A3 names them, types from the
tab 3b D4 column of the same name; responses are the agent.* rows minus org_id, created_by,
updated_by and deleted_at."""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AgentKind = Literal[
    "analysis", "monitor", "report", "reconcile", "data_prep", "data_quality", "document", "custom"
]
Autonomy = Literal["suggest_only", "propose_approve", "act_then_report"]
PermissionLevel = Literal["read", "write_derived", "send_external"]
MemoryScope = Literal["agent", "user", "data_space"]
MemoryKind = Literal["decision", "preference", "data_insight", "domain_context"]
MemoryStatus = Literal["pending_review", "active", "disabled", "conflict"]
RunStatus = Literal[
    "queued", "planning", "running", "waiting_human", "paused", "succeeded", "failed", "cancelled"
]


class _Row(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ToolIn(BaseModel):
    tool: str = Field(min_length=1, max_length=200)
    permission_level: PermissionLevel
    requires_checkpoint: bool = False
    allowed_targets: list[uuid.UUID] | None = None


class ToolView(_Row):
    id: uuid.UUID
    tool: str
    permission_level: str
    requires_checkpoint: bool
    allowed_targets: list[uuid.UUID] | None


class ConfigCreate(BaseModel):
    goal: str = Field(min_length=1, max_length=4_000)
    data_scope: dict[str, Any] = Field(default_factory=dict)
    skill_ids: list[uuid.UUID] = Field(default_factory=list)
    autonomy: Autonomy = "propose_approve"
    checkpoints: list[Any] = Field(default_factory=list)
    denied_actions: list[str] = Field(default_factory=list)
    limits: dict[str, Any] = Field(default_factory=dict)
    model_by_task: dict[str, Any] = Field(default_factory=dict)
    change_note: str | None = Field(default=None, max_length=4_000)
    tools: list[ToolIn] = Field(default_factory=list)


class ConfigView(_Row):
    id: uuid.UUID
    agent_id: uuid.UUID
    version: int
    goal: str
    data_scope: Any
    skill_ids: list[uuid.UUID]
    autonomy: str
    checkpoints: Any
    denied_actions: list[str]
    limits: Any
    model_by_task: Any
    change_note: str | None
    status: str
    eval_run_id: uuid.UUID | None
    created_at: datetime.datetime
    tools: list[ToolView] = Field(default_factory=list)


class ConfigDraft(BaseModel):
    """POST /v1/agents:draft-from-text: an AgentConfig proposal, not saved."""

    goal: str
    data_scope: dict[str, Any]
    skill_ids: list[uuid.UUID]
    autonomy: str
    checkpoints: list[Any]
    denied_actions: list[str]
    limits: dict[str, Any]
    model_by_task: dict[str, Any]
    tools: list[ToolIn]
    template_ref: uuid.UUID | None
    # Parts of the proposal beyond the owner's rights (UC-13-01 step 3); they cannot be saved.
    out_of_scope: list[str]


class DraftRequest(BaseModel):
    description: str = Field(min_length=1, max_length=4_000)
    template_ref: uuid.UUID | None = None


class AgentCreate(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=500)
    kind: AgentKind
    template_ref: uuid.UUID | None = None
    monthly_budget: decimal.Decimal | None = Field(
        default=None, ge=0, max_digits=14, decimal_places=2
    )
    config: ConfigCreate | None = None


class DeploymentView(_Row):
    id: uuid.UUID
    agent_id: uuid.UUID
    config_version: int
    previous_version: int | None
    traffic_pct: int
    gates: Any
    observed: Any
    status: str
    rollback_reason: str | None
    approved_by: uuid.UUID | None
    hitl_request_id: uuid.UUID | None
    started_at: datetime.datetime | None
    finished_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class AgentSummary(_Row):
    id: uuid.UUID
    data_space_id: uuid.UUID
    code: str
    name: str
    kind: str
    owner_user_id: uuid.UUID
    account_user_id: uuid.UUID
    template_ref: uuid.UUID | None
    current_config_version: int | None
    status: str
    paused_reason: str | None
    monthly_budget: decimal.Decimal | None
    spent_this_month: decimal.Decimal
    created_at: datetime.datetime
    updated_at: datetime.datetime
    # GET /v1/agents: runs in progress and runs waiting for a person (A3).
    active_runs: int = 0
    waiting_runs: int = 0


class AgentView(AgentSummary):
    current_config: ConfigView | None = None
    active_deployment: DeploymentView | None = None


class TriggerIn(BaseModel):
    kind: Literal["schedule", "event", "threshold", "agent_call", "manual"]
    cron: str | None = Field(default=None, max_length=200)
    work_calendar_id: uuid.UUID | None = None
    event_type: str | None = Field(default=None, max_length=200)
    condition: dict[str, Any] = Field(default_factory=dict)
    min_interval: datetime.timedelta | None = None
    no_overlap: bool = True
    combine: Literal["any", "all"] = "any"
    enabled: bool = True


class TriggersPut(BaseModel):
    triggers: list[TriggerIn] = Field(max_length=50)


class TriggerView(_Row):
    id: uuid.UUID
    agent_id: uuid.UUID
    kind: str
    cron: str | None
    work_calendar_id: uuid.UUID | None
    event_type: str | None
    condition: Any
    min_interval: datetime.timedelta | None
    no_overlap: bool
    combine: str
    enabled: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime
    # UC-13-03 step 3: the next 5 fire times of a schedule; for event and threshold triggers the
    # 30-day match count, which needs the event history (null until the trigger worker reports it).
    next_runs: list[datetime.datetime] = Field(default_factory=list)
    matches_30d: int | None = None
    warning: str | None = None


class TrialRunCreate(BaseModel):
    config_version: int | None = Field(default=None, ge=1)
    as_of: datetime.date | None = None


class EvalRunCreate(BaseModel):
    config_version: int | None = Field(default=None, ge=1)


class RunAccepted(BaseModel):
    """A1.6: the body of a 202, next to the Location header."""

    id: uuid.UUID
    status: str
    poll_after_ms: int


class DeploymentCreate(BaseModel):
    config_version: int = Field(ge=1)
    traffic_pct: int = Field(default=10, ge=0, le=100)
    gates: dict[str, Any] = Field(default_factory=dict)


class DeploymentPatch(BaseModel):
    traffic_pct: int = Field(ge=0, le=100)


class DeploymentRollback(BaseModel):
    reason: str = Field(min_length=1, max_length=2_000)
    to_version: int | None = Field(default=None, ge=1)


class StepView(_Row):
    id: uuid.UUID
    seq: int
    kind: str
    name: str
    input: Any | None
    output: Any | None
    summary: str | None
    status: str
    attempt: int
    child_run_id: uuid.UUID | None
    cost: decimal.Decimal
    started_at: datetime.datetime
    duration_ms: int | None


class RunSummary(_Row):
    id: uuid.UUID
    agent_id: uuid.UUID
    config_version: int
    data_space_id: uuid.UUID
    trigger_kind: str
    trigger_ref: str | None
    initiated_by: uuid.UUID | None
    parent_run_id: uuid.UUID | None
    depth: int
    is_test: bool
    as_of: datetime.date | None
    status: str
    current_step: int | None
    cost: decimal.Decimal
    tokens: int
    waiting_since: datetime.datetime | None
    started_at: datetime.datetime | None
    finished_at: datetime.datetime | None
    stop_reason: str | None
    created_at: datetime.datetime


class RunView(RunSummary):
    workflow_id: str | None
    narrative: Any
    error: str | None
    stop_detail: Any
    snapshot_refs: Any
    # view=technical: the steps (agent_step) in order.
    steps: list[StepView] | None = None


class RunCancel(BaseModel):
    reason: str | None = Field(default=None, max_length=2_000)


class AgentLock(BaseModel):
    reason: str = Field(min_length=1, max_length=2_000)


class PlanPut(BaseModel):
    steps: list[Any] = Field(min_length=1)
    edit_note: str | None = Field(default=None, max_length=4_000)


class PlanView(BaseModel):
    run_id: uuid.UUID
    version: int
    steps: list[Any]
    edit_note: str | None
    edited_by_user: bool


class MemoryCreate(BaseModel):
    scope: MemoryScope
    user_id: uuid.UUID | None = None
    kind: MemoryKind
    content: str = Field(min_length=1, max_length=8_000)
    expires_at: datetime.datetime | None = None


class MemoryPatch(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=8_000)
    expires_at: datetime.datetime | None = None
    status: MemoryStatus | None = None


class MemoryView(_Row):
    id: uuid.UUID
    agent_id: uuid.UUID | None
    data_space_id: uuid.UUID
    scope: str
    user_id: uuid.UUID | None
    kind: str
    content: str
    source_kind: str
    source_ref: uuid.UUID | None
    expires_at: datetime.datetime | None
    use_count: int
    correction_count: int
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime
