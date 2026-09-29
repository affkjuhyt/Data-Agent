"""Request bodies of the write routes. Every write carries the version it was based on."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from services.indicator_api.domains.governance import ColumnMapping


class MappingProposalUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    mappings: list[ColumnMapping] | None = Field(default=None, min_length=1)
    status: str | None = None


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    decision: str = Field(min_length=1, description="e.g. approved, rejected")
