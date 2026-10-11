from __future__ import annotations

import datetime
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field


class HitlView(BaseModel):
    id: uuid.UUID
    data_space_id: uuid.UUID
    kind: str
    risk: str
    title: str
    context: Any
    options: Any
    status: str
    assignee_id: uuid.UUID | None
    due_at: datetime.datetime | None
    response: Any


class HitlResolve(BaseModel):
    status: Literal["answered", "approved", "rejected", "changes_requested", "reassigned"]
    response: dict[str, Any] = Field(default_factory=dict)
    remember_as_rule: bool = False
    assignee_id: uuid.UUID | None = None


class ProposalView(BaseModel):
    id: uuid.UUID
    data_space_id: uuid.UUID
    kind: str
    target_service: str
    target_ref: uuid.UUID | None
    title: str
    diff: Any
    evidence: Any
    impact: Any
    impact_level: str
    status: str


class ProposalDecision(BaseModel):
    decision: Literal["accept", "accept_with_edit", "reject", "request_changes"]
    note: str | None = Field(default=None, max_length=4_000)
    diff: dict[str, Any] | None = None
    reviewed_regressions: bool = False


class EvalItemCreate(BaseModel):
    category: Literal["correct_result", "ask_at_right_time", "respect_permission", "correct_format", "refusal"] = "correct_result"
    input: dict[str, Any]
    expected: dict[str, Any]
    as_of: datetime.date | None = None
    tolerance: float | None = None


class EvalItemView(EvalItemCreate):
    id: uuid.UUID
    eval_set_id: uuid.UUID
    origin: str
    status: str


class EvalRunView(BaseModel):
    id: uuid.UUID
    eval_set_id: uuid.UUID
    subject_version: str
    trigger: str
    item_count: int
    passed: int
    failed: int
    pass_rate: float | None
    regressions: int
    status: str
    started_at: datetime.datetime
    finished_at: datetime.datetime | None


class ErrorBookCreate(BaseModel):
    entry_no: str = Field(min_length=1, max_length=100)
    data_space_id: uuid.UUID
    source_kind: Literal["user_report", "number_check", "reviewer_reject", "replay_mismatch", "forecast_miss", "knowledge_lint"]
    title: str = Field(min_length=1, max_length=2_000)
    detail: dict[str, Any] = Field(default_factory=dict)
    severity: Literal["low", "medium", "high"] = "medium"
    feedback_id: uuid.UUID | None = None
    answer_id: uuid.UUID | None = None


class ErrorBookPatch(BaseModel):
    error_kind: str | None = None
    root_cause: str | None = None
    assignee_id: uuid.UUID | None = None
    severity: Literal["low", "medium", "high"] | None = None


class ErrorBookView(BaseModel):
    id: uuid.UUID
    entry_no: str
    data_space_id: uuid.UUID
    source_kind: str
    title: str
    detail: Any
    severity: str
    error_kind: str | None
    root_cause: str | None
    assignee_id: uuid.UUID | None
    fix_kind: str | None
    fix_ref: str | None
    status: str
    pack_contribution: str
