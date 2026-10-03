"""References: the small, stable keys one branch uses to point at another's object.

A ref carries enough to find the object and to detect that it changed (`version`),
never the object itself — the full Entity, Series, ReviewCase or Capability is
owned by the service that stores it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from libs.contracts.v1.base import ContractModel, NonEmptyStr, Sha256

# Upper-case segments joined by ':' — EIA:RBRTE:D, PNJ:SJC_BAR:HCM:BUY:I.
SERIES_KEY_PATTERN = r"^[A-Z0-9][A-Z0-9_.\-]*(:[A-Z0-9][A-Z0-9_.\-]*)+$"
SeriesKey = Annotated[str, Field(pattern=SERIES_KEY_PATTERN, description="e.g. EIA:RBRTE:D")]

# D/W/M/Q/A plus I (instant) and P (event period). A plain code until P0-04 adds
# these to libs.indicator.period.Frequency; switching to that enum then is a
# breaking change and bumps SeriesRef.SCHEMA_VERSION (COMPATIBILITY.md).
FrequencyCode = Annotated[str, Field(pattern=r"^[DWMQAIP]$")]

# A unit code in snake_case (money, percent, vnd_per_chi, usd_per_barrel). Not the
# libs.indicator Unit enum yet: source units such as vnd_per_chi do not exist
# there until P0-04 (`money_per`).
UnitCode = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]


class EntityRef(ContractModel):
    entity_id: NonEmptyStr = Field(description="Stable id, e.g. BRENT_SPOT, GOLD_SJC_BAR")


class SeriesRef(ContractModel):
    series_key: SeriesKey
    entity_id: NonEmptyStr
    unit: UnitCode
    unit_multiplier: int = Field(
        default=0, ge=-18, le=18, description="Power of ten, as Quantity.multiplier"
    )
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    frequency: FrequencyCode


class ReviewCaseKind(StrEnum):
    SCHEMA_DRIFT = "SCHEMA_DRIFT"
    DQ_BLOCK = "DQ_BLOCK"
    CONFLICT = "CONFLICT"
    EXTRACTION_LOW_CONF = "EXTRACTION_LOW_CONF"


class ReviewCaseRef(ContractModel):
    case_id: UUID
    kind: ReviewCaseKind
    version: int = Field(ge=1, description="Send back as expected_version when deciding")


class CapabilityRef(ContractModel):
    capability_id: NonEmptyStr = Field(description="e.g. COMPARE_PERIOD")
    version: int = Field(ge=1)


class PublicationTimeBasis(StrEnum):
    SOURCE_DECLARED = "source_declared"
    FETCH_TIME = "fetch_time"


class ReleaseRef(ContractModel):
    """One new content of a source (14 section 5.1)."""

    release_id: UUID
    source_id: UUID
    artifact_sha256: Sha256
    release_seq: int = Field(ge=1)
    publication_time: AwareDatetime
    publication_time_basis: PublicationTimeBasis
    available_at: AwareDatetime


# ── Where in the artifact a value was read (provenance, invariant 8) ─────────


class TableCellRef(ContractModel):
    """A cell of a CSV/XLSX sheet, or a row/cell of an HTML table (`col` = the cell name)."""

    kind: Literal["cell"] = "cell"
    sheet: str | None = None
    row: int = Field(ge=0)
    col: str = Field(min_length=1)


class JsonPathRef(ContractModel):
    kind: Literal["json_path"] = "json_path"
    path: NonEmptyStr


class XPathRef(ContractModel):
    kind: Literal["xpath"] = "xpath"
    path: NonEmptyStr


class PageRef(ContractModel):
    """A region of a PDF page, in the same top-left-origin points Docling parsing stores.

    The shape is repeated rather than imported from libs.basemodel so the
    contract stays a leaf package (.importlinter `contracts-is-a-leaf`).
    """

    kind: Literal["page"] = "page"
    page_no: int = Field(ge=1)
    left: float
    top: float
    right: float
    bottom: float
    page_width: float = Field(gt=0)
    page_height: float = Field(gt=0)
    quote_id: str | None = None

    @model_validator(mode="after")
    def _box_is_ordered(self) -> PageRef:
        if self.right < self.left or self.bottom < self.top:
            raise ValueError("page box must have right >= left and bottom >= top")
        return self


class ApiQueryRef(ContractModel):
    """A value fetched from an API: the request that returns it again."""

    kind: Literal["api_query"] = "api_query"
    url: NonEmptyStr
    params: dict[str, Any] = Field(default_factory=dict)


SourceRef = Annotated[
    TableCellRef | JsonPathRef | XPathRef | PageRef | ApiQueryRef,
    Field(discriminator="kind"),
]


class EvidenceRef(ContractModel):
    evidence_id: UUID
    release_id: UUID
    artifact_sha256: Sha256
    source_ref: SourceRef
