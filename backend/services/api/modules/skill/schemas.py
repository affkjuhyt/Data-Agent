"""Request and response shapes of the skill module (tab 4 A3, group "Kỹ năng").

Responses are the table's columns minus org_id, created_by, updated_by, deleted_at (A3 "Trả về").
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Category = Literal[
    "decomposition", "causal", "forecast", "reconciliation", "quality", "report", "custom"
]
Scope = Literal["personal", "data_space", "org"]
Origin = Literal["manual", "from_session", "agent_suggested", "pack"]
SuggestionStatus = Literal["open", "accepted", "dismissed"]

_CODE = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$"


class SandboxLimits(BaseModel):
    """BR-94 (and BR-08): a step gets at most 2 vCPU, 4 GB and 120 seconds, and never the network."""

    model_config = ConfigDict(extra="forbid")

    cpu: float | None = Field(default=None, gt=0, le=2)
    memory_mb: int | None = Field(default=None, gt=0, le=4096)
    timeout_s: int | None = Field(default=None, gt=0, le=120)
    network: Literal[False] = False


class SkillCreate(BaseModel):
    code: str = Field(pattern=_CODE)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4_000)
    category: Category
    scope: Scope = "personal"
    origin: Origin = "manual"
    # The workspace (from_session) or the suggestion (agent_suggested) the skill came from.
    origin_ref: uuid.UUID | None = None


class SkillView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    data_space_id: uuid.UUID | None
    code: str
    name: str
    description: str | None
    category: str
    scope: str
    owner_id: uuid.UUID
    origin: str
    origin_ref: uuid.UUID | None
    current_version: int | None
    replaced_by_skill_id: uuid.UUID | None
    run_count_30d: int
    created_at: datetime.datetime
    updated_at: datetime.datetime


class VersionCreate(BaseModel):
    params_schema: dict[str, Any]
    steps: list[dict[str, Any]] = Field(min_length=1, max_length=100)
    template_md: str | None = Field(default=None, max_length=20_000)
    metric_version_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    sandbox_limits: SandboxLimits | None = None


class VersionPatch(BaseModel):
    params_schema: dict[str, Any] | None = None
    steps: list[dict[str, Any]] | None = Field(default=None, min_length=1, max_length=100)
    template_md: str | None = Field(default=None, max_length=20_000)
    sandbox_limits: SandboxLimits | None = None


class VersionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    skill_id: uuid.UUID
    version: int
    params_schema: Any
    steps: Any
    template_md: str | None
    metric_version_ids: list[uuid.UUID]
    sandbox_limits: Any
    status: str
    eval_run_id: uuid.UUID | None
    approved_by: uuid.UUID | None
    approved_at: datetime.datetime | None
    approval_steps: int
    retire_at: datetime.datetime | None
    created_at: datetime.datetime


class VersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version: int
    status: str
    eval_run_id: uuid.UUID | None
    approved_at: datetime.datetime | None
    retire_at: datetime.datetime | None
    created_at: datetime.datetime


class SkillDetail(SkillView):
    # The version a run uses (published, or published then flagged needs_review, UC-03-04 1a).
    published_version: VersionView | None = None
    versions: list[VersionSummary] = Field(default_factory=list)


class RunCreate(BaseModel):
    params: dict[str, Any]
    as_of: datetime.date | None = None


class TryRequest(BaseModel):
    """POST /v1/skill-versions/{id}:try: one trial of a draft with these parameters."""

    params: dict[str, Any]


class ToolView(BaseModel):
    """A tool an agent may be given (tab 5 G2): code, what it does, its permission level."""

    code: str
    level: Literal["read", "write_derived", "external_send", "system_action"]
    description: str
    executed_by: str
    default_checkpoint: str | None


class Accepted(BaseModel):
    """A1.6: the body of a 202, next to the Location header."""

    id: uuid.UUID
    status: str
    poll_after_ms: int


class RunStepView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    seq: int
    name: str
    query_run_id: uuid.UUID | None
    output: Any
    status: str
    duration_ms: int | None
    created_at: datetime.datetime


class RunView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    skill_version_id: uuid.UUID
    data_space_id: uuid.UUID
    caller_kind: str
    caller_ref: uuid.UUID | None
    principal_id: uuid.UUID
    params: Any
    as_of: datetime.date | None
    status: str
    result: Any
    result_ref: str | None
    started_at: datetime.datetime | None
    finished_at: datetime.datetime | None
    error: str | None
    created_at: datetime.datetime


class RunDetail(RunView):
    skill_id: uuid.UUID
    version: int
    steps: list[RunStepView] = Field(default_factory=list)


class RetireRequest(BaseModel):
    replaced_by_skill_id: uuid.UUID | None = None
    reason: str = Field(min_length=1, max_length=4_000, pattern=r"\S")


class ShareRequest(BaseModel):
    scope: Scope


class SuggestionView(BaseModel):
    """BR-98: the step structure and the sessions it was seen in, never a cell's content."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    data_space_id: uuid.UUID
    pattern: Any
    session_ids: list[uuid.UUID]
    user_count: int
    status: str
    dismissed_until: datetime.datetime | None
    accepted_skill_id: uuid.UUID | None
    created_at: datetime.datetime
    updated_at: datetime.datetime
