"""Analyses over a metric: level of detail and time intelligence (the Holistics review, 19/10/2026).

Each one wraps the compiler's own statement (compile_metric), as compare.py does, so the value
next to the analysed figure is exactly what the same question without the analysis returns:

  * share_of_total: each member's part of the total of the members asked, per time bucket when
    the question also cuts along time ("tỷ trọng dư nợ theo vùng"). Column `share`, in percent:
    the figure the answer shows is the stored cell itself, so a receipt traces it unchanged;
  * running_total: the value accumulated along the time cut ("doanh thu luỹ kế theo tháng"),
    restarted at each year, quarter or month for a to-date question ("luỹ kế từ đầu năm theo
    tháng"). Column `cumulative`;
  * to_date: the period from the start of its year, quarter or month to its end ("doanh thu luỹ kế
    tháng 9" is January to September). A plain period, compiled by the compiler itself.

A year and a quarter to date start where the Data Space's fiscal year does (GEN-21): with a fiscal
year from April, "luỹ kế từ đầu năm" in September is April to September, as its books read it.

Both columns are window functions over the compiled values (SUM() OVER), which every dialect a
source may speak has. A sum of values is only a total when the metric adds up: one SUM or COUNT
measure. A ratio, an average or a distinct count does not (the shares of a ratio do not sum to
the ratio of the total), and a balance does not add up over time, so those are refused with the
reason rather than answered with a figure that looks right.
"""

from __future__ import annotations

import datetime
import hashlib
from dataclasses import replace

from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from libs import clock
from services.worker_agent.semantic.compiler import (
    Compiled,
    CompileError,
    Dimension,
    Layer,
    MetricVersion,
    Period,
    Request,
    compile_metric,
)

SHARE_OF_TOTAL = "share_of_total"
RUNNING_TOTAL = "running_total"
TO_DATE_UNITS = ("year", "quarter", "month", "week")


def additive_measures(layer: Layer) -> dict[str, str]:
    """The expressions of the measures that may add up, by code, for the checks that read a
    measure's SQL (answer/insights.is_additive, skills/decomposition.shape_of).

    A steward's `none` takes a measure out: a SUM over pre-computed rates or over rows already
    aggregated at another grain has the shape of a total and is not one, which only the
    declaration can tell. The declaration only narrows; a distinct count declared `full` still
    fails the shape check."""
    return {code: m.expression for code, m in layer.measures.items() if m.additivity != "none"}


def is_additive(metric: MetricVersion, layer: Layer) -> bool:
    """Whether the metric's values add up across members: one measure that is a SUM or a COUNT
    (not DISTINCT), the measure itself and not an expression over it, and not declared `none`."""
    try:
        tree = parse_one(metric.expression)
    except ParseError:
        return False
    while isinstance(tree, exp.Paren):
        tree = tree.this
    measures = additive_measures(layer)
    if not isinstance(tree, exp.Column) or tree.name not in measures:
        return False
    try:
        measure = parse_one(measures[tree.name])
    except ParseError:
        return False
    if isinstance(measure, exp.Sum):
        return True
    return isinstance(measure, exp.Count) and not isinstance(measure.this, exp.Distinct)


def _refuse_unless_additive(metric: MetricVersion, layer: Layer, what: str) -> None:
    if not is_additive(metric, layer):
        raise CompileError(
            "CONTRACT_MISMATCH",
            f"{metric.display_name} là tỷ lệ, trung bình hoặc đếm không trùng nên không cộng được"
            f" giữa các nhóm; chưa tính {what} cho chỉ số này.",
        )


def to_date(
    period: Period, unit: str, metric: MetricVersion, cal: clock.Calendar = clock.DEFAULT
) -> Period:
    """The period from the start of the (fiscal) year, quarter or month its last day is in, to
    its end."""
    if unit not in TO_DATE_UNITS:
        raise CompileError("CONTRACT_MISMATCH", f"unknown to-date unit {unit}")
    if metric.period_semantics == "end_of_period":
        raise CompileError(
            "CONTRACT_MISMATCH",
            f"{metric.display_name} là số dư tại một ngày, cộng dồn từ đầu kỳ không có nghĩa;"
            " hãy hỏi số dư cuối kỳ.",
        )
    last = period.end - datetime.timedelta(days=1)
    return Period(clock.start_of(last, unit, cal), period.end)


def _inner(request: Request, layer: Layer, dialect: str) -> tuple[Compiled, exp.Subquery]:
    """The compiled statement, unranked and unordered (the analysis orders and ranks after the
    window: a share of the top 5 is of the total of all members, not of the five)."""
    compiled = compile_metric(replace(request, top_n=None), layer, dialect=dialect)
    statement = parse_one(compiled.sql, dialect=dialect)
    assert isinstance(statement, exp.Select)
    # SQL Server refuses ORDER BY in a subquery; the outer statement orders.
    statement.set("order", None)
    return compiled, statement.subquery("s", copy=False)


def _finish(
    request: Request,
    compiled: Compiled,
    select: exp.Select,
    column: str,
    dialect: str,
) -> Compiled:
    dims = [exp.column(code, quoted=True) for code in request.group_by]
    if request.top_n is not None:
        value = exp.column("value", quoted=True)
        select = select.order_by(value.desc() if request.top_desc else value.asc())
        select = select.limit(request.top_n)
    elif dims:
        select = select.order_by(*[d.copy() for d in dims])
    sql = select.sql(dialect=dialect)
    return replace(
        compiled,
        sql=sql,
        columns=(*request.group_by, "value", column),
        sql_sha256=hashlib.sha256(sql.encode()).hexdigest(),
    )


def _time_codes(request: Request, layer: Layer) -> list[str]:
    return [code for code in request.group_by if (d := layer.dimensions.get(code)) and d.is_time]


def compile_share(request: Request, layer: Layer, *, dialect: str) -> Compiled:
    """Each member's share of the total of the members asked. Columns: dims, value, share."""
    metric = request.metric
    times = _time_codes(request, layer)
    if not [code for code in request.group_by if code not in times]:
        raise CompileError(
            "CONTRACT_MISMATCH", "Hãy nêu chiều cần chia, ví dụ: tỷ trọng doanh thu theo khu vực."
        )
    _refuse_unless_additive(metric, layer, "tỷ trọng")
    if metric.period_semantics == "average_over_period":
        # The members' means over the days each has data do not add up to the total's mean.
        raise CompileError(
            "CONTRACT_MISMATCH",
            f"{metric.display_name} là bình quân theo ngày; chưa tính tỷ trọng cho chỉ số này.",
        )
    compiled, inner = _inner(request, layer, dialect)
    value = exp.column("value", quoted=True)
    total = exp.Window(
        this=exp.Sum(this=value.copy()),
        partition_by=[exp.column(code, quoted=True) for code in times],
    )
    share = exp.Div(
        this=exp.Mul(this=value.copy(), expression=exp.Literal.number("100.0")),
        expression=exp.Nullif(this=total, expression=exp.Literal.number(0)),
    )
    select = exp.select(
        *(exp.column(code, quoted=True) for code in request.group_by),
        value,
        exp.alias_(share, "share", quoted=True),
    ).from_(inner)
    return _finish(request, compiled, select, "share", dialect)


def _bucket_label(
    day: datetime.date, grain: str, dim: Dimension, cal: clock.Calendar
) -> exp.Expression:
    """The label the compiler gives the bucket a day is in (compiler._at_grain), as a literal."""
    if grain in ("fiscal_year", "fiscal_quarter") and cal.fiscal:
        year, quarter = clock.fiscal_quarter_of(day, cal)
        return exp.Literal.string(
            f"FY{year}" + (f"-Q{quarter}" if grain == "fiscal_quarter" else "")
        )
    grain = grain.removeprefix("fiscal_")
    if grain == "week":
        return exp.Literal.string(clock.week_of(day, cal)[0].isoformat())
    if grain == "month":
        return exp.Literal.string(f"{day:%Y-%m}")
    if grain == "quarter":
        return exp.Literal.string(f"{day:%Y}-Q{(day.month - 1) // 3 + 1}")
    if grain == "year":
        return exp.Literal.string(f"{day:%Y}")
    if dim.value_type == "string":
        return exp.Literal.string(day.isoformat())
    return exp.cast(exp.Literal.string(day.isoformat()), "DATE")


def _year_of(
    bucket: exp.Expression, grain: str, dim: Dimension, cal: clock.Calendar
) -> exp.Expression:
    """The (fiscal) year a bucket is in: the first four characters of a label ('2026-09',
    '2026-Q3') or of an ISO text day, the year of a date; 'FY2026' of a fiscal label.

    With a fiscal year from month s, a day, week or month label before s is in the year that
    started the calendar year before: CASE on its month, as a number every dialect compares."""

    def part(start: int, length: int) -> exp.Expression:
        return exp.Substring(
            this=bucket.copy(), start=exp.Literal.number(start), length=exp.Literal.number(length)
        )

    if grain in ("fiscal_year", "fiscal_quarter") and cal.fiscal:
        return part(1, 6)
    grain = grain.removeprefix("fiscal_")
    text = grain != "day" or dim.value_type == "string"
    year = part(1, 4) if text else exp.Year(this=bucket.copy())
    if not cal.fiscal:
        return year
    if grain in ("quarter", "year"):
        raise CompileError(
            "CONTRACT_MISMATCH",
            "Năm tài chính không trùng năm dương lịch: hãy luỹ kế theo quý tài chính hoặc theo"
            " tháng.",
        )
    month = exp.cast(part(6, 2), "INT") if text else exp.Month(this=bucket.copy())
    number = exp.cast(year, "INT")
    return exp.Case(
        ifs=[
            exp.If(
                this=exp.GTE(
                    this=month, expression=exp.Literal.number(cal.fiscal_year_start_month)
                ),
                true=number.copy(),
            )
        ],
        default=exp.Sub(this=number, expression=exp.Literal.number(1)),
    )


def compile_running(
    request: Request, layer: Layer, *, dialect: str, restart: str | None = None
) -> Compiled:
    """The value accumulated along the question's time cut, from the first bucket of the period,
    or from the start of each year (`restart="year"`) for a to-date question; the buckets of that
    year before the period are read and accumulated, then left out. A restart at each quarter or
    month along a cut by month or day is not offered yet.
    Columns: dims, value, cumulative."""
    metric = request.metric
    times = _time_codes(request, layer)
    if len(times) != 1:
        raise CompileError(
            "CONTRACT_MISMATCH",
            "Hãy nêu cách chia theo thời gian, ví dụ: doanh thu luỹ kế theo tháng.",
        )
    _refuse_unless_additive(metric, layer, "luỹ kế")
    if metric.period_semantics != "sum_over_period":
        raise CompileError(
            "CONTRACT_MISMATCH",
            f"{metric.display_name} là số dư hoặc bình quân, cộng dồn theo thời gian không có"
            " nghĩa.",
        )
    if restart not in (None, "year"):
        raise CompileError(
            "CONTRACT_MISMATCH",
            "Chưa luỹ kế từ đầu quý hoặc đầu tháng khi đã chia theo thời gian; hãy hỏi luỹ kế từ"
            " đầu năm, hoặc bỏ chiều thời gian.",
        )
    (time_code,) = times
    dim = layer.dimensions[time_code]
    cal = layer.calendar
    grain = request.grain or metric.time_grain
    asked: Period | None = request.period
    widened = request
    if restart is not None and asked is not None:
        widened = replace(
            request, period=Period(clock.start_of(asked.start, restart, cal), asked.end)
        )
    compiled, inner = _inner(widened, layer, dialect)
    bucket = exp.column(time_code, quoted=True)
    partition: list[exp.Expression] = [
        exp.column(code, quoted=True) for code in request.group_by if code != time_code
    ]
    if restart is not None:
        partition.append(_year_of(bucket.copy(), grain, dim, cal))
    cumulative = exp.Window(
        this=exp.Sum(this=exp.column("value", quoted=True)),
        partition_by=partition,
        order=exp.Order(expressions=[exp.Ordered(this=bucket.copy())]),
        spec=exp.WindowSpec(
            kind="ROWS", start="UNBOUNDED", start_side="PRECEDING", end="CURRENT ROW"
        ),
    )
    select = exp.select(
        *(exp.column(code, quoted=True) for code in request.group_by),
        exp.column("value", quoted=True),
        exp.alias_(cumulative, "cumulative", quoted=True),
    ).from_(inner)
    if widened is not request and asked is not None:
        # The buckets before the period were read to accumulate from the start; they are not asked.
        select = (
            exp.select(
                *(exp.column(c, quoted=True) for c in (*request.group_by, "value", "cumulative"))
            )
            .from_(select.subquery("r"))
            .where(
                exp.GTE(this=bucket.copy(), expression=_bucket_label(asked.start, grain, dim, cal))
            )
        )
    return _finish(request, compiled, select, "cumulative", dialect)
