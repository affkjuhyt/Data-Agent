"""Wire shapes of the acquire module (tab 4 A3, group "Thu thập").

Request bodies are A3's fields with the D4.22 column types; responses are the table's columns minus
org_id, created_by, updated_by and secret_ref: a source's OpenBao reference is write-only, the
response only says whether one is set.
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from services.api.modules.acquire import rules

SourceKind = Literal["web_page", "web_listing", "public_api", "feed", "file_drop"]
ReviewGate = Literal["all", "exceptions", "auto"]
RecordDecision = Literal["confirm", "correct", "reject"]
DriftResolution = Literal["approved_new", "manual_fix", "reverted", "false_alarm"]
TrustLevel = decimal.Decimal
# BR-149: at most one request per second per domain.
RateLimit = decimal.Decimal


class _Body(BaseModel):
    # A field A3 does not name is a client bug, not something to ignore silently.
    model_config = ConfigDict(extra="forbid")


class _View(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def _domains(value: list[str]) -> list[str]:
    return list(dict.fromkeys(rules.normalise_domain(d) for d in value))


def _urls(value: list[str]) -> list[str]:
    return list(dict.fromkeys(rules.check_url(u) for u in value))


# ---------------------------------------------------------------- crawl sources


class CrawlSourceCreate(_Body):
    name: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=4_000)
    kind: SourceKind
    seed_urls: list[str] = Field(min_length=1, max_length=200)
    allowed_domains: list[str] = Field(min_length=1, max_length=50)
    max_depth: int = Field(default=3, ge=0, le=10)
    schedule_cron: str | None = Field(default=None, max_length=120)
    rate_limit_rps: RateLimit = Field(default=decimal.Decimal(1), gt=0, le=1)
    auth_mode: Literal["none", "api_key"] = "none"
    secret_ref: str | None = Field(default=None, max_length=500)
    review_gate: ReviewGate = "all"
    # A3 leaves it optional while the column has no default: a source nobody rated is neither
    # trusted nor distrusted until its owner says so (BR-156 label "nguồn ngoài" either way).
    trust_level: TrustLevel = Field(default=decimal.Decimal("0.5"), ge=0, le=1, decimal_places=2)
    openapi_url: str | None = Field(default=None, max_length=2_000)
    terms_url: str | None = Field(default=None, max_length=2_000)

    @field_validator("allowed_domains")
    @classmethod
    def _check_domains(cls, value: list[str]) -> list[str]:
        return _domains(value)

    @field_validator("seed_urls")
    @classmethod
    def _check_seeds(cls, value: list[str]) -> list[str]:
        return _urls(value)

    @field_validator("schedule_cron")
    @classmethod
    def _cron(cls, value: str | None) -> str | None:
        return rules.check_cron(value) if value is not None else None

    @field_validator("openapi_url", "terms_url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return rules.check_url(value) if value is not None else None

    @model_validator(mode="after")
    def _consistent(self) -> CrawlSourceCreate:
        if (self.auth_mode == "api_key") != (self.secret_ref is not None):
            raise ValueError("secret_ref is required with auth_mode api_key, and only with it")
        if self.openapi_url is not None and self.kind != "public_api":
            raise ValueError("openapi_url is for kind public_api")
        return self


class CrawlSourcePatch(_Body):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    goal: str | None = Field(default=None, min_length=1, max_length=4_000)
    allowed_domains: list[str] | None = Field(default=None, min_length=1, max_length=50)
    max_depth: int | None = Field(default=None, ge=0, le=10)
    schedule_cron: str | None = Field(default=None, max_length=120)
    rate_limit_rps: RateLimit | None = Field(default=None, gt=0, le=1)
    review_gate: ReviewGate | None = None
    trust_level: TrustLevel | None = Field(default=None, ge=0, le=1, decimal_places=2)
    owner_id: uuid.UUID | None = None

    @field_validator("allowed_domains")
    @classmethod
    def _check_domains(cls, value: list[str] | None) -> list[str] | None:
        return _domains(value) if value is not None else None

    @field_validator("schedule_cron")
    @classmethod
    def _cron(cls, value: str | None) -> str | None:
        return rules.check_cron(value) if value is not None else None


class ChecklistItem(_Body):
    result: Literal["pass", "fail", "not_applicable"]
    note: str | None = Field(default=None, max_length=2_000)


class LegalChecklist(_Body):
    """BR-174: the four items, each with its result."""

    terms: ChecklistItem
    robots: ChecklistItem
    copyright: ChecklistItem
    personal_data: ChecklistItem


class LegalEvidence(_Body):
    """A capture in MinIO da-raw-crawl/legal: the terms page, robots.txt, a copyright note."""

    kind: Literal["terms", "robots", "copyright", "personal_data", "other"]
    object_key: str = Field(min_length=1, max_length=1_000)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    captured_at: datetime.datetime


class LegalReview(_Body):
    legal_status: Literal["approved", "rejected"]
    terms_url: str = Field(max_length=2_000)
    legal_checklist: LegalChecklist
    legal_evidence: list[LegalEvidence] = Field(max_length=50)
    legal_review_due_at: datetime.datetime | None = None
    note: str | None = Field(default=None, max_length=4_000)

    @field_validator("terms_url")
    @classmethod
    def _url(cls, value: str) -> str:
        return rules.check_url(value)

    @model_validator(mode="after")
    def _approval_is_complete(self) -> LegalReview:
        if self.legal_status == "approved":
            if not any(e.kind == "terms" for e in self.legal_evidence):
                # terms_sha256 is the hash of this capture (BR-174).
                raise ValueError("an approval needs the capture of the terms page as evidence")
            failed = [
                name
                for name in rules.LEGAL_CHECKLIST
                if getattr(self.legal_checklist, name).result == "fail"
            ]
            if failed:
                raise ValueError(f"an approval cannot have failed items: {', '.join(failed)}")
        elif self.legal_review_due_at is not None:
            raise ValueError("legal_review_due_at goes with an approval")
        return self


class PauseRequest(_Body):
    reason: str = Field(min_length=1, max_length=2_000)

    @field_validator("reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason cannot be blank")
        return value.strip()


class LastFetchError(BaseModel):
    url: str
    http_status: int | None
    fetched_at: datetime.datetime


class CrawlSourceView(_View):
    id: uuid.UUID
    data_space_id: uuid.UUID
    name: str
    goal: str
    kind: str
    seed_urls: list[str]
    allowed_domains: list[str]
    max_depth: int
    schedule_cron: str | None
    rate_limit_rps: decimal.Decimal
    auth_mode: str
    has_secret: bool = False
    openapi_url: str | None
    robots_checked_at: datetime.datetime | None
    terms_note: str | None
    terms_url: str | None
    terms_sha256: str | None
    terms_observed_sha256: str | None
    terms_checked_at: datetime.datetime | None
    legal_status: str
    legal_checklist: dict[str, Any]
    legal_evidence: list[Any]
    legal_reviewed_by: uuid.UUID | None
    legal_reviewed_at: datetime.datetime | None
    legal_review_due_at: datetime.datetime | None
    review_gate: str
    trust_level: decimal.Decimal
    status: str
    owner_id: uuid.UUID
    active_schema_version_id: uuid.UUID | None
    consecutive_blocked: int
    last_run_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class CrawlSourceSummary(CrawlSourceView):
    """A3 of GET /v1/crawl-sources: the monitoring columns of SCR-56 (UC-14-06 step 1)."""

    last_run_status: str | None = None
    last_published_at: datetime.datetime | None = None
    # Seconds since the last published run; compared with schedule_cron by the screen.
    freshness_seconds: int | None = None
    sample_accuracy: decimal.Decimal | None = None
    pending_review: int = 0
    open_drifts: int = 0
    last_run_cost: decimal.Decimal | None = None
    last_fetch_error: LastFetchError | None = None
    has_issue: bool = False


# ---------------------------------------------------------------- schemas


class CrawlSchemaVersionView(_View):
    id: uuid.UUID
    source_id: uuid.UUID
    version: int
    status: str
    record_selector: str
    fields: list[Any]
    template_fingerprint: Any
    value_rules: list[Any]
    sample_page_ids: list[uuid.UUID]
    test_required_accuracy: decimal.Decimal | None
    parent_version_id: uuid.UUID | None
    inferred_by_run_id: uuid.UUID | None
    approved_by: uuid.UUID | None
    approved_at: datetime.datetime | None
    created_at: datetime.datetime


class SchemaInferenceRequest(_Body):
    sample_urls: list[str] | None = Field(default=None, max_length=100)
    hints: str | None = Field(default=None, max_length=4_000)

    @field_validator("sample_urls")
    @classmethod
    def _check(cls, value: list[str] | None) -> list[str] | None:
        return _urls(value) if value is not None else None


class SchemaTestRequest(_Body):
    sample_page_ids: list[uuid.UUID] | None = Field(default=None, max_length=500)


class SchemaApproveRequest(_Body):
    note: str | None = Field(default=None, max_length=4_000)


class SchemaRevertRequest(_Body):
    reason: str = Field(min_length=1, max_length=2_000)

    @field_validator("reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason cannot be blank")
        return value.strip()


class Accepted(BaseModel):
    """A1.6: the body of a 202; Location names where the result appears."""

    id: uuid.UUID
    status: str
    poll_after_ms: int = 2_000


class RevertAccepted(Accepted):
    restored_version_id: uuid.UUID
    superseded_records: int
    # Pages whose original is past retention or was never stored: not re-extractable (UC-14-03 E1).
    expired_page_ids: list[uuid.UUID] = Field(default_factory=list)


# ---------------------------------------------------------------- runs, pages, records


class CrawlRunView(_View):
    id: uuid.UUID
    source_id: uuid.UUID
    schema_version_id: uuid.UUID | None
    trigger: str
    status: str
    pages_fetched: int
    pages_changed: int
    records_extracted: int
    records_flagged: int
    sample_accuracy: decimal.Decimal | None
    drift_score: decimal.Decimal | None
    cost: decimal.Decimal | None
    agent_run_id: uuid.UUID | None
    started_at: datetime.datetime | None
    finished_at: datetime.datetime | None
    created_at: datetime.datetime


class CrawlPageView(_View):
    id: uuid.UUID
    url: str
    fetched_at: datetime.datetime
    http_status: int | None
    content_type: str | None
    sha256: str
    object_key: str | None
    byte_size: int | None
    changed: bool
    retain_until: datetime.datetime | None


class CrawlRunDetail(CrawlRunView):
    pages: list[CrawlPageView] = Field(default_factory=list)
    pages_truncated: bool = False


class CrawlSourceDetail(CrawlSourceSummary):
    active_schema: CrawlSchemaVersionView | None = None
    recent_runs: list[CrawlRunView] = Field(default_factory=list)


class SourcePage(BaseModel):
    """Where a record comes from, to highlight it on the capture (BR-150)."""

    page_id: uuid.UUID
    url: str
    object_key: str | None
    content_type: str | None
    sha256: str


class ExtractedRecordView(_View):
    id: uuid.UUID
    run_id: uuid.UUID
    page_id: uuid.UUID
    schema_version_id: uuid.UUID
    entity_key: str
    period: str
    values: dict[str, Any]
    field_confidence: dict[str, Any]
    record_confidence: decimal.Decimal
    status: str
    review_reasons: list[str]
    is_random_sample: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ExtractedRecordItem(ExtractedRecordView):
    source_page: SourcePage | None = None
    # field name → locator of the schema version (CSS/XPath, JSONPath, column, PDF region).
    locators: dict[str, Any] = Field(default_factory=dict)


class RecordReview(_Body):
    decision: RecordDecision
    corrected_values: dict[str, Any] | None = None
    reason: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def _shape(self) -> RecordReview:
        if (self.decision == "correct") != (self.corrected_values is not None):
            raise ValueError("corrected_values is required with correct, and only with it")
        if self.decision == "correct" and not self.corrected_values:
            raise ValueError("corrected_values cannot be empty")
        if self.decision == "reject" and not (self.reason or "").strip():
            raise ValueError("a rejection needs a reason")
        return self


class ReviewDecisionView(_View):
    id: uuid.UUID
    record_id: uuid.UUID
    decision: str
    corrected_values: dict[str, Any] | None
    reason: str | None
    decided_by: uuid.UUID
    decided_at: datetime.datetime
    used_as_example: bool


class RecordReviewResult(BaseModel):
    record: ExtractedRecordView
    decision: ReviewDecisionView


class BulkFilter(_Body):
    run_id: uuid.UUID
    review_reasons: list[str] | None = Field(default=None, max_length=10)


class BulkReview(_Body):
    record_ids: list[uuid.UUID] | None = Field(default=None, min_length=1, max_length=1_000)
    filter: BulkFilter | None = None
    # A correction carries per-record values, which a bulk call has not.
    decision: Literal["confirm", "reject"]
    reason: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def _one_selector(self) -> BulkReview:
        if (self.record_ids is None) == (self.filter is None):
            raise ValueError("give record_ids or filter, not both")
        if self.decision == "reject" and not (self.reason or "").strip():
            raise ValueError("a rejection needs a reason")
        return self


class BulkReviewResult(BaseModel):
    updated: int
    by_status: dict[str, int]


# ---------------------------------------------------------------- drifts


class SchemaDriftView(_View):
    id: uuid.UUID
    source_id: uuid.UUID
    run_id: uuid.UUID
    from_version_id: uuid.UUID
    drift_kind: str
    severity: str
    signals: dict[str, Any]
    action: str
    proposed_version_id: uuid.UUID | None
    resolution: str | None
    resolved_by: uuid.UUID | None
    resolved_at: datetime.datetime | None
    created_at: datetime.datetime


class SchemaDriftItem(SchemaDriftView):
    proposed_version: CrawlSchemaVersionView | None = None


class DriftResolve(_Body):
    resolution: DriftResolution
    proposed_version_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=4_000)


# ---------------------------------------------------------------- consolidation


class ConsolidationSpecView(_View):
    id: uuid.UUID
    data_space_id: uuid.UUID
    name: str
    entity_type: str
    grain: str
    source_ids: list[uuid.UUID]
    normalization: dict[str, Any]
    source_priority: list[Any]
    aggregates: list[Any]
    summary_template: str | None
    allow_interpolation: bool
    target_dataset_id: uuid.UUID | None
    owner_id: uuid.UUID
    status: str
    last_edited_by: uuid.UUID
    last_edited_at: datetime.datetime
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ConsolidationSpecCreate(_Body):
    name: str = Field(min_length=1, max_length=200)
    entity_type: str = Field(min_length=1, max_length=100)
    grain: str = Field(min_length=1, max_length=200)
    source_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    normalization: dict[str, Any] = Field(default_factory=dict)
    source_priority: list[Any] = Field(default_factory=list)
    aggregates: list[Any] = Field(default_factory=list)
    summary_template: str | None = Field(default=None, max_length=4_000)
    allow_interpolation: bool = False
    target_dataset_id: uuid.UUID | None = None


class ConsolidationSpecPatch(_Body):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    entity_type: str | None = Field(default=None, min_length=1, max_length=100)
    grain: str | None = Field(default=None, min_length=1, max_length=200)
    source_ids: list[uuid.UUID] | None = Field(default=None, min_length=1, max_length=500)
    normalization: dict[str, Any] | None = None
    source_priority: list[Any] | None = None
    aggregates: list[Any] | None = None
    summary_template: str | None = Field(default=None, max_length=4_000)
    allow_interpolation: bool | None = None
    target_dataset_id: uuid.UUID | None = None
    owner_id: uuid.UUID | None = None
    status: Literal["active", "retired"] | None = None


class ConsolidationRunCreate(_Body):
    input_run_ids: list[uuid.UUID] | None = Field(default=None, min_length=1, max_length=500)


class ConsolidationRunView(_View):
    id: uuid.UUID
    spec_id: uuid.UUID
    input_run_ids: list[uuid.UUID]
    status: str
    records_in: int
    records_out: int
    conflicts_opened: int
    dataset_version_id: uuid.UUID | None
    claims_created: int
    chunks_embedded: int
    started_at: datetime.datetime
    finished_at: datetime.datetime | None
    spec_editor_id: uuid.UUID
    created_at: datetime.datetime


# ---------------------------------------------------------------- entity links


class EntityLinkView(_View):
    id: uuid.UUID
    data_space_id: uuid.UUID
    entity_type: str
    canonical_key: str
    reference_entity_id: uuid.UUID | None
    source_id: uuid.UUID
    source_key: str
    match_score: decimal.Decimal | None
    method: str
    status: str
    decided_by: uuid.UUID | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class EntityLinkDecision(_Body):
    status: Literal["confirmed", "rejected"]
    reference_entity_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _confirm_points_at_the_catalogue(self) -> EntityLinkDecision:
        if self.status == "confirmed" and self.reference_entity_id is None:
            # BR-169: a confirmed link always points at the reference catalogue.
            raise ValueError("confirmed needs reference_entity_id (BR-169)")
        return self
