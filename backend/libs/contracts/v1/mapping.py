"""What the columns of a source mean: `ingest.mapping_version.rules` (14 section 6.2, IN-2).

Proposed by the LLM, the seed or a human; checked by a validator; approved by
someone other than its author. The rules themselves are immutable once stored —
a change is a new mapping version.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from libs.contracts.v1.base import ClockTime, ContractModel, IanaTimezone, NonEmptyStr
from libs.contracts.v1.refs import FrequencyCode, SeriesKey, UnitCode


class TimeRule(ContractModel):
    """Where the period of each row comes from.

    One selector: a `column` (by header name or 0-based position), a `field`
    (JSON/SDMX), or `from: release_publication_time` for sources that only ever
    show the current price (PNJ, DOJI, Vietcombank).
    """

    column: str | int | None = None
    field: str | None = None
    from_: Literal["release_publication_time"] | None = Field(default=None, alias="from")
    format: str | None = Field(
        default=None, description="excel_serial or a strptime format such as %Y-M%m"
    )
    frequency: FrequencyCode
    market_timezone: IanaTimezone
    anchor: Literal["period_start", "period_end"] | None = Field(
        default=None, description="Which end of the period the date names (EIA weekly: period_end)"
    )
    year_from: Literal["fetch_time"] | None = Field(
        default=None, description="The date has no year (LBMA today.json '29/09')"
    )
    session_time: ClockTime | None = Field(
        default=None, description="Clock time of the fixing when the date has none (LBMA PM 15:00)"
    )

    @model_validator(mode="after")
    def _one_selector(self) -> Self:
        chosen = [s for s in (self.column, self.field, self.from_) if s is not None]
        if len(chosen) != 1:
            raise ValueError("set exactly one of column, field, from")
        if self.from_ is None and self.format is None:
            raise ValueError("format is required when the time is read from a column or field")
        return self


class ValueRule(ContractModel):
    """One value the source publishes, and the series it feeds.

    Picked by `column` (table), `field` (JSON row or named HTML cell), `attr`
    (XML attribute), or `select` (row/dimension values: SDMX INDICATOR, the
    product name of a price board) — `select` combines with `field`.
    """

    column: str | int | None = None
    field: str | None = None
    attr: str | None = None
    select: dict[str, str] | None = None
    series_key: SeriesKey
    measure: NonEmptyStr
    # Set when the source states the value in another unit than the series
    # (PNJ: thousand VND per chỉ, series: million VND per lượng); the
    # normaliser converts with libs.indicator, never by hand in the mapping.
    source_unit: UnitCode | None = None
    source_multiplier: int | None = Field(default=None, ge=-18, le=18)

    @model_validator(mode="after")
    def _picks_one_value(self) -> Self:
        direct = [s for s in (self.column, self.field, self.attr) if s is not None]
        if len(direct) > 1:
            raise ValueError("set at most one of column, field, attr")
        if not direct and self.select is None:
            raise ValueError("set one of column, field, attr, or select")
        if self.source_multiplier is not None and self.source_unit is None:
            raise ValueError("source_multiplier needs source_unit")
        return self


class MappingRules(ContractModel):
    time: TimeRule
    values: list[ValueRule] = Field(min_length=1)
    side_columns: list[str] = Field(
        default_factory=list,
        description="Kept in source_ref but never turned into numbers (Vietcombank 'Transfer')",
    )

    @model_validator(mode="after")
    def _one_rule_per_series(self) -> Self:
        keys = [v.series_key for v in self.values]
        duplicates = sorted({k for k in keys if keys.count(k) > 1})
        if duplicates:
            raise ValueError(f"series_key mapped more than once: {duplicates}")
        return self
