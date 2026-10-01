"""How a source is read: the V2 keys of `KBSourceConfig.config` (14 section 3, IN-2).

Physical reading only — which file, sheet, selector and locale. What a column
*means* (which series it feeds) is `MappingRules`, kept apart so that a meaning
change goes through review while a reading change is caught as drift by the
profile fingerprint (14 section 6.1).

Every source in docs/v2/data/sources-oil-gold.yaml parses with these models
(tests/unit/contracts/test_seed.py).
"""

from __future__ import annotations

import re
from datetime import timedelta
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from libs.contracts.v1.base import ClockTime, ContractModel, IanaTimezone, NonEmptyStr
from libs.contracts.v1.observation import ObsStatus


def _compiles(pattern: str | None) -> str | None:
    if pattern is not None:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"not a valid regular expression: {exc}") from None
    return pattern


class UseRights(StrEnum):
    INTERNAL_ANALYSIS = "internal_analysis"
    PENDING_REVIEW = "pending_review"
    BLOCKED = "blocked"  # the worker does not run the source


class Purpose(StrEnum):
    PRODUCTION = "production"
    EVALUATION = "evaluation"  # labelled "dữ liệu thử" wherever it is shown


class RateLimit(ContractModel):
    min_interval_s: int = Field(ge=1)


# ── ReleaseInfo: where a release's publication time is read from ─────────────


class _ReleaseInfoBase(ContractModel):
    pass


class XlsCellTime(_ReleaseInfoBase):
    """A labelled cell (EIA `Contents` "Release Date:", Pink Sheet "Updated on …")."""

    from_: Literal["xls_cell"] = Field(alias="from")
    sheet: NonEmptyStr
    label: str | None = None
    label_prefix: str | None = None
    time: ClockTime | None = Field(
        default=None, description="Clock time when the cell has a date only"
    )
    tz: IanaTimezone | None = None

    @model_validator(mode="after")
    def _one_label(self) -> Self:
        if (self.label is None) == (self.label_prefix is None):
            raise ValueError("set exactly one of label, label_prefix")
        return self


class SdmxDatasetAttrTime(_ReleaseInfoBase):
    """An attribute of the SDMX DataSet (IMF PUBLICATION_DATE is not in the Header)."""

    from_: Literal["sdmx_dataset_attr"] = Field(alias="from")
    attr: NonEmptyStr


class HtmlRegexTime(_ReleaseInfoBase):
    from_: Literal["html_regex"] = Field(alias="from")
    selector: NonEmptyStr
    pattern: NonEmptyStr
    format: NonEmptyStr = Field(description="strptime format of the matched text")
    tz: IanaTimezone

    @field_validator("pattern")
    @classmethod
    def _pattern_compiles(cls, pattern: str) -> str:
        _compiles(pattern)
        return pattern


class JsonMaxDateTime(_ReleaseInfoBase):
    """The latest date among the rows (LBMA: the largest `d`)."""

    from_: Literal["json_max_date"] = Field(alias="from")
    path: NonEmptyStr = Field(description="JSONPath of the date values")
    format: NonEmptyStr
    time: ClockTime | None = None
    tz: IanaTimezone


class XmlTextTime(_ReleaseInfoBase):
    from_: Literal["xml_text"] = Field(alias="from")
    path: NonEmptyStr = Field(description="XPath, e.g. /ExrateList/DateTime")
    format: NonEmptyStr
    tz: IanaTimezone


class FetchTime(_ReleaseInfoBase):
    """No publication time in the content: the fetch time stands in (time_basis first_seen)."""

    from_: Literal["fetch_time"] = Field(alias="from")


ReleaseInfo = Annotated[
    XlsCellTime | SdmxDatasetAttrTime | HtmlRegexTime | JsonMaxDateTime | XmlTextTime | FetchTime,
    Field(discriminator="from_"),
]


class ReleaseTimes(ContractModel):
    """`release_info` of a source: where to read this release's times."""

    publication_time: ReleaseInfo
    next_release_time: ReleaseInfo | None = None


# ── ReleaseCalendar: publication date inferred for historical numbers (IN-5) ─


class Weekday(StrEnum):
    MON = "MON"
    TUE = "TUE"
    WED = "WED"
    THU = "THU"
    FRI = "FRI"
    SAT = "SAT"
    SUN = "SUN"


class WeeklyAfterCalendar(ContractModel):
    """First `weekday time tz` at or after the period end (EIA spot, stocks)."""

    kind: Literal["weekly_after"]
    weekday: Weekday
    time: ClockTime
    tz: IanaTimezone


class DailyLagCalendar(ContractModel):
    """Period end plus `lag` (LBMA PM: end = midnight London)."""

    kind: Literal["daily_lag"]
    lag: timedelta = Field(default=timedelta(0), description="ISO-8601 duration, e.g. PT0S")
    tz: IanaTimezone


class MonthlyDayCalendar(ContractModel):
    """Day `day` of the month after the period end, at `time` (IMF PCPS 10, Pink Sheet 5)."""

    kind: Literal["monthly_day"]
    day: int = Field(ge=1, le=28, description="≤ 28 so every month has the day")
    time: ClockTime
    tz: IanaTimezone


class NoCalendar(ContractModel):
    """Cannot be inferred: historical numbers get time_basis first_seen."""

    kind: Literal["none"]


ReleaseCalendar = Annotated[
    WeeklyAfterCalendar | DailyLagCalendar | MonthlyDayCalendar | NoCalendar,
    Field(discriminator="kind"),
]


# ── ExtractionProfile: how the bytes become rows ────────────────────────────


class _ProfileBase(ContractModel):
    locale: str = Field(pattern=r"^[a-z]{2}-[A-Z]{2}$", description="en-US, vi-VN, …")
    missing_markers: dict[str, ObsStatus] = Field(
        default_factory=dict, description='Source text → status, e.g. {"NA": "not_published"}'
    )
    row_filter: dict[str, str | list[str]] | None = None

    @field_validator("missing_markers")
    @classmethod
    def _markers_are_not_values(cls, markers: dict[str, ObsStatus]) -> dict[str, ObsStatus]:
        if ObsStatus.VALUE in markers.values():
            raise ValueError("a missing marker cannot map to 'value'")
        return markers


class CsvProfile(_ProfileBase):
    kind: Literal["csv"]
    delimiter: str = Field(default=",", min_length=1, max_length=1)
    header_row: int = Field(default=1, ge=1)
    archive_member: str | None = Field(default=None, description="File inside a zip (JODI)")


class XlsxTableProfile(_ProfileBase):
    """.xls and .xlsx."""

    kind: Literal["xlsx_table"]
    sheet: NonEmptyStr
    header_row: int = Field(ge=1)
    unit_row: int | None = Field(default=None, ge=1)
    archive_member: str | None = None


class JsonPathProfile(_ProfileBase):
    kind: Literal["json_path"]
    rows: NonEmptyStr = Field(description="JSONPath of the row array, e.g. $.gold.pm")


class SdmxMessage(StrEnum):
    STRUCTURE_SPECIFIC = "structure_specific"
    GENERIC = "generic"


class SdmxProfile(_ProfileBase):
    kind: Literal["sdmx"]
    message: SdmxMessage = SdmxMessage.STRUCTURE_SPECIFIC


class HtmlTableProfile(_ProfileBase):
    """CSS selectors for the table, its rows and named cells — also for card-style div blocks."""

    kind: Literal["html_table"]
    table: str | None = None
    rows: NonEmptyStr
    cells: dict[str, NonEmptyStr] = Field(min_length=1, description="cell name → selector")
    unit_text: str | None = Field(default=None, description="Selector of the unit caption")


class XmlPathProfile(_ProfileBase):
    kind: Literal["xml_path"]
    rows: NonEmptyStr = Field(description="XPath of the row elements")


ExtractionProfile = Annotated[
    CsvProfile
    | XlsxTableProfile
    | JsonPathProfile
    | SdmxProfile
    | HtmlTableProfile
    | XmlPathProfile,
    Field(discriminator="kind"),
]


class SourceConfigV2(ContractModel):
    """The V2 keys of a source config. Bulk Import and the form both write through it (NOP-122).

    Only `licence` and `use_rights` apply to every source; a document or
    manual-upload source has no extraction profile or calendar.
    """

    licence: NonEmptyStr
    use_rights: UseRights
    purpose: Purpose = Purpose.PRODUCTION
    entity: str | None = Field(default=None, description="Hint for reverse schema, not a mapping")
    rate_limit: RateLimit | None = None
    follow_link_pattern: str | None = None
    freshness_sla: timedelta | None = Field(
        default=None,
        description="ISO-8601 duration; no new release within it → source.sla.breached",
    )
    release_info: ReleaseTimes | None = None
    release_calendar: ReleaseCalendar | None = None
    extraction_profile: ExtractionProfile | None = None
    capture_response: str | None = Field(
        default=None,
        pattern=r"^https?://",
        description="URL of the response a headless crawl captures (LBMA today.json)",
    )

    @field_validator("follow_link_pattern")
    @classmethod
    def _link_pattern_compiles(cls, pattern: str | None) -> str | None:
        return _compiles(pattern)

    @field_validator("freshness_sla")
    @classmethod
    def _positive_sla(cls, sla: timedelta | None) -> timedelta | None:
        if sla is not None and sla <= timedelta(0):
            raise ValueError("freshness_sla must be positive")
        return sla
