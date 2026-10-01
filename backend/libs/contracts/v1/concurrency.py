"""Optimistic concurrency (00 invariant 10: no last-write-wins).

Every write to governed data names the version it was based on. The owning
service compares it with the current row and answers 409 VERSION_CONFLICT with
`current_version` when they differ; it never overwrites.
"""

from __future__ import annotations

from pydantic import Field

from libs.contracts.v1.base import ContractModel


class VersionedWrite(ContractModel):
    """Base of every write DTO on a versioned object."""

    expected_version: int = Field(ge=1, description="The `version` the client last read")
