"""Request and response shapes of the catalog module (tab 4 A3, group "Nguồn và danh mục").

Responses are the table's columns minus org_id, created_by, updated_by, deleted_at and every
secret reference (A3 "Trả về"). A source stores only OpenBao paths (secret_ref,
writeback_secret_ref), never a password or token: the request takes `credentials` as a reference
and refuses any other key (BR-14); the response says only whether one is set.
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SourceKind = Literal[
    "postgresql",
    "oracle",
    "sqlserver",
    "mysql",
    "snowflake",
    "bigquery",
    "databricks",
    "clickhouse",
    "kafka_cdc",
    "object_store",
    "file",
    "sftp",
    "sharepoint",
    "rest_api",
    "other",
]
SourceMode = Literal["federated", "ingest"]
AuthorityLevel = Literal["level_1", "level_2", "level_3", "reference"]
# What a person may set; draft, testing and needs_fix follow from tests and checks.
SettableSourceStatus = Literal["active", "paused", "archived"]
DescriptionStatus = Literal["none", "suggested", "approved"]
PiiLabel = Literal["national_id", "phone", "email", "account_no", "name", "address", "other"]


class SecretReference(BaseModel):
    """Where the account lives in OpenBao (BR-14): a path under the organisation's namespace
    `secret/tenants/{org_id}/`. A password, token or any other key is refused."""

    model_config = ConfigDict(extra="forbid")

    secret_ref: str = Field(min_length=1, max_length=512)


class _Limits(BaseModel):
    # BR-99 ranges: A3 answers 422 validation_failed outside them, before the CHECK would.
    max_concurrency: int | None = Field(default=None, ge=1, le=64)
    query_timeout_s: int | None = Field(default=None, ge=5, le=3600)


class SourceCreate(_Limits):
    name: str = Field(min_length=1, max_length=200)
    kind: SourceKind
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    database_name: str | None = Field(default=None, max_length=255)
    # Default: federated for a database or warehouse, ingest for the rest (UC-04-01 step 3).
    mode: SourceMode | None = None
    authority_level: AuthorityLevel | None = None
    freshness_sla: datetime.timedelta | None = None
    scan_schemas: list[str] | None = Field(default=None, max_length=1000)
    scan_window: dict[str, Any] | None = None
    work_calendar_id: uuid.UUID | None = None
    credentials: SecretReference | None = None
    # A separate write account, only for write-back (UC-06-04); none means no write-back.
    writeback_credentials: SecretReference | None = None


class SourcePatch(_Limits):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    authority_level: AuthorityLevel | None = None
    freshness_sla: datetime.timedelta | None = None
    scan_schemas: list[str] | None = Field(default=None, max_length=1000)
    scan_window: dict[str, Any] | None = None
    status: SettableSourceStatus | None = None
    credentials: SecretReference | None = None
    # UC-06-04: the tables a writeback may target, schema.table each; [] allows none.
    writeback_tables: (
        list[Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")]]
        | None
    ) = Field(default=None, max_length=500)


class ScheduleUpdate(BaseModel):
    """PUT: the four values replace the stored ones; null clears a value."""

    scan_cron: str | None = Field(default=None, max_length=200)
    profile_cron: str | None = Field(default=None, max_length=200)
    scan_window: dict[str, Any] | None = None
    work_calendar_id: uuid.UUID | None = None


class ScanRequest(BaseModel):
    kind: Literal["schema", "profile"] = "schema"
    # Only these tables (schema.table); all tables of scan_schemas when absent.
    tables: list[str] | None = Field(default=None, max_length=10_000)


class ActivateTable(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")
    data_space_id: uuid.UUID
    owner_id: uuid.UUID


class ActivateRequest(BaseModel):
    tables: list[ActivateTable] = Field(default_factory=list, max_length=10_000)
    schema_scan: str = Field(default="daily_0200", max_length=200)
    profiling: str = Field(default="weekly_sun_0300", max_length=200)
    on_schema_change: Literal["pause_and_ask", "continue_and_notify"] = "pause_and_ask"


class Accepted(BaseModel):
    """A1.6: the body of a 202; Location names where the result appears."""

    id: uuid.UUID
    status: str
    poll_after_ms: int = 2_000


class SourceView(BaseModel):
    id: uuid.UUID
    data_space_id: uuid.UUID
    name: str
    kind: str
    connector_icon: str | None
    host: str | None
    port: int | None
    database_name: str | None
    conn_options: Any
    # A3 drops every secret reference from a response: the OpenBao paths are write-only, the
    # response only says whether one is set.
    has_credentials: bool = False
    has_writeback_credentials: bool = False
    writeback_tables: list[str] = Field(default_factory=list)
    mode: str
    authority_level: str
    freshness_sla: datetime.timedelta | None
    max_concurrency: int
    query_timeout_s: int
    scan_schemas: list[str]
    scan_cron: str | None
    profile_cron: str | None
    scan_window: Any
    work_calendar_id: uuid.UUID | None
    status: str
    status_reason: str | None
    last_checked_at: datetime.datetime | None
    db_version: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime

    @classmethod
    def of(cls, row: Any) -> SourceView:
        view = cls.model_validate(row, from_attributes=True)
        view.has_credentials = bool(row.secret_ref)
        view.has_writeback_credentials = bool(row.writeback_secret_ref)
        return view


class ConnectorForm(BaseModel):
    database_label: str
    default_port: int


class ConnectorView(BaseModel):
    """A connector the connection wizard may offer; no credentials are included."""

    code: str
    name: str
    category: Literal["database", "warehouse", "app", "file", "document"]
    subtitle: str | None
    connected_count: int
    badge: Literal["new", "upload", "engineering"] | None
    form: ConnectorForm | None
    prep_notes: list[str]
    sample_sql: str | None


class SecretRefView(BaseModel):
    """A labelled OpenBao reference already used in this Data Space, never its value."""

    ref: str
    label: str


class ApiConfigPut(BaseModel):
    """The request/mapping kept with a REST source; credentials remain references only."""

    request: dict[str, Any]
    mapping: list[dict[str, Any]] = Field(default_factory=list)


class ApiConfigView(ApiConfigPut):
    source_id: uuid.UUID
    name: str
    health: dict[str, Any] | None = None


class RestTryRequest(BaseModel):
    request: dict[str, Any]


class RestTryResult(BaseModel):
    status: int
    duration_ms: int
    body: str
    mapping: list[dict[str, Any]] = Field(default_factory=list)


class SourceSchedule(SourceView):
    """The source and its next five scans of each kind (UC-04-05 step 3)."""

    next_scans: list[datetime.datetime] = Field(default_factory=list)
    next_profiles: list[datetime.datetime] = Field(default_factory=list)


class ScanRunView(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    kind: str
    trigger: str
    workflow_id: str | None
    status: str
    tables_total: int | None
    tables_scanned: int
    tables_skipped: Any
    resume_from: str | None
    started_at: datetime.datetime
    finished_at: datetime.datetime | None
    created_at: datetime.datetime


class DatasetView(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID | None
    data_space_id: uuid.UUID
    origin: str
    schema_name: str
    table_name: str
    object_kind: str
    display_name: str | None
    description: str | None
    description_status: str
    row_estimate: int | None
    history_kind: str
    business_date_col: str | None
    knowledge_time_col: str | None
    enabled: bool
    steward_id: uuid.UUID | None
    scope: str
    expires_at: datetime.datetime | None
    current_version: int
    quality_state: str
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ColumnProfileView(BaseModel):
    scan_run_id: uuid.UUID
    sample_rows: int
    null_ratio: decimal.Decimal | None
    distinct_est: int | None
    min_value: str | None
    max_value: str | None
    top_values: Any
    pattern_hint: str | None
    created_at: datetime.datetime


class ColumnView(BaseModel):
    id: uuid.UUID
    dataset_version: int
    name: str
    ordinal: int
    data_type: str
    normalized_type: str
    is_nullable: bool
    is_key: bool
    display_name: str | None
    description: str | None
    description_status: str
    unit: str | None
    pii_label: str | None
    pii_confirmed: bool
    profile: ColumnProfileView | None = None


class ColumnStatsView(BaseModel):
    name: str
    type_label: str
    null_ratio: float
    distinct: int
    min: str
    max: str
    median: str
    histogram: list[dict[str, bool | float]] = Field(default_factory=list)
    buckets_text: str


class DatasetDetail(DatasetView):
    # The current columns (not removed) with their latest profile.
    columns: list[ColumnView] = Field(default_factory=list)


class ColumnPatch(BaseModel):
    id: uuid.UUID
    display_name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4_000)
    description_status: DescriptionStatus | None = None
    unit: str | None = Field(default=None, max_length=50)
    pii_label: PiiLabel | None = None
    pii_confirmed: bool | None = None


class DatasetPatch(BaseModel):
    enabled: bool | None = None
    steward_id: uuid.UUID | None = None
    display_name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4_000)
    description_status: DescriptionStatus | None = None
    business_date_col: str | None = Field(default=None, max_length=200)
    knowledge_time_col: str | None = Field(default=None, max_length=200)
    columns: list[ColumnPatch] | None = Field(default=None, max_length=5_000)


class DatasetDeriveRequest(BaseModel):
    filters: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    drop_duplicates: bool = False


class DerivedDatasetAccepted(BaseModel):
    id: uuid.UUID
    name: str
    row_count: int


class DerivedTableCreate(BaseModel):
    name: str = Field(
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$",
        max_length=255,
    )
    data_space_id: uuid.UUID
    write_mode: Literal["replace", "append", "merge"]
    refresh: Literal["after_etl", "hourly", "manual"]
    access: Literal["inherit", "owner_only"]
    sql: str = Field(min_length=1, max_length=100_000)


class DerivedTableView(BaseModel):
    id: uuid.UUID
    name: str
    data_space_id: uuid.UUID
    write_mode: Literal["replace", "append", "merge"]
    refresh: Literal["after_etl", "hourly", "manual"]
    access: Literal["inherit", "owner_only"]
    version: int


class DatasetVersionView(BaseModel):
    id: uuid.UUID
    dataset_id: uuid.UUID
    version: int
    schema_hash: str
    diff: Any
    change_kind: str
    proposal_id: uuid.UUID | None
    valid_from: datetime.datetime
    valid_to: datetime.datetime | None
    scan_run_id: uuid.UUID | None
    certified_at: datetime.datetime | None
    certified_by: uuid.UUID | None
    created_at: datetime.datetime


class CertificationView(BaseModel):
    id: uuid.UUID
    dataset_version_id: uuid.UUID
    action: str
    actor_id: uuid.UUID
    note: str | None
    reason: str | None
    created_at: datetime.datetime


class CertifiedVersion(DatasetVersionView):
    """The version after the change, with the history row just written (BR-156)."""

    certification: CertificationView


class CertifyRequest(BaseModel):
    note: str | None = Field(default=None, max_length=4_000)


class RevokeRequest(BaseModel):
    reason: str = Field(max_length=4_000)

    @field_validator("reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        # AC-14-05-07: a blank or whitespace-only reason is refused.
        if not value.strip():
            raise ValueError("reason must not be blank (BR-156)")
        return value.strip()
