"""Wire shapes of the period module (tab 4 A3, group "Kỳ chốt").

Responses are the table's columns minus org_id, created_by and updated_by; amounts are decimals,
which go out as strings (A1.1).
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Body(BaseModel):
    # A field A3 does not name is a client bug, not something to ignore silently.
    model_config = ConfigDict(extra="forbid")


class _View(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PeriodCloseView(_View):
    id: uuid.UUID
    data_space_id: uuid.UUID
    period_calendar_id: uuid.UUID | None
    period_label: str
    business_date: datetime.date
    version: int
    status: str
    snapshot_hash: str | None
    workflow_id: str | None
    closed_by: uuid.UUID | None
    closed_at: datetime.datetime | None
    filed_report_code: str | None
    filed_at: datetime.date | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class PeriodVersion(_View):
    id: uuid.UUID
    version: int
    status: str
    snapshot_hash: str | None
    closed_at: datetime.datetime | None
    filed_report_code: str | None
    filed_at: datetime.date | None


class SnapshotFileView(_View):
    id: uuid.UUID
    dataset_id: uuid.UUID
    dataset_version: int
    row_count: int
    sha256: str
    written_at: datetime.datetime


class PeriodCloseDetail(PeriodCloseView):
    versions: list[PeriodVersion] = Field(default_factory=list)
    snapshot_files: list[SnapshotFileView] = Field(default_factory=list)
    # Count of reconciliation items per status (matched, mismatched, exception_*).
    reconciliation: dict[str, int] = Field(default_factory=dict)


class ReconciliationView(_View):
    id: uuid.UUID
    period_close_id: uuid.UUID
    item_code: str
    snapshot_value: decimal.Decimal
    ledger_value: decimal.Decimal
    tolerance: decimal.Decimal
    delta: decimal.Decimal
    status: str
    hitl_request_id: uuid.UUID | None
    created_at: datetime.datetime


class ExceptionRequest(_Body):
    item_code: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=4_000)


class ApprovalStepPlan(BaseModel):
    step: int
    approver_role: str


class HitlRequestRef(BaseModel):
    """The HITL request the inbox module opens from period.exception_requested, under this id."""

    id: uuid.UUID
    data_space_id: uuid.UUID
    kind: str
    risk: str
    status: str
    source_service: str
    source_ref: uuid.UUID
    approval_steps: list[ApprovalStepPlan]
    # BR-132: whoever pressed Close does not approve the exception.
    excluded_approver_ids: list[uuid.UUID]


class Override(_Body):
    """UC-10-03 3a: the number on the filed report differs from the system's."""

    metric_version_id: uuid.UUID
    reported_value: decimal.Decimal
    override_reason: str = Field(min_length=1, max_length=4_000)
    # BR-133: the controller (AC06) asked to approve the manual number. They approve it in Cần
    # bạn; the filer never states an approval.
    approver_id: uuid.UUID


class PublishRequest(_Body):
    report_code: str = Field(min_length=1, max_length=200)
    filed_at: datetime.date
    overrides: list[Override] = Field(default_factory=list, max_length=500)


class RetroChangeView(_View):
    id: uuid.UUID
    period_close_id: uuid.UUID
    published_result_id: uuid.UUID
    current_value: decimal.Decimal
    delta: decimal.Decimal
    below_materiality: bool
    decision: str | None
    decision_note: str | None
    new_period_close_id: uuid.UUID | None
    compliance_notified: bool
    detected_at: datetime.datetime


class RetroDecision(_Body):
    decision: Literal["new_version", "no_action"]
    decision_note: str = Field(min_length=1, max_length=4_000)


class Accepted(BaseModel):
    """A1.6: the body of a 202; Location names where the result appears."""

    id: uuid.UUID
    status: str
    poll_after_ms: int = 1_000


class CompareSide(BaseModel):
    period_close_id: uuid.UUID
    period_label: str
    version: int
    status: str
    # "published": frozen numbers; "retro": numbers recomputed by the retro check for this version.
    values_from: Literal["published", "retro"]


class CompareItem(BaseModel):
    metric_id: uuid.UUID | None
    metric_code: str | None
    dimension_key: Any
    a_metric_version_id: uuid.UUID | None
    b_metric_version_id: uuid.UUID | None
    a_value: decimal.Decimal | None
    b_value: decimal.Decimal | None
    delta: decimal.Decimal | None
    # retro: a retroactive change; definition: another metric version; data: anything else.
    cause: Literal["retro", "definition", "data", "added", "removed"]


class CompareResult(BaseModel):
    a: CompareSide
    b: CompareSide
    # GET /v1/periods/compare/{rows_id}/rows lists the slices behind the difference.
    rows_id: uuid.UUID | None
    items: list[CompareItem]


class DiffRow(BaseModel):
    retro_change_id: uuid.UUID
    period_close_id: uuid.UUID
    metric_version_id: uuid.UUID
    dimension_key: Any
    published_value: decimal.Decimal
    current_value: decimal.Decimal
    delta: decimal.Decimal
    below_materiality: bool
    detected_at: datetime.datetime
