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
    publisher_key_id: str
    description: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


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
    created_at: datetime.datetime
    updated_at: datetime.datetime


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
