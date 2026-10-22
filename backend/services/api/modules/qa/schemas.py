"""Wire shapes of the A2 groups Hỏi đáp and Truy vấn (tab 4 A3).

Request bodies take exactly A3's fields ("*" required), typed after the D4 column
of the same name. Responses are the table's columns minus org_id, created_by,
updated_by, deleted_at and storage references (A3 "Trả về").
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

KnowledgeMode = Literal["latest", "period_close", "at"]
ErrorKind = Literal[
    "wrong_metric", "wrong_scope", "wrong_period", "wrong_number", "wrong_document", "other"
]


class _Row(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class _Body(BaseModel):
    # A3 names every field a request takes; anything else is a client error, not silently dropped.
    model_config = ConfigDict(extra="forbid")


class JobAccepted(BaseModel):
    """A1.6: the body of a 202; Location points at the resource to poll."""

    id: uuid.UUID
    status: str
    poll_after_ms: int = 1000


# ─── questions and plans ───


class QuestionCreate(_Body):
    text: str = Field(min_length=1, max_length=32_000)
    as_of: datetime.date | None = None
    knowledge_mode: KnowledgeMode = "latest"
    knowledge_at: datetime.datetime | None = None
    purpose: str | None = Field(default=None, max_length=2_000)
    parent_question_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    stream: bool = False


class QuestionAccepted(JobAccepted):
    question_id: uuid.UUID
    conversation_id: uuid.UUID
    stream_url: str | None = None


class PlanView(_Row):
    id: uuid.UUID
    question_id: uuid.UUID | None
    version: int
    chips: Any
    steps: Any
    confidence: float | None
    edited_by_user: bool
    edit_note: str | None
    created_at: datetime.datetime


class QuestionView(_Row):
    id: uuid.UUID
    conversation_id: uuid.UUID
    seq: int
    text: str
    parent_question_id: uuid.UUID | None
    as_of: datetime.date | None
    knowledge_mode: str
    knowledge_at: datetime.datetime | None
    purpose: str | None
    status: str
    received_at: datetime.datetime
    answered_at: datetime.datetime | None
    plan: PlanView | None = None
    answer_id: uuid.UUID | None = None


class PlanPatch(_Body):
    # Plan chips: metric, scope, period, dimensions, data mode (D4 qa.plan.chips).
    chips: list[dict[str, Any]] = Field(min_length=1, max_length=50)
    edit_note: str | None = Field(default=None, max_length=4_000)


class PlanAccepted(JobAccepted):
    plan_id: uuid.UUID
    version: int


# ─── conversations ───


class ConversationView(_Row):
    id: uuid.UUID
    data_space_id: uuid.UUID
    user_id: uuid.UUID
    channel: str
    api_client_id: uuid.UUID | None
    title: str | None
    workspace_id: uuid.UUID | None
    last_question_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ConversationTurnView(_Row):
    """One question in a conversation, ordered by seq (R1-13 reopen)."""

    question_id: uuid.UUID
    seq: int
    text: str
    status: str
    answer_id: uuid.UUID | None = None
    received_at: datetime.datetime
    answered_at: datetime.datetime | None = None


class ConversationDetail(_Row):
    """Conversation with ordered turns for Ask URL hydrate (R1-13)."""

    id: uuid.UUID
    data_space_id: uuid.UUID
    user_id: uuid.UUID
    channel: str
    api_client_id: uuid.UUID | None
    title: str | None
    workspace_id: uuid.UUID | None
    last_question_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    turns: list[ConversationTurnView] = Field(default_factory=list)


# ─── answers, lineage, feedback ───


class AnswerView(_Row):
    id: uuid.UUID
    question_id: uuid.UUID
    plan_id: uuid.UUID
    kind: str
    forecast_job_id: uuid.UUID | None
    number_check: Any
    text: str
    figures: Any
    chart_spec: Any | None
    citations: Any
    filed_vs_current: Any | None
    reconcile_badge: str | None
    agent_name: str | None
    latency_ms: int | None
    lineage_hash: str | None
    data_product_id: uuid.UUID | None
    product_scope: str
    created_at: datetime.datetime


class MetricVersionRef(BaseModel):
    id: uuid.UUID
    metric_id: uuid.UUID
    version: int
    display_name: str
    status: str
    certified_by: uuid.UUID | None
    certified_at: datetime.datetime | None


class QueryRunRef(BaseModel):
    id: uuid.UUID
    route: str
    source_id: uuid.UUID | None
    period_close_id: uuid.UUID | None
    as_of: datetime.date | None
    metric_version_ids: list[uuid.UUID]
    policies_applied: list[uuid.UUID]
    row_count: int | None
    result_hash: str | None
    status: str
    started_at: datetime.datetime


class LineageFigure(BaseModel):
    figure_key: str | None
    figure: Any
    metric_version: MetricVersionRef | None = None
    query_run: QueryRunRef | None = None
    compute_run_id: uuid.UUID | None = None
    receipt_figure: Any | None = None


class LineageView(BaseModel):
    answer_id: uuid.UUID
    lineage_hash: str | None
    plan: PlanView | None
    figures: list[LineageFigure]


class FeedbackCreate(_Body):
    rating: Literal["up", "down", "wrong"]
    error_kind: ErrorKind | None = None
    expected_value: str | None = Field(default=None, max_length=4_000)
    comment: str | None = Field(default=None, max_length=4_000)

    @model_validator(mode="after")
    def wrong_has_kind(self) -> FeedbackCreate:
        # A3: 422 validation_failed (not the CHECK's business_rule_violated) for this one.
        if self.rating == "wrong" and self.error_kind is None:
            raise ValueError("error_kind is required when rating is 'wrong'")
        return self


class FeedbackView(_Row):
    id: uuid.UUID
    answer_id: uuid.UUID
    user_id: uuid.UUID
    rating: str
    error_kind: str | None
    expected_value: str | None
    comment: str | None
    status: str
    reviewer_id: uuid.UUID | None
    decided_at: datetime.datetime | None
    reason: str | None
    proposal_id: uuid.UUID | None
    eval_item_id: uuid.UUID | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class FeedbackPatch(_Body):
    status: Literal["open", "in_review", "confirmed", "rejected"]
    reason: str | None = Field(default=None, max_length=4_000)

    @model_validator(mode="after")
    def rejection_has_reason(self) -> FeedbackPatch:
        # UC-01-05 5a: a rejection carries its reason.
        if self.status == "rejected" and not (self.reason and self.reason.strip()):
            raise ValueError("reason is required when status is 'rejected'")
        return self


class FeedbackToEval(_Body):
    eval_set_id: uuid.UUID


class EvalItemAccepted(JobAccepted):
    """The eval item is created by the Eval service from feedback.sent_to_eval; `id` is the id it
    will carry (qa.feedback.eval_item_id)."""

    feedback_id: uuid.UUID
    eval_set_id: uuid.UUID


# ─── exports ───


class ExportCreate(_Body):
    format: Literal["xlsx", "csv", "pdf", "png"]
    include_detail_rows: bool = False


class ExportView(_Row):
    id: uuid.UUID
    answer_id: uuid.UUID | None
    artifact_id: uuid.UUID | None
    requested_by: uuid.UUID
    format: str
    include_detail_rows: bool
    row_count: int | None
    status: str
    hitl_request_id: uuid.UUID | None
    expires_at: datetime.datetime | None
    created_at: datetime.datetime
    # Signed for 15 minutes once the file is ready; None until a signer is configured.
    download_url: str | None = None


# ─── receipts and replays ───


class ReceiptFigureView(_Row):
    id: uuid.UUID
    figure_key: str
    label: str
    value: str | None
    unit: str | None
    display_state: str
    stale_note: str | None
    requested_period: str | None
    metric_id: uuid.UUID | None
    metric_version_id: uuid.UUID | None
    metric_version_no: int | None
    metric_certified_by: uuid.UUID | None
    metric_certified_at: datetime.datetime | None
    source_kind: str
    query_run_id: uuid.UUID | None
    compute_run_id: uuid.UUID | None
    published_result_id: uuid.UUID | None
    compiled_sql_sha256: str | None
    expr_sha256: str | None
    result_sha256: str | None
    dataset_versions: Any
    as_of: datetime.date | None
    freshness_state: str
    due_at: datetime.datetime | None
    created_at: datetime.datetime

    @field_validator("value", mode="before")
    @classmethod
    def decimal_as_string(cls, value: object) -> object:
        # A1.1: figures travel as decimal strings so no digit is lost.
        return None if value is None else str(value)


class ReceiptView(_Row):
    id: uuid.UUID
    answer_id: uuid.UUID
    receipt_no: str
    data_product_id: uuid.UUID
    data_product_code: str
    data_product_version: int
    plan_id: uuid.UUID
    answered_at: datetime.datetime
    knowledge_mode: str
    knowledge_at: datetime.datetime | None
    compiler_version: str
    runtime_version: str
    models: Any
    frontier_used: bool
    freshness: Any
    figure_count: int
    masked_count: int
    receipt_sha256: str | None
    sealed_at: datetime.datetime | None
    retain_until: datetime.date
    created_at: datetime.datetime
    figures: list[ReceiptFigureView] = []
    # Only with ?verify=true: the stored SHA-256 recomputed over the stored rows.
    hash_match: bool | None = None


class ReplayCreate(_Body):
    mode: Literal["figures", "figures_and_text"] = "figures"
    reason: str | None = Field(default=None, max_length=4_000)


# BR-177: regenerated wording is always labelled.
REPLAY_TEXT_LABEL = "Câu chữ sinh lại, có thể khác bản gốc; số giữ nguyên"


class ReplayView(_Row):
    id: uuid.UUID
    receipt_id: uuid.UUID
    requested_by: uuid.UUID
    reason: str | None
    mode: str
    compiler_version_used: str
    status: str
    outcome: str | None
    figure_results: Any
    differs_count: int
    cause_summary: str | None
    retro_change_ids: list[uuid.UUID]
    replay_text: str | None
    replay_text_label: str | None = None
    replay_model: str | None
    query_run_ids: list[uuid.UUID]
    error_book_entry_id: uuid.UUID | None
    started_at: datetime.datetime
    finished_at: datetime.datetime | None
    created_at: datetime.datetime

    @model_validator(mode="after")
    def label_regenerated_text(self) -> ReplayView:
        if self.replay_text is not None:
            self.replay_text_label = REPLAY_TEXT_LABEL
        return self


# ─── query runs ───


class ExploreFilter(_Body):
    dimension_id: uuid.UUID
    op: Literal["eq", "in"] = "eq"
    values: list[str] = Field(min_length=1, max_length=500)


class ExploreQueryCreate(_Body):
    metric_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)
    dimensions: list[uuid.UUID] = Field(default_factory=list, max_length=10)
    filters: list[ExploreFilter] = Field(default_factory=list, max_length=50)
    as_of: datetime.date | None = None
    # A comparison the compiler adds as a second period, e.g. previous_period.
    compare: str | None = Field(default=None, max_length=64)


class RowFilter(_Body):
    """A condition on one column of the dataset (SCR-12 Khám phá bảng)."""

    column: str = Field(min_length=1, max_length=200)
    op: Literal["eq", "ne", "gt", "gte", "lt", "lte", "in"] = "eq"
    value: str | int | float | bool | None | list[str | int | float] = None

    @model_validator(mode="after")
    def _list_only_for_in(self) -> RowFilter:
        if (self.op == "in") != isinstance(self.value, list):
            raise ValueError("value is a list exactly when op is `in`")
        return self


class QueryRunCreate(_Body):
    """POST /v1/query-runs: one dataset with filters, or one read-only SQL statement."""

    dataset_id: uuid.UUID | None = None
    sql: str | None = Field(default=None, min_length=1, max_length=100_000)
    filters: list[RowFilter] = Field(default_factory=list, max_length=50)
    # BR-03: a run returns at most 10,000 rows.
    limit: int = Field(default=1_000, ge=1, le=10_000)
    as_of: datetime.date | None = None

    @model_validator(mode="after")
    def _one_target(self) -> QueryRunCreate:
        if (self.dataset_id is None) == (self.sql is None):
            raise ValueError("send dataset_id or sql, not both")
        if self.sql is not None and self.filters:
            raise ValueError("filters apply to a dataset; write them in the SQL instead")
        return self


class QueryRunView(_Row):
    """QueryRun without its SQL (GET .../sql, view_detail) and its storage reference."""

    id: uuid.UUID
    data_space_id: uuid.UUID
    caller_kind: str
    caller_id: uuid.UUID
    principal_id: uuid.UUID
    source_id: uuid.UUID | None
    route: str
    period_close_id: uuid.UUID | None
    policies_applied: list[uuid.UUID]
    metric_version_ids: list[uuid.UUID]
    as_of: datetime.date | None
    row_count: int | None
    result_hash: str | None
    duration_ms: int | None
    status: str
    error: str | None
    started_at: datetime.datetime


class QuerySqlView(BaseModel):
    sql_executed: str
    policies_applied: list[uuid.UUID]
    sql_generated: str


class QueryRowsPage(BaseModel):
    columns: Any
    items: list[Any]
    next_page_token: str = ""
    truncated: bool
    expires_at: datetime.datetime
