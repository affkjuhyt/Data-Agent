"""State and results of reading a release (14 sections 5.2, 6.1, 7.2; dry run NOP-263)."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import Field

from libs.contracts.v1.base import ContractModel, NonEmptyStr, Sha256
from libs.contracts.v1.mapping import MappingRules
from libs.contracts.v1.observation import ObservationCandidate
from libs.contracts.v1.refs import JsonPathRef, ReleaseRef, TableCellRef, XPathRef
from libs.contracts.v1.source_config import ExtractionProfile


class ReleaseProcessingStatus(StrEnum):
    """Where a release is in its reading.

    Only series_extractor moves a release between them (ADR-V2-32):
    received → profiled → (blocked_drift | normalizing) → candidates_written | no_change | parse_error;
    the document path uses extracting.
    """

    RECEIVED = "received"
    PROFILED = "profiled"
    BLOCKED_DRIFT = "blocked_drift"
    NORMALIZING = "normalizing"
    CANDIDATES_WRITTEN = "candidates_written"
    PARSE_ERROR = "parse_error"
    EXTRACTING = "extracting"
    NO_CHANGE = "no_change"


class ColumnProfile(ContractModel):
    position: int = Field(ge=0)
    name_raw: str
    name_norm: str
    dtype: NonEmptyStr
    locale_guess: str | None = None
    unit_guess: str | None = None
    missing_markers_seen: list[str] = Field(default_factory=list)
    null_ratio: float = Field(ge=0, le=1)
    samples: list[str] = Field(default_factory=list, max_length=5)


class ColumnDiff(ContractModel):
    """Columns by normalised name, against the profile the approved mapping was based on."""

    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    changed: list[str] = Field(
        default_factory=list, description="Same name, different dtype or unit guess"
    )


class ProfileSummary(ContractModel):
    columns: list[ColumnProfile]
    fingerprint: Sha256 = Field(
        description="Structure signature: columns sorted by name + declared locale and markers"
    )
    diff_vs_approved: ColumnDiff | None = Field(
        default=None, description="None when the source has no approved mapping yet"
    )


class ParseErrorCode(StrEnum):
    AMBIGUOUS_NUMBER = "AMBIGUOUS_NUMBER"
    BAD_PERIOD = "BAD_PERIOD"
    UNKNOWN_MARKER = "UNKNOWN_MARKER"
    UNIT_MISMATCH = "UNIT_MISMATCH"


ParseLocation = Annotated[TableCellRef | JsonPathRef | XPathRef, Field(discriminator="kind")]


class ParseError(ContractModel):
    """One value that could not be read. Never guessed: `1.234` with an unknown locale stays an error."""

    location: ParseLocation
    raw_text: str | None = None
    error_code: ParseErrorCode
    message: NonEmptyStr


class DryRunRequest(ContractModel):
    """Read a release with a profile/mapping and show the result. Writes nothing.

    A missing `extraction_profile` / `mapping` means the source's current one; a
    missing `release_id` means its latest release.
    """

    source_id: UUID
    release_id: UUID | None = None
    extraction_profile: ExtractionProfile | None = None
    mapping: MappingRules | None = None
    limit: int = Field(default=100, ge=1, le=1000)


class DryRunResult(ContractModel):
    release: ReleaseRef
    profile: ProfileSummary
    candidates_preview: list[ObservationCandidate]
    parse_errors: list[ParseError]
    truncated: bool = Field(description="More candidates than `limit` were read")
