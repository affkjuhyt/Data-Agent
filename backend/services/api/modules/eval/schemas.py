"""Request and response shapes of the eval module (tab 4 A3, group "Cần bạn và đánh giá").

Responses are the table's columns minus org_id, created_by, updated_by (A3 "Trả về").
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

Category = Literal[
    "correct_result", "ask_at_right_time", "respect_permission", "correct_format", "refusal"
]
SourceKind = Literal[
    "user_report",
    "number_check",
    "reviewer_reject",
    "replay_mismatch",
    "forecast_miss",
    "knowledge_lint",
]
Severity = Literal["low", "medium", "high"]
ErrorKind = Literal["definition", "synonym", "data", "freshness", "model", "formula", "retrieval"]
FixKind = Literal[
    "metric_version",
    "synonym",
    "example",
    "knowledge_page",
    "pack_version",
    "source_data",
    "model_config",
    "prompt",
    "code",
    "none",
]


class EvalItemCreate(BaseModel):
    category: Category = "correct_result"
    input: dict[str, Any]
    as_of: datetime.date | None = None
    expected: dict[str, Any]
    tolerance: decimal.Decimal | None = Field(default=None, ge=0)


class EvalItemView(BaseModel):
    id: uuid.UUID
    eval_set_id: uuid.UUID
    category: str
    input: Any
    as_of: datetime.date | None
    expected: Any
    tolerance: decimal.Decimal | None
    origin: str
    origin_ref: uuid.UUID | None
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime


class EvalRunCreate(BaseModel):
    subject_version: str = Field(default="current", min_length=1, max_length=200)


class EvalRunAccepted(BaseModel):
    """A1.6: the run is executed by the eval worker; poll Location."""

    id: uuid.UUID
    status: str
    poll_after_ms: int


class EvalResultView(BaseModel):
    id: uuid.UUID
    eval_item_id: uuid.UUID
    understanding_ok: bool | None
    value_ok: bool | None
    behavior_ok: bool | None
    passed: bool
    actual: Any
    cause: str | None
    changed_from_baseline: bool
    reviewed_by: uuid.UUID | None
    created_at: datetime.datetime


class EvalRunView(BaseModel):
    id: uuid.UUID
    eval_set_id: uuid.UUID
    subject_version: str
    trigger: str
    trigger_ref: uuid.UUID | None
    item_count: int
    passed: int
    failed: int
    correct_refusals: int
    pass_rate: decimal.Decimal | None
    baseline_run_id: uuid.UUID | None
    regressions: int
    status: str
    started_at: datetime.datetime
    finished_at: datetime.datetime | None
    created_at: datetime.datetime


class BaselineComparison(BaseModel):
    baseline_run_id: uuid.UUID
    passed: int
    failed: int
    pass_rate: decimal.Decimal | None
    passed_delta: int


class EvalRunDetail(EvalRunView):
    results: list[EvalResultView] = Field(default_factory=list)
    baseline: BaselineComparison | None = None


class EvalSetView(BaseModel):
    id: uuid.UUID
    data_space_id: uuid.UUID | None
    subject_type: str
    subject_id: uuid.UUID | None
    name: str
    pass_threshold: decimal.Decimal
    min_items: int
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime
    # Active items, and the newest run whatever its state (SCR Đánh giá lớp ngữ cảnh).
    item_count: int = 0
    latest_run: EvalRunView | None = None


class EvalFailure(BaseModel):
    """One item the run got wrong: what was asked, what was expected, what came back."""

    item: EvalItemView
    result: EvalResultView


class EvalSetDetail(EvalSetView):
    # Newest first; the failures are those of the newest completed run (failures_run_id), since
    # a run still going has not judged every item.
    runs: list[EvalRunView] = Field(default_factory=list)
    failures_run_id: uuid.UUID | None = None
    failures: list[EvalFailure] = Field(default_factory=list)


# The reference each source must carry (BR-179); the CHECKs of eval.error_book_entry hold the
# same rule, this answers it as A3's 422 validation_failed before the insert.
REQUIRED_REFS: dict[str, tuple[str, ...]] = {
    "user_report": ("feedback_id", "answer_id"),
    "number_check": ("answer_id",),
    "reviewer_reject": ("hitl_request_id", "answer_id"),
    "replay_mismatch": ("replay_id",),
    "forecast_miss": ("accuracy_log_id", "forecast_detail"),
    "knowledge_lint": ("lint_finding_id",),
}


class ErrorBookCreate(BaseModel):
    source_kind: SourceKind
    data_space_id: uuid.UUID
    data_product_id: uuid.UUID | None = None
    feedback_id: uuid.UUID | None = None
    answer_id: uuid.UUID | None = None
    receipt_id: uuid.UUID | None = None
    hitl_request_id: uuid.UUID | None = None
    replay_id: uuid.UUID | None = None
    accuracy_log_id: uuid.UUID | None = None
    forecast_detail: dict[str, Any] | None = None
    lint_finding_id: uuid.UUID | None = None
    title: str = Field(min_length=1, max_length=2_000)
    detail: dict[str, Any] = Field(default_factory=dict)
    severity: Severity = "medium"

    @model_validator(mode="after")
    def _source_reference(self) -> ErrorBookCreate:
        missing = [name for name in REQUIRED_REFS[self.source_kind] if getattr(self, name) is None]
        if missing:
            raise ValueError(f"source_kind {self.source_kind} needs {', '.join(missing)} (BR-179)")
        return self


class ErrorBookPatch(BaseModel):
    error_kind: ErrorKind | None = None
    root_cause: str | None = Field(default=None, min_length=1, max_length=4_000)
    assignee_id: uuid.UUID | None = None
    severity: Severity | None = None
    title: str | None = Field(default=None, min_length=1, max_length=2_000)


class MarkFixed(BaseModel):
    fix_kind: FixKind
    fix_proposal_id: uuid.UUID | None = None
    fix_ref: str | None = Field(default=None, min_length=1, max_length=500)
    note: str | None = Field(default=None, max_length=4_000)


class AddRegression(BaseModel):
    eval_set_id: uuid.UUID | None = None
    input: dict[str, Any] | None = None
    expected: dict[str, Any] | None = None
    tolerance: decimal.Decimal | None = Field(default=None, ge=0)
    as_of: datetime.date | None = None


class Dismiss(BaseModel):
    reason: str | None = Field(default=None, max_length=4_000)
    duplicate_of_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _reason_or_duplicate(self) -> Dismiss:
        if not (self.reason or "").strip() and self.duplicate_of_id is None:
            raise ValueError("reason or duplicate_of_id is required")
        return self


class ProposeToPack(BaseModel):
    pack_id: uuid.UUID
    summary: str = Field(min_length=1, max_length=4_000)


class ErrorBookView(BaseModel):
    id: uuid.UUID
    entry_no: str
    data_space_id: uuid.UUID
    data_product_id: uuid.UUID | None
    source_kind: str
    feedback_id: uuid.UUID | None
    answer_id: uuid.UUID | None
    receipt_id: uuid.UUID | None
    hitl_request_id: uuid.UUID | None
    replay_id: uuid.UUID | None
    accuracy_log_id: uuid.UUID | None
    forecast_detail: Any
    lint_finding_id: uuid.UUID | None
    title: str
    detail: Any
    severity: str
    error_kind: str | None
    root_cause: str | None
    assignee_id: uuid.UUID | None
    fix_kind: str | None
    fix_proposal_id: uuid.UUID | None
    fix_ref: str | None
    fixed_by: uuid.UUID | None
    fixed_at: datetime.datetime | None
    eval_item_id: uuid.UUID | None
    regression_run_id: uuid.UUID | None
    status: str
    dismiss_reason: str | None
    duplicate_of_id: uuid.UUID | None
    reopened_count: int
    pack_contribution: str
    pack_proposal_ref: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ErrorBookDetail(ErrorBookView):
    # The regression case and its latest CI result (A3); receipt, replay and fix proposal
    # summaries live in qa and inbox, which svc_eval does not read.
    eval_item: EvalItemView | None = None
    latest_result: EvalResultView | None = None
