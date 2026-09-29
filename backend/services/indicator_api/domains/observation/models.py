"""Observations: a number with its provenance and four time axes (03 section 3.2).

Required fields are the ones P0-09 lists; an observation missing any of them
is refused, never defaulted (P0-09 BR-2).
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field

from libs.basemodel.services_docling.docling_model import PageRegion
from services.indicator_api.domains.contract import ContractModel, TimeAxes

SHA256_HEX = r"^[0-9a-f]{64}$"


class VersionKind(StrEnum):
    MACHINE = "machine"
    CORRECTION = "correction"
    OVERRIDE = "override"
    REVISION = "revision"


class TableCellRef(ContractModel):
    """A cell in a CSV/XLSX artifact."""

    artifact: str
    sheet: str | None = None
    row: int = Field(ge=0)
    col: str


class PageRef(ContractModel):
    """A region on a PDF page, as Docling parsing already stores it (page_no + box)."""

    artifact: str
    region: PageRegion


class ApiQueryRef(ContractModel):
    """A value fetched from an API: the request that returns it again."""

    url: str
    params: dict[str, Any] = Field(default_factory=dict)


SourceRef = TableCellRef | PageRef | ApiQueryRef


class _ObservationBase(TimeAxes):
    series_key: str = Field(min_length=1)
    # Decimal, never float (docs/v2/12-numeric-core.md); None only when the
    # source publishes a missing-value code, which obs_status then carries.
    value: Decimal | None
    unit: str
    obs_status: str
    release_id: UUID
    artifact_sha256: str = Field(pattern=SHA256_HEX)
    source_ref: SourceRef


class ObservationCandidate(_ObservationBase):
    """What an extractor proposes; it has no effective version until governance publishes it."""


class Observation(_ObservationBase):
    """An effective observation, one row per version in ClickHouse `obs.observation`."""

    version_kind: VersionKind
    effective_version: int = Field(ge=1)
    review_case_id: UUID | None = None
