"""Provider requests shared by the agent and data-source adapters."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from libs.contracts.v1.base import ContractModel, NonEmptyStr


class ProviderRequest(ContractModel):
    """The scoped input a provider receives for one data operation."""

    provider: NonEmptyStr
    org_id: UUID
    data_space_id: UUID
    principal_id: UUID | None = None
    source_id: UUID
    sql: NonEmptyStr
    caller_kind: NonEmptyStr
    caller_id: UUID
    as_of: date | None = None
