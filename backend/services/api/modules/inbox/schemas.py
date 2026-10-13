"""Request and response shapes of the inbox module (tab 4 A3, group "Cần bạn và đánh giá").

Responses are the table's columns minus org_id, created_by, updated_by (A3 "Trả về").
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

HitlDecision = Literal["answer", "approve", "reject", "request_changes", "reassign"]
ProposalDecisionKind = Literal["accept", "accept_with_edit", "reject", "request_changes"]


class ApprovalStepView(BaseModel):
    id: uuid.UUID
    step: int
    approver_role: str
    approver_id: uuid.UUID | None
    decision: str | None
    note: str | None
    decided_at: datetime.datetime | None
    created_at: datetime.datetime


class HitlView(BaseModel):
    id: uuid.UUID
    # None for an org-wide request (an audit export, an emergency grant).
    data_space_id: uuid.UUID | None
    kind: str
    risk: str
    title: str
    context: Any
    options: Any
    source_service: str
    source_ref: uuid.UUID | None
    parent_run_label: str | None
    assignee_id: uuid.UUID | None
    assignee_group: str | None
    assignee_capability: str | None
    due_at: datetime.datetime | None
    status: str
    response: Any
    remember_as_rule: bool
    resolved_by: uuid.UUID | None
    resolved_at: datetime.datetime | None
    channel_card_ids: list[uuid.UUID]
    created_at: datetime.datetime
    updated_at: datetime.datetime


class HitlDetail(HitlView):
    # The multi-step chain (UC-07-07 5a, BR-132); empty for a single-person request.
    approval_steps: list[ApprovalStepView] = Field(default_factory=list)


class HitlResolve(BaseModel):
    decision: HitlDecision
    option: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=4_000)
    reassign_to: uuid.UUID | None = None
    remember_as_rule: bool = False

    @model_validator(mode="after")
    def _reassign_needs_a_person(self) -> HitlResolve:
        if (self.decision == "reassign") != (self.reassign_to is not None):
            raise ValueError("reassign_to is required with decision reassign, and only with it")
        if self.decision == "request_changes" and not (self.note or "").strip():
            # UC-07-07 6a: the agent revises its plan from this comment.
            raise ValueError("decision request_changes needs a note")
        return self


class ProposalReviewView(BaseModel):
    id: uuid.UUID
    step: int
    reviewer_id: uuid.UUID
    decision: str
    note: str | None
    reviewed_regressions: bool
    decided_at: datetime.datetime


class ProposalView(BaseModel):
    id: uuid.UUID
    data_space_id: uuid.UUID
    kind: str
    target_service: str
    target_ref: uuid.UUID | None
    title: str
    diff: Any
    reason: str | None
    evidence: Any
    impact: Any
    impact_level: str
    proposed_by: uuid.UUID
    assignee_id: uuid.UUID | None
    eval_run_id: uuid.UUID | None
    requires_compliance: bool
    status: str
    expires_at: datetime.datetime
    applied_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class EvalRunSummary(BaseModel):
    id: uuid.UUID
    status: str
    item_count: int
    passed: int
    failed: int
    pass_rate: decimal.Decimal | None
    regressions: int
    baseline_run_id: uuid.UUID | None
    finished_at: datetime.datetime | None


class ChangedResult(BaseModel):
    """An item whose result changed against the baseline run (BR-28)."""

    eval_item_id: uuid.UUID
    passed: bool
    cause: str | None
    actual: Any
    reviewed_by: uuid.UUID | None


class ProposalDetail(ProposalView):
    eval_run: EvalRunSummary | None = None
    changed_results: list[ChangedResult] = Field(default_factory=list)
    reviews: list[ProposalReviewView] = Field(default_factory=list)


class ProposalDecision(BaseModel):
    decision: ProposalDecisionKind
    note: str | None = Field(default=None, max_length=4_000)
    reviewed_regressions: bool = False
    edited_diff: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _edit_goes_with_accept_with_edit(self) -> ProposalDecision:
        if (self.decision == "accept_with_edit") != (self.edited_diff is not None):
            raise ValueError("edited_diff is required with accept_with_edit, and only with it")
        return self
