"""Request and response shapes of the context module (tab 4 A3, group "Lớp ngữ cảnh").

Responses are the table's columns minus org_id, created_by, updated_by (A3 "Trả về"); request
fields carry the type of the D4 column of the same name.
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Classification = Literal["public", "internal", "confidential", "restricted"]
SLUG = r"^[a-z0-9][a-z0-9-]{1,79}$"


class View(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Accepted(BaseModel):
    """A1.6: the body of a 202, next to the Location header."""

    id: uuid.UUID
    status: str
    poll_after_ms: int


# ---------------------------------------------------------------- metrics (UC-07-01)


class MetricVersionCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4_000)
    expression: str = Field(min_length=1, max_length=8_000)
    filters: list[dict[str, Any]] | None = None
    unit: str | None = Field(default=None, max_length=50)
    decimals: int | None = Field(default=None, ge=0, le=10)
    direction: Literal["higher_better", "lower_better", "neutral"] | None = None
    period_semantics: Literal["end_of_period", "sum_over_period", "average_over_period"]
    time_grain: Literal["day", "month", "quarter", "year"] | None = None
    allowed_dimensions: list[uuid.UUID] | None = None
    additivity: Literal["full", "semi", "none"] | None = None
    legal_basis: str | None = Field(default=None, max_length=1_000)
    change_note: str | None = Field(default=None, max_length=2_000)


class MetricCreate(BaseModel):
    code: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{1,63}$")
    owner_id: uuid.UUID
    consumer_group: str | None = Field(default=None, max_length=200)
    canonical_dataset_id: uuid.UUID | None = None
    # The first version's definition; without it the metric starts with no version.
    definition: MetricVersionCreate | None = None


class MetricPreview(BaseModel):
    periods: int | None = Field(default=None, ge=1, le=24)


class MetricVersionView(View):
    id: uuid.UUID
    metric_id: uuid.UUID
    version: int
    display_name: str
    description: str | None
    expression: str
    filters: Any
    unit: str | None
    decimals: int
    direction: str
    period_semantics: str
    time_grain: str
    allowed_dimensions: list[uuid.UUID]
    additivity: str
    legal_basis: str | None
    override_id: uuid.UUID | None
    change_note: str | None
    status: str
    certified_by: uuid.UUID | None
    certified_at: datetime.datetime | None
    valid_from: datetime.datetime | None
    valid_to: datetime.datetime | None
    proposal_id: uuid.UUID | None
    created_at: datetime.datetime


class MetricView(View):
    id: uuid.UUID
    data_space_id: uuid.UUID
    code: str
    owner_id: uuid.UUID
    consumer_group: str | None
    canonical_dataset_id: uuid.UUID | None
    pack_metric_id: uuid.UUID | None
    current_version: int | None
    in_filed_report: bool
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime
    # Display name of the current version, else of the latest one.
    display_name: str | None = None
    certified: bool = False


class MetricUsage(BaseModel):
    """Where the metric is used, as far as schema context can see (data products, pages)."""

    data_product_ids: list[uuid.UUID] = Field(default_factory=list)
    knowledge_page_ids: list[uuid.UUID] = Field(default_factory=list)


class MetricDetail(MetricView):
    versions: list[MetricVersionView] = Field(default_factory=list)
    usage: MetricUsage = Field(default_factory=MetricUsage)


# ---------------------------------------------------------------- relationships, synonyms, examples


class RelationshipCreate(BaseModel):
    from_dataset_id: uuid.UUID
    from_columns: list[str] = Field(min_length=1, max_length=16)
    to_dataset_id: uuid.UUID
    to_columns: list[str] = Field(min_length=1, max_length=16)
    cardinality: Literal["one_to_one", "many_to_one", "one_to_many"]

    @model_validator(mode="after")
    def _pairs(self) -> RelationshipCreate:
        if len(self.from_columns) != len(self.to_columns):
            raise ValueError("from_columns and to_columns pair up one to one")
        return self


class RelationshipView(View):
    id: uuid.UUID
    data_space_id: uuid.UUID
    from_dataset_id: uuid.UUID
    from_columns: list[str]
    to_dataset_id: uuid.UUID
    to_columns: list[str]
    cardinality: str
    origin: str
    overlap_ratio: decimal.Decimal | None
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime


SynonymTarget = Literal["metric", "dimension", "measure", "entity", "value", "column"]


class SynonymCreate(BaseModel):
    phrase: str = Field(min_length=1, max_length=200)
    target_kind: SynonymTarget
    target_id: uuid.UUID
    target_value: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _value(self) -> SynonymCreate:
        if (self.target_kind == "value") != (self.target_value is not None):
            raise ValueError("target_value is required with target_kind value, and only with it")
        return self


class SynonymView(View):
    id: uuid.UUID
    data_space_id: uuid.UUID
    phrase: str
    phrase_norm: str
    target_kind: str
    target_id: uuid.UUID
    target_value: str | None
    origin: str
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime
    # Set when the phrase already meant something else: the term that now holds both meanings
    # (UC-07-02 E1).
    glossary_term_id: uuid.UUID | None = None


class ExampleCreate(BaseModel):
    question: str = Field(min_length=1, max_length=2_000)
    sql: str = Field(min_length=1, max_length=20_000)
    metric_ids: list[uuid.UUID] | None = None


class ExampleView(View):
    id: uuid.UUID
    data_space_id: uuid.UUID
    question: str
    sql: str
    metric_ids: list[uuid.UUID]
    origin: str
    last_verified_at: datetime.datetime | None
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime


# ---------------------------------------------------------------- glossary (UC-07-09)


class TermCreate(BaseModel):
    term: str = Field(min_length=1, max_length=200)
    definition: str = Field(min_length=1, max_length=4_000)
    meanings: list[dict[str, Any]] | None = Field(default=None, max_length=20)
    default_meaning: int | None = Field(default=None, ge=0)
    legal_basis: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def _default_in_meanings(self) -> TermCreate:
        if self.default_meaning is not None and self.default_meaning >= len(self.meanings or []):
            raise ValueError("default_meaning is an index into meanings")
        return self


class TermView(View):
    id: uuid.UUID
    data_space_id: uuid.UUID
    term: str
    term_norm: str
    definition: str
    meanings: Any
    default_meaning: int | None
    legal_basis: str | None
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime


class UnmappedPhraseView(View):
    id: uuid.UUID
    data_space_id: uuid.UUID
    phrase_norm: str
    sample_questions: list[str]
    ask_count: int
    suggested_target: Any
    first_seen_at: datetime.datetime
    last_seen_at: datetime.datetime
    status: str


class TermPreference(BaseModel):
    glossary_term_id: uuid.UUID
    meaning_index: int = Field(ge=0)


class TermPreferencesPut(BaseModel):
    preferences: list[TermPreference] = Field(max_length=500)


class UserTermPrefView(View):
    id: uuid.UUID
    data_space_id: uuid.UUID
    user_id: uuid.UUID
    glossary_term_id: uuid.UUID
    meaning_index: int
    created_at: datetime.datetime
    updated_at: datetime.datetime


# ---------------------------------------------------------------- knowledge pages (UC-07-10..12)

PageKind = Literal["metric", "entity", "source", "topic"]


class KnowledgePageCreate(BaseModel):
    kind: PageKind
    subject_kind: Literal[
        "metric",
        "organization",
        "product",
        "segment",
        "branch",
        "channel",
        "dataset",
        "document_collection",
        "crawl_source",
        "topic",
    ]
    subject_ref: str | None = Field(default=None, max_length=200)
    slug: str = Field(pattern=SLUG)
    title: str = Field(min_length=1, max_length=300)
    index_summary: str | None = Field(default=None, max_length=300)
    tags: list[str] | None = Field(default=None, max_length=30)
    owner_id: uuid.UUID


class KnowledgePageView(View):
    id: uuid.UUID
    data_space_id: uuid.UUID
    kind: str
    subject_kind: str
    subject_ref: str | None
    slug: str
    title: str
    index_summary: str
    tags: list[str]
    owner_id: uuid.UUID
    status: str
    stale_reason: str | None
    current_version_id: uuid.UUID | None
    latest_version_no: int
    required_data_space_ids: list[uuid.UUID]
    classification: str
    pii_column_refs: list[str]
    has_external_source: bool
    certification_suspended: bool
    policy_state: str
    policy_computed_at: datetime.datetime | None
    last_read_at: datetime.datetime | None
    read_count_30d: int
    created_at: datetime.datetime
    updated_at: datetime.datetime


class KnowledgeIndexEntry(View):
    """One line of the index the planner reads before querying (UC-07-10)."""

    id: uuid.UUID
    slug: str
    kind: str
    subject_kind: str
    subject_ref: str | None
    title: str
    index_summary: str
    tags: list[str]
    status: str
    classification: str
    current_version_id: uuid.UUID | None
    has_external_source: bool
    certification_suspended: bool
    updated_at: datetime.datetime


class SourceIn(BaseModel):
    # Joins this source to the claims and figures that cite it within the request.
    source_key: str = Field(min_length=1, max_length=64)
    source_kind: Literal[
        "conflict_claim",
        "dataset_version",
        "document_version",
        "document_chunk",
        "metric_version",
        "answer",
        "glossary_term",
        "knowledge_page",
    ]
    source_ref: uuid.UUID
    source_label: str = Field(min_length=1, max_length=300)
    dataset_version_id: uuid.UUID | None = None
    external: bool = False
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    data_space_id: uuid.UUID | None = None
    classification: Classification = "internal"
    pii_column_refs: list[str] = Field(default_factory=list, max_length=100)


class ClaimIn(BaseModel):
    anchor: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    statement: str = Field(min_length=1, max_length=1_000)
    subject_key: str | None = Field(default=None, max_length=200)
    attribute: str | None = Field(default=None, max_length=200)
    period: str | None = Field(default=None, max_length=50)
    source_key: str
    valid_from: datetime.date
    valid_to: datetime.date | None = None
    figure_condition: dict[str, Any] | None = None


class FigureIn(BaseModel):
    token: str = Field(min_length=5, max_length=500)
    binding_kind: Literal["metric", "aggregate"]
    metric_code: str | None = Field(default=None, max_length=100)
    aggregate_code: str | None = Field(default=None, max_length=100)
    dataset_id: uuid.UUID | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    display_format: Literal["auto", "percent_2", "amount_billion", "count"] = "auto"
    source_key: str


class LinkIn(BaseModel):
    to_page_id: uuid.UUID | None = None
    to_slug: str | None = Field(default=None, pattern=SLUG)
    link_kind: Literal[
        "see_also", "defines", "part_of", "competitor_of", "uses_metric", "derived_from"
    ] = "see_also"

    @model_validator(mode="after")
    def _one_target(self) -> LinkIn:
        if (self.to_page_id is None) == (self.to_slug is None):
            raise ValueError("a link names to_page_id or to_slug")
        return self


class KnowledgeVersionCreate(BaseModel):
    body: str = Field(min_length=1, max_length=20_000)
    summary: str | None = Field(default=None, max_length=2_000)
    change_note: str | None = Field(default=None, max_length=2_000)
    change_size: Literal["minor", "major"] | None = None
    # saveback goes through :from-answer, exec_publish through the exec module.
    origin: Literal["manual", "ingest", "lint_fix"] | None = None
    knowledge_run_id: uuid.UUID | None = None
    sources: list[SourceIn] = Field(min_length=1, max_length=200)
    claims: list[ClaimIn] = Field(max_length=500)
    figures: list[FigureIn] | None = Field(default=None, max_length=500)
    links: list[LinkIn] | None = Field(default=None, max_length=200)


class KnowledgeVersionView(View):
    id: uuid.UUID
    page_id: uuid.UUID
    version: int
    parent_version_id: uuid.UUID | None
    status: str
    origin: str
    body: str
    body_sha256: str
    summary: str
    change_note: str | None
    change_size: str
    knowledge_run_id: uuid.UUID | None
    agent_run_id: uuid.UUID | None
    source_answer_id: uuid.UUID | None
    proposal_id: uuid.UUID | None
    reviewed_by: uuid.UUID | None
    reviewed_at: datetime.datetime | None
    certified_by: uuid.UUID | None
    certified_at: datetime.datetime | None
    created_at: datetime.datetime


class SourceView(View):
    id: uuid.UUID
    source_kind: str
    source_ref: uuid.UUID
    source_label: str
    dataset_version_id: uuid.UUID | None
    external: bool
    content_sha256: str
    data_space_id: uuid.UUID
    classification: str


class ClaimView(View):
    id: uuid.UUID
    anchor: str
    statement: str
    subject_key: str | None
    attribute: str | None
    period: str | None
    source_link_id: uuid.UUID
    valid_from: datetime.date
    valid_to: datetime.date | None
    figure_condition: Any


class FigureView(View):
    id: uuid.UUID
    token: str
    binding_kind: str
    metric_code: str | None
    aggregate_code: str | None
    dataset_id: uuid.UUID | None
    params: Any
    display_format: str
    source_link_id: uuid.UUID


class LinkView(View):
    id: uuid.UUID
    to_page_id: uuid.UUID
    link_kind: str


class RenderedKnowledgePage(BaseModel):
    """A served (or named) version with what a reader needs to show it. Figures are the
    placeholders' bindings: query.semantic fills them for the reader (BR-162), never this table."""

    page: KnowledgePageView
    version: KnowledgeVersionView | None
    as_of: datetime.date
    render: str
    claims: list[ClaimView] = Field(default_factory=list)
    figures: list[FigureView] = Field(default_factory=list)
    sources: list[SourceView] = Field(default_factory=list)
    links: list[LinkView] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    parent_body: str | None = None
    diff: str | None = None


class CertifyBody(BaseModel):
    note: str | None = Field(default=None, max_length=2_000)


class ReasonBody(BaseModel):
    reason: str = Field(min_length=1, max_length=2_000)


class FromAnswer(BaseModel):
    answer_id: uuid.UUID
    mode: Literal["new_page", "append"] | None = None
    target_page_id: uuid.UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)
    note: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def _target(self) -> FromAnswer:
        if self.mode == "append" and self.target_page_id is None:
            raise ValueError("mode append needs target_page_id")
        return self


class KnowledgeRunCreate(BaseModel):
    kind: Literal["ingest", "lint"]
    data_space_id: uuid.UUID
    source_kind: str | None = Field(default=None, max_length=50)
    source_ref: uuid.UUID | None = None
    page_limit: int | None = Field(default=None, ge=1, le=50)


class KnowledgeRunView(View):
    id: uuid.UUID
    data_space_id: uuid.UUID
    kind: str
    trigger: str
    trigger_event: str | None
    source_kind: str | None
    source_ref: uuid.UUID | None
    status: str
    page_limit: int
    sources_seen: int
    sources_skipped_unchanged: int
    pages_analyzed: int
    pages_changed: int
    pages_deferred: int
    findings_count: int
    hitl_count: int
    agent_run_id: uuid.UUID | None
    cost: decimal.Decimal | None
    started_at: datetime.datetime | None
    finished_at: datetime.datetime | None
    created_at: datetime.datetime


class RunVersionRef(View):
    id: uuid.UUID
    page_id: uuid.UUID
    version: int
    status: str


class KnowledgeRunDetail(KnowledgeRunView):
    page_versions: list[RunVersionRef] = Field(default_factory=list)


FindingAction = Literal[
    "propose_edit", "mark_stale", "retire_page", "create_page", "dismiss", "reassign"
]


class FindingResolve(BaseModel):
    action: FindingAction
    reason: str | None = Field(default=None, max_length=2_000)
    assignee_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _needs(self) -> FindingResolve:
        if self.action == "dismiss" and not (self.reason or "").strip():
            # BR-166: a finding is dismissed only with a reason.
            raise ValueError("dismiss needs a reason")
        if (self.action == "reassign") != (self.assignee_id is not None):
            raise ValueError("assignee_id is required with reassign, and only with it")
        return self


class FindingView(View):
    id: uuid.UUID
    run_id: uuid.UUID
    data_space_id: uuid.UUID
    page_id: uuid.UUID | None
    related_page_id: uuid.UUID | None
    kind: str
    severity: str
    fingerprint: str
    detail: Any
    status: str
    action: str | None
    action_reason: str | None
    page_version_id: uuid.UUID | None
    proposal_id: uuid.UUID | None
    hitl_request_id: uuid.UUID | None
    suppressed_until: datetime.datetime | None
    resolved_by: uuid.UUID | None
    resolved_at: datetime.datetime | None
    created_at: datetime.datetime


# ---------------------------------------------------------------- data products (UC-07-13)


class SampleQuestion(BaseModel):
    model_config = ConfigDict(extra="allow")

    text: str = Field(min_length=1, max_length=1_000)
    metric_code: str | None = None
    period: str | None = None
    expected: Any = None


class ScopeEntity(BaseModel):
    dimension: str = Field(min_length=1, max_length=100)
    values: list[str] = Field(default_factory=list, max_length=500)


PeriodGrain = Literal["day", "week", "month", "quarter", "year", "event"]

# P07 fields: the context layer's own; P06 fields: the published layer's (pipeline).
CONTEXT_REQUIRED = (
    "description",
    "period_grain",
    "period_from",
    "owner_id",
    "steward_id",
    "freshness_statement",
)
CONTEXT_ONLY = (
    *CONTEXT_REQUIRED,
    "sample_questions",
    "scope_entities",
    "period_to",
    "out_of_scope_note",
    "pack_version_id",
)
PUBLISHED_ONLY = ("dataset_id", "data_mart_id", "contract")


class DataProductCreate(BaseModel):
    """One operation for both layers (docs/v3/06-api-v1.md §1): `context` is the P07 data product
    (context.data_product), `published` the P06 shared product (pipeline.data_product)."""

    layer: Literal["context", "published"] = "context"
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=2_000)
    sample_questions: list[SampleQuestion] | None = Field(default=None, max_length=500)
    scope_entities: list[ScopeEntity] | None = Field(default=None, max_length=50)
    period_grain: PeriodGrain | None = None
    period_from: datetime.date | None = None
    period_to: datetime.date | None = None
    out_of_scope_note: str | None = Field(default=None, max_length=2_000)
    # Both layers name an owner (P07 owner, P06 approver of publishing; A3 12/10/2026).
    owner_id: uuid.UUID
    steward_id: uuid.UUID | None = None
    freshness_statement: str | None = Field(default=None, min_length=1, max_length=500)
    pack_version_id: uuid.UUID | None = None
    dataset_id: uuid.UUID | None = None
    data_mart_id: uuid.UUID | None = None
    contract: dict[str, Any] | None = None


class DataProductPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=2_000)
    sample_questions: list[SampleQuestion] | None = Field(default=None, max_length=500)
    scope_entities: list[ScopeEntity] | None = Field(default=None, max_length=50)
    period_from: datetime.date | None = None
    period_to: datetime.date | None = None
    out_of_scope_note: str | None = Field(default=None, max_length=2_000)
    owner_id: uuid.UUID | None = None
    steward_id: uuid.UUID | None = None
    freshness_statement: str | None = Field(default=None, min_length=1, max_length=500)


class DataProductView(View):
    layer: Literal["context"] = "context"
    id: uuid.UUID
    data_space_id: uuid.UUID
    code: str
    name: str
    description: str
    sample_questions: Any
    scope_entities: Any
    period_grain: str
    period_from: datetime.date
    period_to: datetime.date | None
    out_of_scope_note: str | None
    owner_id: uuid.UUID
    steward_id: uuid.UUID
    freshness_statement: str
    classification: str
    eval_set_id: uuid.UUID | None
    pack_version_id: uuid.UUID | None
    version: int
    status: str
    suspended_reason: str | None
    proposal_id: uuid.UUID | None
    activated_by: uuid.UUID | None
    activated_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    # Worst state of the primary datasets: missing, overdue, unknown, fresh.
    freshness_state: str = "unknown"


class PublishedDataProductView(BaseModel):
    """The P06 row as the pipeline module returns it."""

    model_config = ConfigDict(extra="ignore")

    layer: Literal["published"] = "published"
    id: uuid.UUID
    data_space_id: uuid.UUID
    code: str
    name: str
    version: int
    dataset_id: uuid.UUID | None = None
    data_mart_id: uuid.UUID | None = None
    contract: Any = None
    consumers: list[uuid.UUID] = Field(default_factory=list)
    status: str
    breaking_notice_at: datetime.datetime | None = None
    retire_after: datetime.datetime | None = None
    approved_by: uuid.UUID | None = None
    owner_id: uuid.UUID
    created_at: datetime.datetime
    updated_at: datetime.datetime


MemberKind = Literal["dataset", "metric", "knowledge_page", "document_collection", "shared_product"]


class MemberCreate(BaseModel):
    member_kind: MemberKind
    member_ref: uuid.UUID
    role: Literal["primary", "reference"] | None = None
    sla_kind: Literal["none", "schedule", "max_age"] | None = None
    sla_cron: str | None = Field(
        default=None, pattern=r"^\s*\S+(\s+\S+){4}\s*$", description="5-field cron, local time"
    )
    sla_event_calendar_code: str | None = Field(default=None, max_length=100)
    sla_timezone: str | None = Field(default=None, max_length=64)
    sla_work_calendar_id: uuid.UUID | None = None
    sla_max_age: datetime.timedelta | None = None
    sla_grace: datetime.timedelta | None = None
    as_of_col: str | None = Field(default=None, max_length=200)


class MemberView(View):
    id: uuid.UUID
    data_product_id: uuid.UUID
    member_kind: str
    member_ref: uuid.UUID
    role: str
    sla_kind: str
    sla_cron: str | None
    sla_event_calendar_code: str | None
    sla_timezone: str
    sla_work_calendar_id: uuid.UUID | None
    sla_max_age: datetime.timedelta | None
    sla_grace: datetime.timedelta
    as_of_col: str | None
    last_as_of: datetime.datetime | None
    last_loaded_at: datetime.datetime | None
    expected_as_of: datetime.datetime | None
    next_due_at: datetime.datetime | None
    freshness_state: str
    last_checked_at: datetime.datetime | None
    classification: str
    classification_confirmed_by: uuid.UUID | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class PeriodState(BaseModel):
    period: datetime.date
    # BR-178: shown, masked_stale (due and missing), not_available (not due yet).
    state: Literal["shown", "masked_stale", "not_available"]


class DatasetFreshness(BaseModel):
    member_id: uuid.UUID
    dataset_id: uuid.UUID
    sla_kind: str
    last_as_of: datetime.datetime | None
    last_loaded_at: datetime.datetime | None
    expected_as_of: datetime.datetime | None
    due_at: datetime.datetime | None
    next_due_at: datetime.datetime | None
    state: str
    periods: list[PeriodState] | None = None


class FreshnessReport(BaseModel):
    data_product_id: uuid.UUID
    at: datetime.datetime
    datasets: list[DatasetFreshness]


class DataProductDetail(DataProductView):
    members: list[MemberView] = Field(default_factory=list)
    freshness: list[DatasetFreshness] = Field(default_factory=list)
    # Held by the eval module (eval.eval_run, eval.error_book_entry); svc_context has no
    # eval_read grant, so these stay null until the eval module exposes them.
    latest_eval_run: dict[str, Any] | None = None
    open_error_book_entries: int | None = None


class DataProductSubmitted(Accepted):
    """202 of :submit: `id` is the eval run, named again as A3 asks."""

    eval_run_id: uuid.UUID


class SubmitBody(BaseModel):
    note: str | None = Field(default=None, max_length=2_000)


class ScopeMatch(BaseModel):
    data_product_id: uuid.UUID
    in_scope: bool
    reasons: list[str] = Field(default_factory=list)


class DataProductScope(BaseModel):
    """One active data product as the Understander matches it (dataproduct.match, G5.16)."""

    id: uuid.UUID
    code: str
    name: str
    version: int
    metric_codes: list[str]
    scope_entities: Any
    period_grain: str
    period_from: datetime.date
    period_to: datetime.date | None
    out_of_scope_note: str | None
    freshness_statement: str
    classification: str
    match: ScopeMatch | None = None
