"""Where a metric has data: the periods that hold a figure, the first and the last, and whether
the last one looks cut off (AGC-10).

An analyst asked about a quarter the data never reaches looks where the data is before writing
anything, and does not compare a month the books stopped half way through with a whole one. The
probe is the metric itself cut along its time dimension with no period, through the compiler and
the executor like any governed read, so policies apply and the run is recorded. It is a plain
function over the semantic layer: the agent's data_coverage tool and its planning context use it,
and so may any other reader that needs the last period with data (forecasting).
"""

from __future__ import annotations

import datetime
import statistics
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from libs.sql_guard import SqlRejected, dialect_of
from services.worker_agent.explore import _add_months, _as_date, bucket, grain_of
from services.worker_agent.query.executor import CallerKind, run_query
from services.worker_agent.semantic import compiler as c
from services.worker_agent.semantic.loader import SpaceLayer

D = datetime.date

# A last period whose figure is under this share of the median of the ones before it is taken as
# cut off (the books of December 2013 in AdventureWorks hold 27 adjusting entries, against some
# 1,150 rows a month): its figure is shown, never compared as if it were a whole period.
PARTIAL_SHARE = 0.1
# How many periods before the last the median is taken over, and the fewest that make it a median.
PARTIAL_WINDOW = 6
PARTIAL_MIN_HISTORY = 3


@dataclass(frozen=True)
class Coverage:
    grain: str
    # The periods that hold a figure, oldest first: the start of each and its value.
    periods: tuple[tuple[D, float], ...] = ()
    partial_last: bool = False
    query_run_id: uuid.UUID | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def first(self) -> D | None:
        return self.periods[0][0] if self.periods else None

    @property
    def last(self) -> D | None:
        return self.periods[-1][0] if self.periods else None

    @property
    def last_complete(self) -> D | None:
        """The last period that is whole: the one before a cut-off tail, else the last."""
        if self.partial_last:
            return self.periods[-2][0] if len(self.periods) >= 2 else None
        return self.last

    def covers(self, start: D, end: D) -> bool:
        """Whether any period with data begins inside [start, end)."""
        return any(start <= at < end for at, _ in self.periods)


def partial_tail(values: Sequence[float]) -> bool:
    """Whether the last value is a sliver of the ones before it (a period the data stops in)."""
    if len(values) < PARTIAL_MIN_HISTORY + 1:
        return False
    before = [abs(v) for v in values[-1 - PARTIAL_WINDOW : -1]]
    typical = statistics.median(before)
    return typical > 0 and abs(values[-1]) < PARTIAL_SHARE * typical


def from_rows(rows: Sequence[Sequence[Any]], grain: str) -> Coverage:
    """The coverage of a probe's rows (time label, value): buckets with a non-null figure."""
    found: dict[D, float] = {}
    for row in rows:
        if len(row) < 2 or row[-1] is None:
            continue
        start = _as_date(row[0])
        if start is None:
            continue
        try:
            found[start] = found.get(start, 0.0) + float(row[-1])
        except (TypeError, ValueError):
            continue
    periods = tuple(sorted(found.items()))
    return Coverage(grain, periods, partial_tail([v for _, v in periods]))


def time_code(layer: SpaceLayer, version: c.MetricVersion) -> str | None:
    """The time dimension a probe cuts along: the metric's own, else one it allows, else any."""
    times = [d for d in layer.vocabulary.dimensions if d.is_time]
    for d in times:
        if version.time_dimension_id is not None and d.dimension_id == version.time_dimension_id:
            return d.code
    for d in times:
        if d.dimension_id in version.allowed_dimension_ids:
            return d.code
    return times[0].code if times else None


async def probe(
    layer: SpaceLayer,
    version: c.MetricVersion,
    source: Any,
    *,
    org_id: uuid.UUID,
    space_id: uuid.UUID,
    user_id: uuid.UUID,
    caller_id: uuid.UUID,
    secrets: Any,
    as_of: D | None = None,
    grain: str = "month",
    filters: Mapping[str, Sequence[str]] | None = None,
    caller_kind: CallerKind = "question",
) -> Coverage | None:
    """The metric cut along time at `grain` over all its data; None when it cannot be probed
    (no time dimension, a compile refused) or the source did not answer."""
    code = time_code(layer, version)
    if code is None:
        return None
    request = c.Request(
        version,
        group_by=[code],
        filters={k: list(v) for k, v in (filters or {}).items()},
        period=None,
        grain=grain,
        top_n=None,
    )
    try:
        compiled = c.compile_metric(request, layer.layer, dialect=dialect_of(source.kind))
    except (c.CompileError, SqlRejected):
        return None
    outcome = await run_query(
        org_id=org_id,
        data_space_id=space_id,
        principal_id=user_id,
        caller_kind=caller_kind,
        caller_id=caller_id,
        source=source,
        sql=compiled.sql,
        secrets=secrets,
        metric_version_ids=[compiled.metric_version_id],
        as_of=as_of,
    )
    if not outcome.ok:
        return None
    found = from_rows(outcome.rows or [], grain)
    return Coverage(found.grain, found.periods, found.partial_last, outcome.query_run_id)


def label(start: D, grain: str) -> str:
    """'2013-11', '2013-Q4', '2013': how the compiler labels a period, and query_metric takes it."""
    if grain == "year":
        return f"{start.year}"
    if grain == "quarter":
        return f"{start.year}-Q{(start.month - 1) // 3 + 1}"
    if grain == "month":
        return f"{start.year}-{start.month:02d}"
    return start.isoformat()


_MONTHS = {"year": 12, "quarter": 3, "month": 1}


def _whole(cov: Coverage, start: D, months: int) -> bool:
    """Whether every period of the coverage inside [start, start + months) has data and none is
    the cut-off tail: a period of that length the data fully covers."""
    end = _add_months(start, months)
    held = {at for at, _ in cov.periods if start <= at < end}
    tail = cov.periods[-1][0] if cov.partial_last else None
    if tail is not None and start <= tail < end:
        return False
    step = _MONTHS.get(cov.grain, 0)
    if not step:
        return bool(held)
    expected = {_add_months(start, k) for k in range(0, months, step)}
    return expected <= held


def suggest(cov: Coverage, asked: c.Period) -> tuple[c.Period, c.Period | None] | None:
    """For an asked period the data does not cover: the latest whole period of the same length
    before the data ends, and the same period a year earlier to compare with, when it is whole."""
    months = _MONTHS.get(grain_of(asked))
    if months is None or not cov.periods:
        return None
    last = cov.periods[-1][0]
    # Align on the asked period's own phase: quarters on quarters, years on its first month.
    phase = (asked.start.year * 12 + asked.start.month - 1) % months
    total = last.year * 12 + last.month - 1
    total -= (total - phase) % months
    start = D(total // 12, total % 12 + 1, 1)
    for _ in range(8):
        if _whole(cov, start, months):
            chosen = c.Period(start, _add_months(start, months))
            earlier = _add_months(start, -12)
            compare = (
                c.Period(earlier, _add_months(earlier, months))
                if _whole(cov, earlier, months)
                else None
            )
            return chosen, compare
        start = _add_months(start, -months)
    return None


def iso(period: c.Period) -> str:
    """The period as query_metric takes it: '2013', '2013-Q3', '2013-09', or 'start/end'."""
    months = (period.end.year - period.start.year) * 12 + period.end.month - period.start.month
    if period.start.day == 1 and period.end.day == 1:
        if months == 12 and period.start.month == 1:
            return f"{period.start.year}"
        if months == 3 and period.start.month % 3 == 1:
            return label(period.start, "quarter")
        if months == 1:
            return label(period.start, "month")
    return f"{period.start.isoformat()}/{period.end.isoformat()}"


def describe(cov: Coverage, name: str, asked: c.Period | None = None) -> str:
    """The coverage in a few lines a planner or a reader acts on."""
    if not cov.periods:
        return f"{name}: không có dữ liệu ở kỳ nào."
    first, last = cov.periods[0][0], cov.periods[-1][0]
    lines = [
        f"{name}: có dữ liệu từ {label(first, cov.grain)} đến {label(last, cov.grain)}"
        f" ({len(cov.periods)} kỳ {cov.grain})."
    ]
    if cov.partial_last:
        complete = cov.last_complete
        lines.append(
            f"Kỳ cuối {label(last, cov.grain)} chỉ có {cov.periods[-1][1]:,.0f}, quá nhỏ so với các"
            " kỳ trước: dữ liệu dừng giữa kỳ, không so sánh với nó như một kỳ đầy đủ"
            + (f"; kỳ đầy đủ cuối là {label(complete, cov.grain)}." if complete else ".")
        )
    if asked is not None and not cov.covers(asked.start, asked.end):
        complete = cov.last_complete or last
        where = (
            "nằm sau dữ liệu"
            if asked.start >= bucket(complete, cov.grain).end
            else "không có dữ liệu"
        )
        lines.append(f"Kỳ được hỏi ({iso(asked)}) {where}: không truy vấn kỳ đó.")
        found = suggest(cov, asked)
        if found is not None:
            chosen, compare = found
            lines.append(
                f'Kỳ đầy đủ gần nhất cùng độ dài: period="{iso(chosen)}"'
                + (f'; kỳ so sánh cùng kỳ năm trước: period="{iso(compare)}"' if compare else "")
                + ". Câu trả lời phải nói rõ là số của kỳ này, không phải kỳ được hỏi."
            )
    return " ".join(lines)
