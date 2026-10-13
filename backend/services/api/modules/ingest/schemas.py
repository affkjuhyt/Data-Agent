"""Request and response shapes of the ingest module (tab 4 A3, group "Nạp dữ liệu").

Field types follow tab 3b D4 for the column of the same name. Responses are the table's columns
minus org_id, created_by, updated_by and deleted_at (A3 "Trả về").
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

JobMode = Literal["full", "incremental", "cdc"]
JobStatus = Literal["draft", "active", "paused", "failing", "schema_drift"]
RuleKind = Literal[
    "not_null", "range", "unique", "in_list", "sum_matches", "freshness", "custom_sql"
]
Severity = Literal["high", "medium", "low"]


def _required_text(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value.strip()


class Accepted(BaseModel):
    """A1.6: the body of a 202; Location names where the result appears."""

    id: uuid.UUID
    status: str
    poll_after_ms: int = 1_000


# --- jobs ---------------------------------------------------------------------------------------


class JobRunView(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    workflow_id: str | None
    rows_read: int
    rows_written: int
    rows_dlq: int
    cursor_from: str | None
    cursor_to: str | None
    lag_seconds: int | None
    status: str
    started_at: datetime.datetime
    finished_at: datetime.datetime | None
    error: str | None
    created_at: datetime.datetime


class IngestionJobView(BaseModel):
    id: uuid.UUID
    data_space_id: uuid.UUID
    source_id: uuid.UUID
    name: str
    mode: str
    source_object: str
    target_dataset_id: uuid.UUID | None
    cursor_column: str | None
    cursor_value: str | None
    schedule_cron: str | None
    freshness_sla: datetime.timedelta | None
    dlq_retention_days: int
    status: str
    paused_reason: str | None
    paused_at: datetime.datetime | None
    consecutive_failures: int
    created_at: datetime.datetime
    updated_at: datetime.datetime


class IngestionJobDryRun(IngestionJobView):
    """POST /v1/sources/{id}/jobs?dry_run=true: the job as it would be created, not recorded.

    A3 also asks for a sample of 1,000 rows and its errors. Reading the source is worker-ingest's:
    the api holds no connection to it, so sample and errors stay empty and `note` says so."""

    dry_run: Literal[True] = True
    sample: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    note: str


class IngestionJobSummary(IngestionJobView):
    """GET /v1/jobs: the job with its latest run, lag and pending DLQ records (UC-04-09 step 1)."""

    last_run: JobRunView | None = None
    lag_seconds: int | None = None
    dlq_pending: int = 0


class JobCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    mode: JobMode
    source_object: str = Field(min_length=1, max_length=1_000)
    target_dataset_id: uuid.UUID | None = None
    cursor_column: str | None = Field(default=None, max_length=200)
    schedule_cron: str | None = Field(default=None, max_length=100)
    freshness_sla: datetime.timedelta | None = None
    dlq_retention_days: int | None = Field(default=None, ge=1, le=365)

    _name = field_validator("name", "source_object")(_required_text)

    @model_validator(mode="after")
    def _incremental_needs_a_cursor(self) -> JobCreate:
        # A3: 422 validation_failed when an incremental job has no cursor column.
        if self.mode == "incremental" and not (self.cursor_column or "").strip():
            raise ValueError("an incremental job needs cursor_column")
        return self


class JobPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    schedule_cron: str | None = Field(default=None, max_length=100)
    freshness_sla: datetime.timedelta | None = None
    dlq_retention_days: int | None = Field(default=None, ge=1, le=365)


class Reason(BaseModel):
    reason: str = Field(min_length=1, max_length=2_000)

    _reason = field_validator("reason")(_required_text)


class Backfill(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: datetime.date = Field(alias="from")
    to: datetime.date

    @model_validator(mode="after")
    def _ordered(self) -> Backfill:
        if self.to < self.from_:
            raise ValueError("to must not be before from")
        return self


class DlqRecordView(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    job_run_id: uuid.UUID
    payload_ref: str
    error_kind: str
    error: str
    replay_count: int
    status: str
    discard_reason: str | None
    expires_at: datetime.datetime
    created_at: datetime.datetime


# --- uploads ------------------------------------------------------------------------------------


class FileUploadView(BaseModel):
    id: uuid.UUID
    data_space_id: uuid.UUID
    uploader_id: uuid.UUID
    file_name: str
    object_key: str
    size_bytes: int
    sheet: str | None
    header_row: int | None
    encoding: str | None
    decimal_sep: str | None
    column_map: Any
    validation: Any
    error_ratio: decimal.Decimal | None
    target_dataset_id: uuid.UUID | None
    replace_mode: str | None
    expires_at: datetime.datetime
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime


class UploadPatch(BaseModel):
    sheet: str | None = Field(default=None, max_length=200)
    header_row: int | None = Field(default=None, ge=0, le=1_000)
    encoding: str | None = Field(default=None, max_length=50)
    decimal_sep: Literal[".", ","] | None = None
    column_map: list[dict[str, Any]] | None = Field(default=None, max_length=2_000)
    target_dataset_id: uuid.UUID | None = None
    replace_mode: Literal["replace", "keep"] | None = None


class DatasetView(BaseModel):
    """A3 "Trả về: Dataset" of :commit (catalog.dataset). A new manual table is registered by the
    catalog consumer of file_upload.committed under the id given here; until then `registered`
    is false and only the fields known at commit are set."""

    id: uuid.UUID
    data_space_id: uuid.UUID
    origin: str
    registered: bool
    schema_name: str | None = None
    table_name: str | None = None
    display_name: str | None = None
    scope: str | None = None
    expires_at: datetime.datetime | None = None
    current_version: int | None = None
    quality_state: str | None = None
    enabled: bool | None = None


# --- quality ------------------------------------------------------------------------------------


class QualityRuleView(BaseModel):
    id: uuid.UUID
    data_space_id: uuid.UUID
    dataset_id: uuid.UUID
    column_name: str | None
    kind: str
    params: Any
    severity: str
    origin: str
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime


class QualityRuleCreate(BaseModel):
    dataset_id: uuid.UUID
    column_name: str | None = Field(default=None, max_length=200)
    kind: RuleKind
    params: dict[str, Any] | None = None
    severity: Severity | None = None


class QualityRulePatch(BaseModel):
    params: dict[str, Any] | None = None
    severity: Severity | None = None
    status: Literal["draft", "active", "disabled"] | None = None


class QualityResultView(BaseModel):
    id: uuid.UUID
    rule_id: uuid.UUID
    run_kind: str
    run_ref: uuid.UUID | None
    passed: bool
    violations: int
    sample_ref: str | None
    acknowledged_by: uuid.UUID | None
    acknowledged_at: datetime.datetime | None = None
    acknowledged_note: str | None = None
    checked_at: datetime.datetime
    # From the rule, so the list reads without a second call.
    dataset_id: uuid.UUID
    severity: str


class Acknowledge(BaseModel):
    note: str = Field(min_length=1, max_length=4_000)

    _note = field_validator("note")(_required_text)
