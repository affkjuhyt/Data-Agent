"""Wire shapes of the Kiểm toán group (tab 4 A3), over audit.* in the dataagent_audit database."""

from __future__ import annotations

import datetime
import ipaddress
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _ip_text(value: Any) -> Any:
    # SQLAlchemy returns inet as ipaddress.IPv4Address/IPv6Address. Leaving that
    # for pydantic to treat as str fails the whole GET /v1/audit response.
    if isinstance(value, ipaddress.IPv4Address | ipaddress.IPv6Address):
        return str(value)
    return value


class AuditFilters(BaseModel):
    """The filters of GET /v1/audit, also the `filters` of an export."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    actor_id: uuid.UUID | None = None
    on_behalf_of: uuid.UUID | None = None
    object_kind: str | None = None
    object_id: uuid.UUID | None = None
    action: str | None = None
    outcome: Literal["ok", "denied", "error"] | None = None
    from_: datetime.datetime | None = Field(default=None, alias="from")
    to: datetime.datetime | None = None
    agent_run_id: uuid.UUID | None = None


class AuditLogView(BaseModel):
    """One audit_log row; before/after were masked of personal data when written (BR-138)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    seq: int
    occurred_at: datetime.datetime
    actor_kind: str
    actor_id: uuid.UUID | None
    on_behalf_of: uuid.UUID | None
    data_space_id: uuid.UUID | None
    service: str
    action: str
    object_kind: str | None
    object_id: uuid.UUID | None
    outcome: str
    before: Any | None
    after: Any | None
    agent_run_id: uuid.UUID | None
    ip: Annotated[str | None, BeforeValidator(_ip_text)]
    prev_hash: str
    hash: str


class VerifyIn(BaseModel):
    from_seq: int | None = Field(default=None, ge=1)


class VerifyResult(BaseModel):
    ok: bool
    broken_at_seq: int | None
    checked_rows: int


class ExportIn(BaseModel):
    filters: AuditFilters
    format: Literal["csv", "jsonl"] = "csv"


class ExportAccepted(BaseModel):
    """A1.6: the export is built and signed by a worker; poll Location."""

    id: uuid.UUID
    status: Literal["queued", "waiting_approval"]
    row_count: int
    poll_after_ms: int = 2_000


class ExportJobView(BaseModel):
    """ExportJob (A3). object_key stays inside: the file is fetched by a signed link, not a path."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filters: dict[str, Any]
    format: str
    row_count: int | None
    status: str
    requested_by: uuid.UUID
    approved_by: uuid.UUID | None
    approved_at: datetime.datetime | None
    signature: str | None
    expires_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class SecurityEventView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    occurred_at: datetime.datetime
    kind: str
    severity: str
    data_space_id: uuid.UUID | None
    actor_id: uuid.UUID | None
    source_ref: str | None
    detail: dict[str, Any]
    status: str
    handled_by: uuid.UUID | None
