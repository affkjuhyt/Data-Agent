"""Observations: a number with its provenance and four time axes (03 section 3.2, 14 section 7.1).

`ObservationCandidate` is what an extractor proposes; it has no effective version
until governance publishes it as an `Observation`. Lifecycle state (pending_dq,
in_review, …) is not on the wire: it belongs to the store that owns the row.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from libs.contracts.v1.base import DecimalValue, NonEmptyStr, Sha256, TimeAxes
from libs.contracts.v1.refs import SeriesKey, SourceRef, UnitCode
from libs.indicator.quantity import Quantity, Unit, UnitError


class ObsStatus(StrEnum):
    VALUE = "value"
    MISSING = "missing"
    NOT_PUBLISHED = "not_published"
    WITHHELD = "withheld"
    CONFIDENTIAL = "confidential"


class TimeBasis(StrEnum):
    """Where `available_at` came from (14 section 7.1)."""

    RELEASE_DECLARED = "release_declared"
    CALENDAR_INFERRED = "calendar_inferred"
    FIRST_SEEN = "first_seen"
    DOCUMENT_STATED = "document_stated"


class Origin(StrEnum):
    STRUCTURED_MAPPING = "structured_mapping"
    DOCUMENT_EXTRACTION = "document_extraction"
    MANUAL_ENTRY = "manual_entry"


class ChangeKind(StrEnum):
    NEW = "new"
    REVISION = "revision"


class VersionKind(StrEnum):
    MACHINE = "machine"
    CORRECTION = "correction"
    OVERRIDE = "override"
    REVISION = "revision"


class _ObservationBase(TimeAxes):
    series_key: SeriesKey
    # None only when the source publishes a missing-value marker, which obs_status
    # then names; never 0 in place of a missing number.
    value: DecimalValue | None
    obs_status: ObsStatus
    unit: UnitCode
    unit_multiplier: int = Field(default=0, ge=-18, le=18)
    source_ref: SourceRef
    release_id: UUID
    artifact_sha256: Sha256

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.obs_status is ObsStatus.VALUE) != (self.value is not None):
            raise ValueError("value is set exactly when obs_status is 'value'")
        if self.event_time_end < self.event_time_start:
            raise ValueError("event_time_end must not be before event_time_start")
        return self

    def to_quantity(self, currency: str | None = None) -> Quantity:
        """The value as a libs.indicator Quantity, for the numeric core.

        Only for units the numeric core knows (`libs.indicator.quantity.Unit`);
        a source unit such as vnd_per_chi raises UnitError until P0-04 adds it.
        """
        if self.value is None:
            raise UnitError(f"{self.series_key} has no value ({self.obs_status.value})")
        try:
            unit = Unit(self.unit)
        except ValueError:
            raise UnitError(f"unit {self.unit!r} is not a libs.indicator Unit") from None
        return Quantity(self.value, unit, self.unit_multiplier, currency)


class ObservationCandidate(_ObservationBase):
    """A number an extractor proposes, before data quality and review."""

    time_basis: TimeBasis
    origin: Origin
    period_code: NonEmptyStr = Field(
        description="Display spelling of the period: 2026-09-15, 2026-M08, 2026-W38, P:2398/QĐ-BCT"
    )
    raw_text: str | None = Field(
        default=None, description="The source cell as written: '25,760.00', '25.226', '…'"
    )
    change_kind: ChangeKind


class Observation(_ObservationBase):
    """An effective observation: one row per version in ClickHouse `obs.observation`."""

    version_kind: VersionKind
    effective_version: int = Field(ge=1)
    review_case_id: UUID | None = None
