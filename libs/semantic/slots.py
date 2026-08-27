"""Analytical slots — the structured shape of an analytical request, and the
deterministic rules for refining it across turns.

A follow-up like "chỉ lấy miền Bắc" is not a new question; it is the previous
question with one filter added. With no structured state, the only thing carried
between turns was the chat transcript, so answering a follow-up meant re-reading
it and re-inventing the whole query — and every re-invention was free to drop a
filter, switch the date column, or silently change the aggregation. The number
moved and nothing said why.

Slots make the refinement an operation on data instead of an act of
re-interpretation: the previous request is a value, the follow-up is a small
delta, and `merge` combines them by rules that are the same every time.

Deliberately separate from conversation history. Chat history is what was
*said*; slots are what is currently *being asked*. Conflating them is why "chỉ
lấy miền Bắc" was previously answered by hoping a model would notice the earlier
turn's filters and re-emit them.

Pure — no I/O, no model calls. Feeds `compile_metric` directly.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any

from libs.semantic.models import FilterOp, MetricFilter, TimeGrain, validate_identifier

# Relative periods a follow-up can name. Resolved to concrete dates by the
# caller, which is the only layer that knows "today" — keeping this module
# free of a clock is what makes a merge reproducible in a test and in a replay.
RELATIVE_PERIODS = (
    "today", "yesterday", "this_week", "last_week",
    "this_month", "last_month", "this_quarter", "last_quarter",
    "this_year", "last_year",
)

# How a comparison question relates two periods.
COMPARE_PREVIOUS_PERIOD = "previous_period"
COMPARE_SAME_PERIOD_LAST_YEAR = "same_period_last_year"
COMPARISONS = (COMPARE_PREVIOUS_PERIOD, COMPARE_SAME_PERIOD_LAST_YEAR)


@dataclass(frozen=True)
class SlotFilter:
    """One caller-supplied predicate, keyed by the BUSINESS dimension name.

    Keyed by dimension rather than physical column on purpose: the merge rules
    below replace-by-key, and "miền" has to collide with "miền" even when the
    underlying column is renamed between metric versions.
    """

    dimension: str
    op: str = FilterOp.EQ.value
    values: tuple[Any, ...] = ()

    def __post_init__(self) -> None:
        if not self.dimension:
            raise ValueError("slot filter needs a dimension")

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> SlotFilter:
        values = raw.get("values", raw.get("value"))
        if values is None:
            values = ()
        elif not isinstance(values, (list, tuple)):
            values = (values,)
        return SlotFilter(
            dimension=str(raw.get("dimension", raw.get("column", ""))).strip().lower(),
            op=str(raw.get("op", FilterOp.EQ.value)).strip().lower(),
            values=tuple(values),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"dimension": self.dimension, "op": self.op, "values": list(self.values)}

    def to_metric_filter(self, column: str) -> MetricFilter:
        """Bind to a physical column and hand to the compiler.

        A single value under an IN op collapses to EQ (and vice versa) so the
        emitted SQL reads naturally either way; the column is validated by
        MetricFilter itself.
        """
        validate_identifier(column, f"filter column for dimension {self.dimension!r}")
        op = FilterOp(self.op)
        if op in (FilterOp.IN, FilterOp.NOT_IN):
            return MetricFilter(column=column, op=op, value=list(self.values))
        if op in (FilterOp.IS_NULL, FilterOp.IS_NOT_NULL):
            return MetricFilter(column=column, op=op, value=None)
        if len(self.values) > 1:
            # "miền Bắc và miền Nam" under an EQ op is an IN, not a
            # contradiction. Silently emitting `= 'Bắc'` and dropping the rest
            # would answer a different question than the one asked.
            return MetricFilter(column=column, op=FilterOp.IN, value=list(self.values))
        return MetricFilter(column=column, op=op, value=self.values[0] if self.values else None)


@dataclass(frozen=True)
class AnalyticalSlots:
    """What is currently being asked, in structured form."""

    metric: str = ""
    dimensions: tuple[str, ...] = ()
    filters: tuple[SlotFilter, ...] = ()
    # Concrete ISO bounds, half-open [start, end).
    time_range: tuple[str, str] | None = None
    # Named period the caller has not yet resolved to dates.
    relative_period: str = ""
    grain: str = ""
    compare_to: str = ""
    # Dimensions whose filter this turn explicitly REMOVES ("bỏ lọc miền").
    # Carried as its own field rather than expressed as an empty filter, so
    # "remove it" and "I said nothing about it" stay distinguishable — they mean
    # opposite things during a merge.
    remove_filters: tuple[str, ...] = field(default=())

    def filter_for(self, dimension: str) -> SlotFilter | None:
        wanted = (dimension or "").strip().lower()
        for f in self.filters:
            if f.dimension == wanted:
                return f
        return None

    def is_empty(self) -> bool:
        return not (
            self.metric or self.dimensions or self.filters
            or self.time_range or self.relative_period or self.grain or self.compare_to
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "dimensions": list(self.dimensions),
            "filters": [f.to_dict() for f in self.filters],
            "time_range": list(self.time_range) if self.time_range else None,
            "relative_period": self.relative_period,
            "grain": self.grain,
            "compare_to": self.compare_to,
        }

    @staticmethod
    def from_dict(raw: dict[str, Any] | None) -> AnalyticalSlots:
        if not isinstance(raw, dict):
            return AnalyticalSlots()

        time_range = raw.get("time_range")
        if isinstance(time_range, (list, tuple)) and len(time_range) == 2:
            bounds: tuple[str, str] | None = (str(time_range[0]), str(time_range[1]))
        else:
            bounds = None

        filters = []
        for item in raw.get("filters") or []:
            if not isinstance(item, dict):
                continue
            try:
                parsed = SlotFilter.from_dict(item)
            except ValueError:
                continue          # a nameless filter is noise, not an error
            if parsed.dimension:
                filters.append(parsed)

        grain = str(raw.get("grain", "") or "").strip().lower()
        if grain and grain not in {g.value for g in TimeGrain}:
            grain = ""
        relative = str(raw.get("relative_period", "") or "").strip().lower()
        if relative and relative not in RELATIVE_PERIODS:
            relative = ""
        compare_to = str(raw.get("compare_to", "") or "").strip().lower()
        if compare_to and compare_to not in COMPARISONS:
            compare_to = ""

        return AnalyticalSlots(
            metric=str(raw.get("metric", "") or "").strip(),
            dimensions=tuple(
                str(d).strip().lower() for d in (raw.get("dimensions") or []) if str(d).strip()
            ),
            filters=tuple(filters),
            time_range=bounds,
            relative_period=relative,
            grain=grain,
            compare_to=compare_to,
            remove_filters=tuple(
                str(d).strip().lower() for d in (raw.get("remove_filters") or []) if str(d).strip()
            ),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


def merge(previous: AnalyticalSlots | None, incoming: AnalyticalSlots) -> AnalyticalSlots:
    """Apply this turn's slots to the previous turn's, deterministically.

    The rules, and why each one is the way it is:

    * **A named metric that differs starts fresh.** "và số khách hàng thì sao?"
      is a new question, not a refinement of revenue — inheriting revenue's
      filters would silently scope it to whatever the last question happened to
      be looking at.
    * **Filters replace by dimension, they do not accumulate.** "chỉ lấy miền
      Bắc" then "chỉ lấy miền Nam" must mean South. Appending would produce
      `region = 'Bắc' AND region = 'Nam'` — always empty, and empty results are
      the hardest kind of wrong answer to notice.
    * **A field absent from the follow-up is inherited, not cleared.** That is
      the whole point: "chỉ lấy miền Bắc" says nothing about the period, so the
      period must survive. Clearing is available, but only by asking for it
      (`remove_filters`).
    * **Dimensions replace wholesale when given.** "theo tỉnh" replaces "theo
      miền" rather than grouping by both, which is what the words mean.
    """
    if previous is None or previous.is_empty():
        return replace(incoming, remove_filters=())

    if incoming.metric and previous.metric and incoming.metric != previous.metric:
        return replace(incoming, remove_filters=())

    metric = incoming.metric or previous.metric

    # Replace-by-dimension. Order follows the previous turn so a stable request
    # renders stable SQL; genuinely new dimensions append at the end.
    merged: dict[str, SlotFilter] = {f.dimension: f for f in previous.filters}
    for f in incoming.filters:
        merged[f.dimension] = f
    for dimension in incoming.remove_filters:
        merged.pop(dimension, None)

    time_range = incoming.time_range or (None if incoming.relative_period else previous.time_range)
    relative = incoming.relative_period or ("" if incoming.time_range else previous.relative_period)

    return AnalyticalSlots(
        metric=metric,
        dimensions=incoming.dimensions or previous.dimensions,
        filters=tuple(merged.values()),
        time_range=time_range,
        relative_period=relative,
        grain=incoming.grain or previous.grain,
        # NOT inherited. "so với cùng kỳ" applies to the turn that asked for it;
        # carrying it forward would turn every later follow-up into a comparison
        # nobody requested.
        compare_to=incoming.compare_to,
        remove_filters=(),
    )
