"""compare_to (R1-06): a metric over a period next to the same metric over a base period, in one
SQL statement.

The two sides are the compiler's own statements (compile_metric), so a compared value is exactly
the value the same question without a comparison returns. They are combined as

    SELECT <dims>, MAX(CASE WHEN side = 0 THEN value END) AS value,
                   MAX(CASE WHEN side = 1 THEN value END) AS base_value
    FROM (<current> UNION ALL <base>) GROUP BY <dims>

rather than a FULL JOIN: every dialect a source may speak has UNION ALL and GROUP BY (MySQL has
no FULL JOIN), and GROUP BY puts a NULL member of both sides in one row where `=` would not. A
member present on one side only keeps NULL on the other; the change is not invented.

The change itself is not computed here: the answer traces it as a derived figure over the two
values (G5.15), recomputed on replay.
"""

from __future__ import annotations

import calendar
import datetime
import hashlib
from dataclasses import replace

from sqlglot import exp, parse_one

from services.worker_agent.semantic.compiler import (
    Compiled,
    CompileError,
    Layer,
    Period,
    Request,
    compile_metric,
)

PREVIOUS_PERIOD = "previous_period"
SAME_PERIOD_LAST_YEAR = "same_period_last_year"
COMPARE_TO = (PREVIOUS_PERIOD, SAME_PERIOD_LAST_YEAR)


def _add_months(day: datetime.date, months: int) -> datetime.date:
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    # 29/02 a year back is 28/02: the last day of the month, not an error.
    return datetime.date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def _months_between(period: Period) -> int | None:
    """The period's length in whole months when it runs from a 1st to a 1st, else None."""
    if period.start.day != 1 or period.end.day != 1:
        return None
    return (period.end.year - period.start.year) * 12 + period.end.month - period.start.month


def base_period(period: Period, compare_to: str) -> Period:
    """The period a comparison reads against.

    previous_period: the same length right before, in months when the period is whole months (a
    quarter's previous is the quarter before, February's is January), in days otherwise.
    same_period_last_year: the same dates a year back.
    """
    if compare_to == SAME_PERIOD_LAST_YEAR:
        return Period(_add_months(period.start, -12), _add_months(period.end, -12))
    if compare_to == PREVIOUS_PERIOD:
        months = _months_between(period)
        if months:
            return Period(_add_months(period.start, -months), period.start)
        return Period(period.start - (period.end - period.start), period.start)
    raise CompileError("CONTRACT_MISMATCH", f"unknown comparison {compare_to}")


def compile_comparison(
    request: Request, layer: Layer, compare_to: str, *, dialect: str
) -> tuple[Compiled, Period]:
    """The compared statement and the base period it read. Columns: dims, value, base_value."""
    if request.period is None:
        raise CompileError(
            "NO_DATA_FOR_PERIOD",
            "Hãy nêu kỳ cần so sánh, ví dụ: tháng 9/2026 so với tháng trước.",
        )
    time_codes = {code for code, d in layer.dimensions.items() if d.is_time}
    if time_codes & set(request.group_by):
        # Rows of 2026-10 and 2025-10 do not share a key; matching them by position is a guess.
        raise CompileError(
            "UNSUPPORTED",
            "Chưa so sánh hai kỳ khi đã chia theo thời gian; hãy bỏ chiều thời gian, hoặc hỏi"
            " diễn biến theo tháng.",
        )
    base = base_period(request.period, compare_to)
    current = compile_metric(request, layer, dialect=dialect)
    previous = compile_metric(replace(request, period=base), layer, dialect=dialect)

    def side(compiled: Compiled, tag: int) -> exp.Select:
        statement = parse_one(compiled.sql, dialect=dialect)
        assert isinstance(statement, exp.Select)
        if statement.args.get("limit") is None:
            # SQL Server refuses ORDER BY in a subquery; the outer statement orders.
            statement.set("order", None)
        inner = statement.subquery("s", copy=False)
        return exp.select(
            *(exp.column(c, quoted=True) for c in compiled.columns),
            exp.alias_(exp.Literal.number(tag), "side", quoted=True),
        ).from_(inner)

    def pick(tag: int, name: str) -> exp.Alias:
        when = exp.If(
            this=exp.EQ(this=exp.column("side", quoted=True), expression=exp.Literal.number(tag)),
            true=exp.column("value", quoted=True),
        )
        return exp.Alias(
            this=exp.Max(this=exp.Case(ifs=[when])), alias=exp.to_identifier(name, quoted=True)
        )

    both = exp.union(side(current, 0), side(previous, 1), distinct=False).subquery("u")
    dims = [exp.column(code, quoted=True) for code in request.group_by]
    select = exp.select(*dims, pick(0, "value"), pick(1, "base_value")).from_(both)
    if dims:
        select = select.group_by(*[d.copy() for d in dims]).order_by(*[d.copy() for d in dims])
    sql = select.sql(dialect=dialect)
    return (
        replace(
            current,
            sql=sql,
            columns=(*request.group_by, "value", "base_value"),
            sql_sha256=hashlib.sha256(sql.encode()).hexdigest(),
        ),
        base,
    )
