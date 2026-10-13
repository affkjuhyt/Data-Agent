"""Request and response shapes of the pack module (tab 4 A3, group "Gói ngành").

Responses are the table's columns minus org_id, created_by, updated_by (A3 "Trả về").
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _View(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Accepted(BaseModel):
    """A1.6: the body of a 202; Location names where the result appears."""

    id: uuid.UUID
    status: str
    poll_after_ms: int = 2_000


# --- packs and versions -------------------------------------------------------------------------


class IndustryPackView(_View):
    id: uuid.UUID
    code: str
    name: str
    industry_code: str
    publisher: str
    # Null for an organisation's own pack until its first version is published and signed.
    publisher_key_id: str | None
    origin: Literal["vendor", "organisation"]
    description: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class PackCreate(_Body):
    """POST /v1/packs: the organisation's own pack (UC-09-05), versions come later."""

    code: str = Field(min_length=1, max_length=200, pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    name: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=20_000)


class PackVersionView(_View):
    id: uuid.UUID
    pack_id: uuid.UUID
    version: str
    manifest: Any
    signature: str
    signature_verified_at: datetime.datetime | None
    min_core_version: str
    release_notes: str | None
    breaking_changes: Any
    object_key: str
    status: str
    released_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class CanonicalAttributeView(_View):
    id: uuid.UUID
    code: str
    name: str
    data_type: str
    required: bool
    code_list: Any
    pii: bool


class CanonicalEntityView(_View):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    key_attributes: list[str]
    attributes: list[CanonicalAttributeView] = Field(default_factory=list)


class PackMetricView(_View):
    id: uuid.UUID
    code: str
    name: str
    expression: str
    unit: str | None
    additivity: str
    legal_basis: str | None
    is_internal: bool
    in_regulatory_report: bool


class ValidationRuleView(_View):
    id: uuid.UUID
    code: str
    description: str
    expression: str
    tolerance: decimal.Decimal | None
    severity: str


class PackSkillView(_View):
    id: uuid.UUID
    kind: str
    code: str
    name: str
    definition: Any


class PackCheckRunView(_View):
    id: uuid.UUID
    pack_version_id: uuid.UUID
    checks: Any
    passed: bool
    ran_by: uuid.UUID
    created_at: datetime.datetime


class PackVersionDetail(PackVersionView):
    """GET /v1/pack-versions/{id}: with the canonical model, metrics, rules and sample skills."""

    entities: list[CanonicalEntityView] = Field(default_factory=list)
    metrics: list[PackMetricView] = Field(default_factory=list)
    rules: list[ValidationRuleView] = Field(default_factory=list)
    skills: list[PackSkillView] = Field(default_factory=list)
    # Where :validate reports (its Location): the latest pre-release check of this version.
    latest_check_run: PackCheckRunView | None = None


class PackVersionCreate(_Body):
    version: str = Field(min_length=1, max_length=64)
    # The content of the version: entities (with attributes), metrics, rules, skills.
    manifest: dict[str, Any]
    min_core_version: str = Field(min_length=1, max_length=64)
    release_notes: str | None = Field(default=None, max_length=20_000)
    # Each item: {"code", "kind", "migration"}; a breaking change needs its guide (UC-09-05 4a).
    breaking_changes: list[dict[str, Any]] = Field(default_factory=list)


# --- installs, mappings, calibration ------------------------------------------------------------


class PackInstallView(_View):
    id: uuid.UUID
    data_space_id: uuid.UUID
    pack_version_id: uuid.UUID
    previous_pack_version_id: uuid.UUID | None
    status: str
    activated_at: datetime.datetime | None
    activated_by: uuid.UUID | None
    proposal_id: uuid.UUID | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class PackInstallCreate(_Body):
    pack_version_id: uuid.UUID


class EntityMappingInput(_Body):
    attribute_id: uuid.UUID
    dataset_id: uuid.UUID
    column_expr: str = Field(min_length=1, max_length=4_000)
    confidence: decimal.Decimal | None = Field(default=None, ge=0, le=1)
    # A person confirming a suggestion, or mapping by hand.
    origin: Literal["confirmed", "manual"] = "confirmed"


class CodeMappingInput(_Body):
    attribute_id: uuid.UUID
    source_code: str = Field(min_length=1, max_length=500)
    canonical_code: str = Field(min_length=1, max_length=500)


class MappingsPut(_Body):
    entity_mappings: list[EntityMappingInput]
    code_mappings: list[CodeMappingInput] | None = None


class EntityMappingView(_View):
    id: uuid.UUID
    pack_install_id: uuid.UUID
    attribute_id: uuid.UUID
    dataset_id: uuid.UUID
    column_expr: str
    confidence: decimal.Decimal | None
    origin: str
    valid_from: datetime.datetime
    valid_to: datetime.datetime | None
    confirmed_by: uuid.UUID | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class EntityProgress(BaseModel):
    """Mapping progress of one canonical entity (the "112/124" of Đưa vào ngành · Ánh xạ)."""

    code: str
    name: str
    mapped: int
    total: int
    required_mapped: int
    required_total: int


class CalibrationSummary(_View):
    id: uuid.UUID
    trigger: str
    status: str
    passed: int
    failed: int
    finished_at: datetime.datetime | None
    created_at: datetime.datetime


class PackInstallDetail(PackInstallView):
    """GET /v1/pack-installs/{id}: the install with its pack, mapping progress and activation."""

    pack_id: uuid.UUID
    pack_code: str
    pack_name: str
    version: str
    entities: list[EntityProgress] = Field(default_factory=list)
    mapped: int = 0
    total: int = 0
    # The last reconciliation: activation waits for it to pass (BR-36).
    latest_calibration_run: CalibrationSummary | None = None


class CodeMappingView(_View):
    id: uuid.UUID
    source_code: str
    canonical_code: str
    valid_from: datetime.datetime


class MappingView(EntityMappingView):
    """GET /v1/pack-installs/{id}/mappings: a current mapping with the attribute it maps and,
    for a code attribute, its source-to-canonical code table."""

    entity_code: str
    attribute_code: str
    attribute_name: str
    required: bool
    code_mappings: list[CodeMappingView] = Field(default_factory=list)


class CalibrationItemView(_View):
    id: uuid.UUID
    item_kind: str
    item_code: str
    period: str | None
    computed_value: decimal.Decimal | None
    filed_value: decimal.Decimal | None
    delta: decimal.Decimal | None
    passed: bool
    suggested_cause: str | None


class CalibrationRunView(_View):
    """GET /v1/calibration-runs/{id}: each metric and rule against the filed numbers."""

    id: uuid.UUID
    pack_install_id: uuid.UUID
    trigger: str
    periods: list[str]
    tolerance_pp: decimal.Decimal
    passed: int
    failed: int
    status: str
    finished_at: datetime.datetime | None
    created_at: datetime.datetime
    items: list[CalibrationItemView] = Field(default_factory=list)


class CalibrateRequest(_Body):
    # Periods to reconcile, e.g. ["2026-08", "2026-09"]; empty: the worker takes the last two.
    periods: list[str] | None = Field(default=None, max_length=24)


# --- metric overrides ---------------------------------------------------------------------------


class MetricOverrideCreate(_Body):
    pack_install_id: uuid.UUID
    pack_metric_code: str = Field(min_length=1, max_length=200)
    expression: str = Field(min_length=1, max_length=20_000)
    scope_condition: str | None = Field(default=None, max_length=4_000)
    reason: str = Field(min_length=1, max_length=4_000)


class MetricOverrideView(_View):
    id: uuid.UUID
    pack_install_id: uuid.UUID
    pack_metric_code: str
    expression: str
    scope_condition: str | None
    reason: str
    status: str
    approved_by: uuid.UUID | None
    compliance_approved_by: uuid.UUID | None
    calibration_run_id: uuid.UUID | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class MetricOverrideDetail(MetricOverrideView):
    """GET /v1/metric-overrides/{id}: the pack's formula next to the organisation's, and where
    the approval stands (a regulatory metric also needs compliance, AC-09-03-07)."""

    metric_name: str | None
    base_expression: str | None
    in_regulatory_report: bool
    # The approval still missing: "approve", "compliance" or None once decided.
    awaiting: Literal["approve", "compliance"] | None
    # The trial of the formula on real data (UC-09-03 step 3), once one has run.
    calibration_run: CalibrationSummary | None = None


# --- upgrades -----------------------------------------------------------------------------------


class UpgradeRequest(_Body):
    to_version_id: uuid.UUID


class UpgradeDecision(_Body):
    kind: Literal["metric", "mapping"] = "metric"
    code: str = Field(min_length=1, max_length=200)
    decision: Literal["keep_override", "take_new", "defer"]


class DecisionsPut(_Body):
    decisions: list[UpgradeDecision] = Field(min_length=1)


class ApplyRequest(_Body):
    apply_from_period: str | None = Field(default=None, max_length=64)


class RollbackRequest(_Body):
    reason: str = Field(min_length=1, max_length=4_000)


class PackUpgradeView(_View):
    id: uuid.UUID
    pack_install_id: uuid.UUID
    from_version_id: uuid.UUID
    to_version_id: uuid.UUID
    merge_result: Any
    conflicts_open: int
    calibration_run_id: uuid.UUID | None
    status: str
    apply_from_period: str | None
    rollback_until: datetime.datetime | None
    approved_by: uuid.UUID | None
    # A dry run (?dry_run=true) is computed and returned, never stored.
    dry_run: bool = False
    created_at: datetime.datetime | None
    updated_at: datetime.datetime | None


class PackUpgradeDetail(PackUpgradeView):
    """GET /v1/pack-upgrades/{id}: the three-way merge split into what needs a person and what
    was decided, and the test run of workflow pack_upgrade."""

    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    calibration_run: CalibrationSummary | None = None
