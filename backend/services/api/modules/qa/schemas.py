"""Wire shapes for the Q&A module (A2 Hỏi đáp)."""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class QuestionCreate(BaseModel):
    text: str = Field(min_length=1, max_length=32_000)
    conversation_id: uuid.UUID | None = None
    as_of: datetime.date | None = None
    knowledge_mode: Literal["latest", "period_close", "at"] = "latest"
    knowledge_at: datetime.datetime | None = None
    purpose: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def knowledge_at_is_consistent(self) -> "QuestionCreate":
        if self.knowledge_mode == "at" and self.knowledge_at is None:
            raise ValueError("knowledge_at is required when knowledge_mode is 'at'")
        if self.knowledge_mode != "at" and self.knowledge_at is not None:
            raise ValueError("knowledge_at is only valid when knowledge_mode is 'at'")
        return self


class QuestionAccepted(BaseModel):
    question_id: uuid.UUID
    status: str
    poll_after_ms: int = 250


class PlanSummary(BaseModel):
    id: uuid.UUID
    version: int
    chips: Any
    steps: Any
    confidence: float | None
    edited_by_user: bool
    edit_note: str | None


class QuestionStatus(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    seq: int
    text: str
    status: str
    knowledge_mode: str
    knowledge_at: datetime.datetime | None
    plan: PlanSummary | None = None
    answer_id: uuid.UUID | None = None


class PlanPatch(BaseModel):
    chips: Any
    edit_note: str | None = Field(default=None, max_length=4_000)


class LineageView(BaseModel):
    answer_id: uuid.UUID
    lineage_hash: str | None
    figures: Any


class ConversationSummary(BaseModel):
    id: uuid.UUID
    data_space_id: uuid.UUID
    title: str | None
    last_question_at: datetime.datetime | None
    updated_at: datetime.datetime


class AnswerView(BaseModel):
    id: uuid.UUID
    question_id: uuid.UUID
    kind: str
    text: str
    figures: Any
    chart_spec: Any | None
    citations: Any
    number_check: Any
    reconcile_badge: str | None
    data_product_id: uuid.UUID | None
    product_scope: str
    agent_name: str | None
    latency_ms: int | None
    lineage_hash: str | None
    created_at: datetime.datetime


class FeedbackCreate(BaseModel):
    rating: Literal["up", "down", "wrong"]
    error_kind: Literal[
        "wrong_metric", "wrong_scope", "wrong_period", "wrong_number", "wrong_document", "other"
    ] | None = None
    expected_value: str | None = Field(default=None, max_length=4_000)
    comment: str | None = Field(default=None, max_length=4_000)

    @model_validator(mode="after")
    def wrong_has_kind(self) -> "FeedbackCreate":
        if self.rating == "wrong" and self.error_kind is None:
            raise ValueError("error_kind is required for a wrong rating")
        return self


class FeedbackView(BaseModel):
    id: uuid.UUID
    answer_id: uuid.UUID
    rating: str
    error_kind: str | None
    status: str
    comment: str | None


class FeedbackPatch(BaseModel):
    status: Literal["open", "in_review", "confirmed", "rejected"]
    reason: str | None = Field(default=None, max_length=4_000)


class ExportCreate(BaseModel):
    format: Literal["xlsx", "csv", "pdf", "png"]
    include_detail_rows: bool = False


class ExportView(BaseModel):
    id: uuid.UUID
    answer_id: uuid.UUID | None
    format: str
    include_detail_rows: bool
    row_count: int | None
    status: str
    artifact_id: uuid.UUID | None
    object_key: str | None
    expires_at: datetime.datetime | None
    created_at: datetime.datetime


class ReceiptView(BaseModel):
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


class ReplayCreate(BaseModel):
    reason: str | None = Field(default=None, max_length=4_000)
    mode: Literal["figures", "figures_and_text"] = "figures"


class ReplayView(BaseModel):
    id: uuid.UUID
    receipt_id: uuid.UUID
    reason: str | None
    mode: str
    compiler_version_used: str
    status: str
    outcome: str | None
    figure_results: Any
    differs_count: int
    cause_summary: str | None
    replay_text: str | None
    replay_model: str | None
    started_at: datetime.datetime
    finished_at: datetime.datetime | None


class QueryRunView(BaseModel):
    id: uuid.UUID
    data_space_id: uuid.UUID
    caller_kind: str
    caller_id: uuid.UUID
    route: str
    sql_generated: str | None = None
    sql_executed: str | None = None
    row_count: int | None
    result_hash: str | None
    duration_ms: int | None
    status: str
    started_at: datetime.datetime


class ExploreQueryCreate(BaseModel):
    text: str = Field(min_length=1, max_length=32_000)
    as_of: datetime.date | None = None


class QueryRows(BaseModel):
    columns: Any
    rows: Any
    truncated: bool
    expires_at: datetime.datetime
