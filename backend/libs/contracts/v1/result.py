"""Provider result envelopes and query provenance."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import Field

from libs.contracts.v1.base import ContractModel, NonEmptyStr


class ResultColumn(ContractModel):
    """A column in a provider result."""

    name: NonEmptyStr
    type: NonEmptyStr


class ResultWarning(ContractModel):
    """A non-fatal condition associated with a provider result."""

    code: NonEmptyStr
    message: NonEmptyStr


class ResultProvenance(ContractModel):
    """Evidence tying result rows to the governed query execution."""

    query_run_id: UUID
    source_id: UUID
    trust: NonEmptyStr
    sql_sha256: str | None = None


class ResultEnvelope(ContractModel):
    """A provider result with data, warnings, provenance, or an execution error."""

    columns: list[ResultColumn] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    warnings: list[ResultWarning] = Field(default_factory=list)
    provenance: ResultProvenance | None = None
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        """Return the wire payload with dates, UUIDs, and decimals JSON-compatible."""

        return self.model_dump(mode="json")
