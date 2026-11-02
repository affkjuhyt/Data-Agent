"""The semantic compiler (tab 5 G5.2): a metric version, the dimensions and filters the question
asked for, and a period → one SQL statement in the source's dialect.

Deterministic and pure: it reads the semantic layer as loaded (loader.py), never the database, and
never an LLM. What it does:

  * the metric's expression is written over measure codes (e.g. `no_xau / du_no * 100`); each code
    is replaced by its measure's own aggregate expression (`SUM(npl_amt)`);
  * the measures' entity gives the base table (catalog.dataset schema.table); a requested
    dimension on another entity is joined through a chain of active many-to-one relationships
    from the base (join_path: at most MAX_HOPS, the shortest one). Every hop goes to the "one"
    side, so the base's rows are never repeated. Two shortest chains to the same table are
    NO_JOIN_PATH naming both, never a guessed join: which way a loan reaches a region (its
    branch's, or its customer's) changes the figure, and only the semantic model's owner knows;
  * measures of several entities (R1-06, `doanh_thu / so_khach_hang`) are aggregated per entity
    first, each on its own table with its own joins, then combined by the requested dimensions:
    joining the tables before aggregating would repeat one side's rows for each of the other's
    (the fan and chasm traps). The sides are combined with UNION ALL + GROUP BY, which every
    dialect has (MySQL has no FULL JOIN), so a member present on one side only keeps NULL on the
    other; a sum or count read there as that side's zero (compiler 5), the value NULL only when no
    side has a figure;
  * the metric's default filters and the question's dimension filters become WHERE clauses with
    values as literals sqlglot quotes (no text is spliced into SQL). A filter is typed (GEN-22):
    membership (in, not_in), a comparison (=, !=, <, >, <=, >=), a range (between, both ends
    included) or null (is_null, is_not_null);
  * a division in the metric's expression divides by NULLIF(…, 0): a member with no
    denominator has no ratio, not a division error that fails the whole answer;
  * the period follows period_semantics: `sum_over_period` aggregates the whole range,
    `end_of_period` the last day of the range that has data (a balance is not summed over days),
    `average_over_period` the mean of the metric's daily values over the range (R1-06). In a
    `sum_over_period` metric, a measure the steward declared `semi` (a balance, not additive over
    time) is still read on the last day (GEN-22): stock and flow aggregated apart, then combined,
    so "doanh thu / tồn kho" divides the period's revenue by its closing stock;
  * the time a metric is read along is its version's time_dimension_id, or the time dimension of
    its table, or the one time dimension its table reaches through approved joins (a star
    schema's date table, GEN-22);
  * a cut along time is at a grain (R1-06): the question's ("theo tháng") or the metric version's
    time_grain. Month, quarter and year are labelled '2026-09', '2026-Q3', '2026'; a week by its
    first day '2026-10-26', on the Data Space's week_start (GEN-21); a fiscal year and quarter
    'FY2026', 'FY2026-Q1' by the calendar's fiscal_year_start_month and naming (the calendar
    year's labels when it starts in January). A balance cut by month is read on the last day with
    data of each month, not summed over its days;
  * output: the requested dimensions, then `value`, grouped and ordered by the dimensions.

The SQL hash and COMPILER_VERSION go on the receipt (qa.answer_receipt, ADR-43), so a figure can be
traced to exactly what produced it.
"""

from __future__ import annotations

import datetime
import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from libs import clock

COMPILER_VERSION = "v3-compiler-5"
MAX_HOPS = 3
GRAINS = ("day", "week", "month", "quarter", "year", "fiscal_quarter", "fiscal_year")
# The day a value was read on, carried from a side to the average over the period.
_DAY = "_day"


class CompileError(ValueError):
    """The question cannot be compiled; `code` is the refusal code it maps to."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Table:
    dataset_id: uuid.UUID
    schema: str | None
    name: str
    history_kind: str = "none"
    knowledge_time_col: str | None = None


@dataclass(frozen=True)
class Measure:
    code: str
    expression: str
    entity_id: uuid.UUID
    # semantic_measure.additivity as the steward declared it: full, semi (not over time) or none.
    additivity: str = "full"


@dataclass(frozen=True)
class Dimension:
    id: uuid.UUID
    code: str
    name: str
    expression: str
    entity_id: uuid.UUID
    is_time: bool = False
    # The catalog's normalized type of the column behind it, when the catalog knows it.
    value_type: str | None = None


@dataclass(frozen=True)
class Join:
    """An active many-to-one relationship, from the base table to another one."""

    from_dataset_id: uuid.UUID
    from_columns: tuple[str, ...]
    to_dataset_id: uuid.UUID
    to_columns: tuple[str, ...]


@dataclass(frozen=True)
class MetricVersion:
    id: uuid.UUID
    metric_code: str
    display_name: str
    expression: str
    filters: tuple[dict[str, object], ...]
    unit: str | None
    decimals: int
    period_semantics: str
    allowed_dimension_ids: frozenset[uuid.UUID]
    certified: bool
    # The grain a cut along time is at when the question names none (metric_version.time_grain).
    time_grain: str = "day"
    # The time it is read along (metric_version.time_dimension_id); None: its table's own.
    time_dimension_id: uuid.UUID | None = None


@dataclass(frozen=True)
class Layer:
    """The part of a Data Space's semantic layer one compile needs."""

    measures: dict[str, Measure]
    dimensions: dict[str, Dimension]  # by code
    entity_tables: dict[uuid.UUID, Table]  # entity id → its primary dataset
    joins: tuple[Join, ...] = ()
    # Where the Data Space's weeks and fiscal years start (its work calendar).
    calendar: clock.Calendar = clock.DEFAULT


FILTER_OPS = (
    "in",
    "not_in",
    "=",
    "!=",
    "<",
    ">",
    "<=",
    ">=",
    "between",
    "is_null",
    "is_not_null",
)
_COMPARISONS: dict[str, type[exp.Expression]] = {
    "=": exp.EQ,
    "!=": exp.NEQ,
    "<": exp.LT,
    ">": exp.GT,
    "<=": exp.LTE,
    ">=": exp.GTE,
}


@dataclass(frozen=True)
class Filter:
    """One condition on a dimension (GEN-22)."""

    dimension: str  # dimension code
    op: str = "in"
    values: tuple[object, ...] = ()


def filter_of(raw: Mapping[str, object]) -> Filter:
    """A metric version's stored filter ({"dimension", "op", "values" | "value"}) as a Filter.

    The earlier form `{"column": …}` and `op` "<>" or "not in" read as before. A filter with an
    unknown op, or the wrong number of values for it, is refused rather than ignored: a dropped
    filter changes the figure without saying so."""
    code = str(raw.get("dimension") or raw.get("column") or "")
    op = str(raw.get("op") or "in").lower()
    found = raw.get("values", raw.get("value"))
    # A list is membership: {"op": "!=", "values": [...]} has always meant NOT IN.
    listed = isinstance(found, list | tuple)
    if op in ("!=", "<>", "not in", "not_in"):
        op = "not_in" if listed or op in ("not in", "not_in") else "!="
    elif op in ("=", "=="):
        op = "in" if listed else "="
    values: tuple[object, ...] = (
        tuple(found) if isinstance(found, list | tuple) else () if found is None else (found,)
    )
    f = Filter(code, op, values)
    check_filter(f)
    return f


def check_filter(f: Filter) -> None:
    if not f.dimension:
        raise CompileError("CONTRACT_MISMATCH", "a filter names no dimension")
    if f.op not in FILTER_OPS:
        raise CompileError("CONTRACT_MISMATCH", f"unknown filter op {f.op}")
    wanted = {"between": 2, "is_null": 0, "is_not_null": 0}.get(f.op)
    if wanted is None:
        wanted = 1 if f.op in _COMPARISONS else -1
    if (wanted == -1 and not f.values) or (wanted >= 0 and len(f.values) != wanted):
        raise CompileError(
            "CONTRACT_MISMATCH", f"filter {f.dimension} {f.op} has {len(f.values)} values"
        )


def _condition(expr: exp.Expression, f: Filter) -> exp.Expression:
    if f.op in ("in", "not_in"):
        cond: exp.Expression = exp.In(this=expr, expressions=[_literal(v) for v in f.values])
        return exp.not_(cond) if f.op == "not_in" else cond
    if f.op == "between":
        low, high = f.values
        return exp.Between(this=expr, low=_literal(low), high=_literal(high))
    if f.op in ("is_null", "is_not_null"):
        null = exp.Is(this=expr, expression=exp.Null())
        return exp.not_(null) if f.op == "is_not_null" else null
    return _COMPARISONS[f.op](this=expr, expression=_literal(f.values[0]))


@dataclass(frozen=True)
class Period:
    start: datetime.date  # inclusive
    end: datetime.date  # exclusive


@dataclass
class Request:
    metric: MetricVersion
    group_by: Sequence[str] = ()  # dimension codes
    filters: Mapping[str, Sequence[str]] = field(default_factory=dict)  # dimension code → values
    # Typed conditions of the question ("dư nợ trên 1 tỷ"), held on every side like `filters`.
    conditions: Sequence[Filter] = ()
    period: Period | None = None
    # The grain of a cut along time ("theo tháng"); None: the metric version's time_grain.
    grain: str | None = None
    top_n: int | None = None
    top_desc: bool = True
    knowledge_at: datetime.datetime | None = None


@dataclass(frozen=True)
class Compiled:
    sql: str
    dialect: str
    metric_version_id: uuid.UUID
    dataset_ids: tuple[uuid.UUID, ...]
    columns: tuple[str, ...]  # the group-by dimension codes, then "value"
    unit: str | None
    decimals: int
    certified: bool
    sql_sha256: str
    compiler_version: str = COMPILER_VERSION


def _table(t: Table, alias: str) -> exp.Table:
    return exp.table_(t.name, db=t.schema, alias=alias, quoted=True)


def _measures(
    metric: MetricVersion, layer: Layer
) -> tuple[exp.Expression, dict[str, exp.Expression], dict[uuid.UUID, list[str]]]:
    """The metric's expression, each measure's aggregate, and the measure codes of each entity."""
    try:
        tree = parse_one(metric.expression)
    except ParseError as exc:
        raise CompileError("CONTRACT_MISMATCH", "the metric's expression does not parse") from exc
    measures: dict[str, exp.Expression] = {}
    by_entity: dict[uuid.UUID, list[str]] = {}
    for column in tree.find_all(exp.Column):
        measure = layer.measures.get(column.name)
        if measure is None:
            raise CompileError(
                "CONTRACT_MISMATCH",
                f"the metric names a measure that is not defined: {column.name}",
            )
        if column.name in measures:
            continue
        try:
            measures[column.name] = parse_one(measure.expression)
        except ParseError as exc:
            raise CompileError(
                "CONTRACT_MISMATCH", f"measure {measure.code} does not parse"
            ) from exc
        by_entity.setdefault(measure.entity_id, []).append(column.name)
    if not measures:
        raise CompileError("CONTRACT_MISMATCH", "the metric's expression names no measure")
    return tree, measures, by_entity


def _swap(tree: exp.Expression, by_code: Mapping[str, exp.Expression]) -> exp.Expression:
    """The expression with each measure code replaced. Matched by name: transform() works on a
    copy, so nodes found earlier are not the ones it visits; the expression names only measures."""

    def swap(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Column) and node.name in by_code:
            return exp.paren(by_code[node.name].copy(), copy=False)
        return node

    return tree.transform(swap)


def _safe_division(tree: exp.Expression) -> exp.Expression:
    """Every division of the metric's expression by NULLIF(denominator, 0)."""

    def guard(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Div) and not isinstance(node.expression, exp.Nullif):
            node.set(
                "expression",
                exp.Nullif(this=node.expression, expression=exp.Literal.number(0)),
            )
        return node

    return tree.transform(guard)


def _qualify(expression: str, alias: str) -> exp.Expression:
    """A dimension's column expression, with its bare columns bound to the table alias."""
    tree = parse_one(expression)
    for column in tree.find_all(exp.Column):
        if not column.table:
            column.set("table", exp.to_identifier(alias))
    return tree


def _literal(value: object) -> exp.Expression:
    if isinstance(value, bool):
        return exp.Boolean(this=value)
    if isinstance(value, int | float):
        return exp.Literal.number(value)
    return exp.Literal.string(str(value))


def _text_bounds(period: Period) -> tuple[str, str]:
    """A period's bounds as ISO text, at the coarsest grain both bounds sit on.

    A time column stored as text ('2026-10', '2026-10-06') compares as text; ISO strings order
    like the dates they name. October as '2026-10'..'2026-11' matches a monthly '2026-10' and a
    daily '2026-10-06' alike, while six days of October stay '2026-10-01'..'2026-10-07' so a
    monthly row is never counted for part of its month.
    """
    start, end = period.start, period.end
    if start.month == end.month == 1 and start.day == end.day == 1:
        return f"{start:%Y}", f"{end:%Y}"
    if start.day == end.day == 1:
        return f"{start:%Y-%m}", f"{end:%Y-%m}"
    return start.isoformat(), end.isoformat()


# The first day of the week a date is in, per dialect: sqlglot 25 does not translate a
# day-of-week function between dialects, and each one counts weekdays its own way. {d} is the
# date as the dialect writes it, {s} the ISO weekday weeks start on. SQL Server counts from
# 1900-01-01, a Monday, so its DATEFIRST setting plays no part.
_WEEK_START = {
    "postgres": "CAST({d} AS DATE) - CAST((EXTRACT(ISODOW FROM {d}) + 7 - {s}) AS INT) % 7",
    "mysql": "DATE_SUB({d}, INTERVAL ((WEEKDAY({d}) + 8 - {s}) % 7) DAY)",
    "tsql": "DATEADD(DAY, -((DATEDIFF(DAY, CAST('19000101' AS DATE), {d}) + 8 - {s}) % 7), {d})",
    "clickhouse": "addDays(toDate({d}), -((toDayOfWeek({d}) + 7 - {s}) % 7))",
    "snowflake": "DATEADD(DAY, -((DAYOFWEEKISO({d}) + 7 - {s}) % 7), {d})",
    "bigquery": "DATE_SUB(DATE({d}), INTERVAL MOD(MOD(EXTRACT(DAYOFWEEK FROM {d}) + 5, 7) + 8 - {s}, 7) DAY)",
}


def fiscal_shift(cal: clock.Calendar) -> int:
    """The months a day moves by so that its calendar year and quarter are its fiscal ones."""
    if not cal.fiscal:
        return 0
    start = cal.fiscal_year_start_month
    return 13 - start if cal.fiscal_year_named_by == "end" else 1 - start


def _at_grain(
    when: exp.Expression,
    dim: Dimension,
    grain: str,
    cal: clock.Calendar = clock.DEFAULT,
    dialect: str = "postgres",
) -> exp.Expression:
    """A time value at a grain, as text that orders like the periods: '2026-09', '2026-Q3'.

    A date is formatted (TO_CHAR, DATE_FORMAT, FORMAT… per dialect); ISO text is cut. A quarter is
    worked out from the month with CASE: Postgres has no QUARTER(), and Oracle's CONCAT takes two
    arguments, so the year and '-Qn' are joined by one CONCAT. A fiscal year or quarter is the
    calendar one of the day moved by fiscal_shift months, labelled 'FY…'.
    """
    if grain == "day":
        return when
    if grain in ("fiscal_year", "fiscal_quarter"):
        shift = fiscal_shift(cal)
        calendar_grain = grain.removeprefix("fiscal_")
        if not shift:
            return _at_grain(when, dim, calendar_grain)
        if dim.value_type == "string":
            raise CompileError(
                "CONTRACT_MISMATCH",
                f"{dim.name} là văn bản, chưa chia theo năm tài chính được; hãy khai báo cột ngày.",
            )
        moved = exp.DateAdd(
            this=when.copy(), expression=exp.Literal.number(shift), unit=exp.var("MONTH")
        )
        return exp.Concat(
            expressions=[exp.Literal.string("FY"), _at_grain(moved, dim, calendar_grain)],
            safe=True,
        )
    if grain == "week":
        template = _WEEK_START.get(dialect)
        if template is None or dim.value_type == "string":
            raise CompileError(
                "CONTRACT_MISMATCH",
                f"chưa chia {dim.name} theo tuần trên nguồn này; hãy chia theo ngày hoặc tháng.",
            )
        first = parse_one(
            template.format(d=when.sql(dialect=dialect), s=cal.week_start), read=dialect
        )
        return exp.TimeToStr(this=first, format=exp.Literal.string("%Y-%m-%d"))

    def part(start: int, length: int, fmt: str) -> exp.Expression:
        if dim.value_type == "string":
            return exp.Substring(
                this=when.copy(),
                start=exp.Literal.number(start),
                length=exp.Literal.number(length),
            )
        return exp.TimeToStr(this=when.copy(), format=exp.Literal.string(fmt))

    if grain == "month":
        return part(1, 7, "%Y-%m")
    if grain == "year":
        return part(1, 4, "%Y")
    month = part(6, 2, "%m")
    quarter = exp.Case(
        ifs=[
            exp.If(
                this=exp.In(
                    this=month.copy(),
                    expressions=[
                        exp.Literal.string(f"{m:02d}") for m in range(3 * q - 2, 3 * q + 1)
                    ],
                ),
                true=exp.Literal.string(f"-Q{q}"),
            )
            for q in range(1, 5)
        ]
    )
    return exp.Concat(expressions=[part(1, 4, "%Y"), quarter], safe=True)


def _table_names(layer: Layer) -> dict[uuid.UUID, str]:
    return {t.dataset_id: t.name for t in layer.entity_tables.values()}


def join_path(layer: Layer, start: uuid.UUID, target: uuid.UUID) -> tuple[Join, ...]:
    """The chain of active many-to-one joins from the `start` table to the `target` one: the
    shortest, at most MAX_HOPS. None is NO_JOIN_PATH; two of the shortest length are too."""
    if start == target:
        return ()
    paths: list[tuple[Join, ...]] = [()]
    for _ in range(MAX_HOPS):
        longer: list[tuple[Join, ...]] = []
        for path in paths:
            here = path[-1].to_dataset_id if path else start
            seen = {start, *(j.to_dataset_id for j in path)}
            longer += [
                (*path, j)
                for j in layer.joins
                if j.from_dataset_id == here and j.to_dataset_id not in seen
            ]
        arrived = [path for path in longer if path[-1].to_dataset_id == target]
        if len(arrived) == 1:
            return arrived[0]
        if arrived:
            names = _table_names(layer)
            ways = " / ".join(
                " → ".join([names.get(start, "?"), *(names.get(j.to_dataset_id, "?") for j in p)])
                for p in arrived
            )
            raise CompileError(
                "NO_JOIN_PATH",
                f"Có nhiều cách nối tới bảng {names.get(target, '?')} ({ways}); người phụ trách"
                " mô hình cần tắt một quan hệ để chọn cách nối.",
            )
        paths = longer
    raise CompileError(
        "NO_JOIN_PATH", f"no approved join path to {_table_names(layer).get(target, target)}"
    )


def _reaches(layer: Layer, entity: uuid.UUID, code: str) -> bool:
    """Whether a table reaches a dimension: its own, or through an approved join path."""
    dim = layer.dimensions.get(code)
    base, table = (
        layer.entity_tables.get(entity),
        layer.entity_tables.get(dim.entity_id if dim else entity),
    )
    if dim is None or base is None or table is None:
        return False
    if dim.entity_id == entity:
        return True
    try:
        join_path(layer, base.dataset_id, table.dataset_id)
    except CompileError:
        return False
    return True


def _on(join: Join, from_alias: str, alias: str) -> exp.Expression:
    return exp.and_(
        *(
            exp.EQ(
                this=exp.column(fc, table=from_alias, quoted=True),
                expression=exp.column(tc, table=alias, quoted=True),
            )
            for fc, tc in zip(join.from_columns, join.to_columns, strict=True)
        )
    )


def time_dimension_for(metric: MetricVersion, layer: Layer, entity: uuid.UUID) -> Dimension | None:
    """The time one table of a metric is read along: the version's time_dimension_id when the
    table reaches it, else the table's own time dimension, else the one time dimension the table
    reaches through approved joins (a fact keyed to a date table). Two reachable and none chosen
    is None: which date a sale counts on (ordered, shipped) is the owner's to say."""
    by_id = {d.id: d for d in layer.dimensions.values()}
    chosen = by_id.get(metric.time_dimension_id) if metric.time_dimension_id else None
    if chosen is not None and _reaches(layer, entity, chosen.code):
        return chosen
    own = next((d for d in layer.dimensions.values() if d.is_time and d.entity_id == entity), None)
    if own is not None:
        return own
    reached = [
        d for d in layer.dimensions.values() if d.is_time and _reaches(layer, entity, d.code)
    ]
    return reached[0] if len(reached) == 1 else None


@dataclass
class _Side:
    """One entity's part of a metric: its select and the datasets it read."""

    select: exp.Select
    dataset_ids: list[uuid.UUID]


def _side(
    request: Request,
    layer: Layer,
    entity: uuid.UUID,
    outputs: Sequence[tuple[str, exp.Expression]],
    grain: str,
    by_day: bool,
    shared: bool = False,
    *,
    dialect: str,
    balance: bool,
) -> _Side:
    """The select of one entity: the requested dimensions, then `outputs`, grouped by the
    dimensions (and by day when the period is averaged), filtered and cut to the period.

    `shared`: the side is one of several tables. The metric's default filters then hold where
    they can (the owner's "trừ cho vay nội bộ" is about the loans, not the revenue); the
    question's filters must hold on every side, or the sides would count different scopes."""
    metric = request.metric
    base = layer.entity_tables.get(entity)
    if base is None:
        raise CompileError("NO_DATA_FOR_PERIOD", "the metric's table is not in the catalogue")
    time_dim = time_dimension_for(metric, layer, entity)

    aliases: dict[uuid.UUID, str] = {base.dataset_id: "t0"}
    # The joins that reached each table: a table already joined is reused only by the same way.
    reached_by: dict[uuid.UUID, tuple[Join, ...]] = {base.dataset_id: ()}
    joins: list[tuple[Table, str, Join, str]] = []  # table, its alias, the join, the from alias
    tables = {t.dataset_id: t for t in layer.entity_tables.values()}

    def alias_for(dim: Dimension) -> str:
        table = layer.entity_tables.get(dim.entity_id)
        if table is None:
            raise CompileError("NO_JOIN_PATH", f"dimension {dim.code} has no table")
        if table.dataset_id in aliases:
            return aliases[table.dataset_id]
        try:
            path = join_path(layer, base.dataset_id, table.dataset_id)
        except CompileError as exc:
            raise CompileError(
                "NO_JOIN_PATH", f"no approved join from the metric to {dim.name}: {exc}"
            ) from exc
        for hop, join in enumerate(path):
            via = path[: hop + 1]
            if join.to_dataset_id in aliases:
                if reached_by[join.to_dataset_id] != via:
                    raise CompileError(
                        "NO_JOIN_PATH",
                        f"bảng {tables[join.to_dataset_id].name} được nối tới bằng hai cách trong"
                        " cùng một câu hỏi",
                    )
                continue
            hop_table = tables.get(join.to_dataset_id)
            if hop_table is None:
                raise CompileError("NO_JOIN_PATH", f"a join on the way to {dim.name} has no table")
            alias = f"t{len(aliases)}"
            aliases[join.to_dataset_id] = alias
            reached_by[join.to_dataset_id] = via
            joins.append((hop_table, alias, join, aliases[join.from_dataset_id]))
        return aliases[table.dataset_id]

    def dimension(code: str) -> Dimension:
        dim = layer.dimensions.get(code)
        if dim is None:
            raise CompileError("UNRESOLVED_ENTITY", f"unknown dimension {code}")
        # BR-81: a question cuts or filters a metric only along the dimensions its owner allowed;
        # none listed allows none. Time is the period's, always allowed.
        if dim.id not in metric.allowed_dimension_ids and not dim.is_time:
            raise CompileError(
                "CONTRACT_MISMATCH", f"{metric.display_name} is not cut by {dim.name}"
            )
        # A time cut of a metric over several tables reads each table's own time.
        if dim.is_time and time_dim is not None and dim.entity_id != entity:
            return time_dim
        return dim

    # The output expressions' columns belong to the base table.
    outputs = [(name, expr.copy()) for name, expr in outputs]
    for _, expr in outputs:
        for column in expr.find_all(exp.Column):
            if not column.table:
                column.set("table", exp.to_identifier("t0"))

    select = exp.select()
    group: list[exp.Expression] = []
    time_cut: Dimension | None = None
    for code in request.group_by:
        dim = dimension(code)
        expr = _qualify(dim.expression, alias_for(dim))
        if dim.is_time:
            time_cut = dim
            expr = _at_grain(expr, dim, grain, layer.calendar, dialect)
        select = select.select(exp.alias_(expr, code, quoted=True))
        group.append(expr)
    if by_day:
        if time_dim is None:
            raise CompileError("NO_DATA_FOR_PERIOD", "the metric's table has no time dimension")
        day = _qualify(time_dim.expression, alias_for(time_dim))
        select = select.select(exp.alias_(day, _DAY, quoted=True))
        group.append(day.copy())
    for name, expr in outputs:
        select = select.select(exp.alias_(expr, name, quoted=True))

    conditions: list[exp.Expression] = []
    if request.knowledge_at is not None:
        if base.history_kind not in {"temporal", "scd2"} or not base.knowledge_time_col:
            raise CompileError(
                "CONTRACT_MISMATCH",
                f"bảng {base.name} chưa khai báo cột thời điểm ghi nhận cho hỏi tại mốc.",
            )
        knowledge_when = _qualify(base.knowledge_time_col, "t0")
        knowledge_value = exp.cast(
            exp.Literal.string(request.knowledge_at.isoformat()), "TIMESTAMP"
        )
        conditions.append(exp.LTE(this=knowledge_when, expression=knowledge_value))
    # The metric's own default filters were set by its owner; the question's go through BR-81.
    filters: list[Filter] = []
    for code in request.filters:
        dimension(code)
    for condition in request.conditions:
        check_filter(condition)
        dimension(condition.dimension)
    for raw in metric.filters:
        f = filter_of(raw)
        if shared and not _reaches(layer, entity, f.dimension):
            continue
        filters.append(f)
    filters += [Filter(code, "in", tuple(values)) for code, values in request.filters.items()]
    filters += list(request.conditions)
    for f in filters:
        filter_dim = layer.dimensions.get(f.dimension)
        if filter_dim is None:
            raise CompileError("CONTRACT_MISMATCH", f"filter on an unknown dimension {f.dimension}")
        conditions.append(_condition(_qualify(filter_dim.expression, alias_for(filter_dim)), f))

    cut_by_day = time_cut is not None and grain == "day"
    # The last-day subquery reads the base table again ("l"), joined to the time's table the way
    # the main query is when the time is on a date table.
    l_alias = "l"
    l_joins: list[tuple[Table, str, Join, str]] = []
    if time_dim is not None and time_dim.entity_id != entity:
        time_table = layer.entity_tables.get(time_dim.entity_id)
        if time_table is not None and time_table.dataset_id != base.dataset_id:
            for hop, join in enumerate(join_path(layer, base.dataset_id, time_table.dataset_id)):
                hop_table = tables[join.to_dataset_id]
                l_joins.append((hop_table, f"l{hop + 1}", join, "l" if hop == 0 else f"l{hop}"))
            l_alias = l_joins[-1][1] if l_joins else "l"
    in_range: exp.Expression | None = None
    in_range_of_l: exp.Expression | None = None
    if request.period is not None:
        if time_dim is None:
            raise CompileError("NO_DATA_FOR_PERIOD", "the metric's table has no time dimension")
        when = _qualify(time_dim.expression, alias_for(time_dim))
        start: exp.Expression
        end: exp.Expression
        if time_dim.value_type == "string":
            start, end = (exp.Literal.string(b) for b in _text_bounds(request.period))
        else:
            start, end = (
                exp.cast(exp.Literal.string(d.isoformat()), "DATE")
                for d in (request.period.start, request.period.end)
            )
        in_range = exp.and_(exp.GTE(this=when, expression=start), exp.LT(this=when, expression=end))
        in_range_of_l = exp.and_(
            exp.GTE(this=_qualify(time_dim.expression, l_alias), expression=start.copy()),
            exp.LT(this=_qualify(time_dim.expression, l_alias), expression=end.copy()),
        )
    if balance and time_dim is not None and not cut_by_day:
        # A balance is read on the last day with data, not summed over days: of the range (or of
        # all data when no period is asked), or of each month, quarter or year of a time cut.
        last_of_l = _qualify(time_dim.expression, l_alias)
        last = exp.select(exp.Max(this=last_of_l)).from_(_table(base, "l"))
        for table, alias, join, from_alias in l_joins:
            last = last.join(
                _table(table, alias), on=_on(join, from_alias, alias), join_type="left"
            )
        if in_range_of_l is not None:
            last = last.where(in_range_of_l)
        when_t0 = _qualify(time_dim.expression, alias_for(time_dim))
        if time_cut is not None:
            last = last.group_by(
                _at_grain(
                    _qualify(time_dim.expression, l_alias), time_dim, grain, layer.calendar, dialect
                )
            )
            conditions.append(exp.In(this=when_t0, query=last.subquery()))
            if in_range is not None:
                conditions.append(in_range)
        else:
            conditions.append(
                exp.EQ(this=when_t0, expression=exp.paren(last.subquery(), copy=False))
            )
    elif in_range is not None:
        conditions.append(in_range)

    # FROM and JOINs last: a filter on another table's dimension adds its join too.
    select = select.from_(_table(base, "t0"))
    for table, alias, join, from_alias in joins:
        select = select.join(
            _table(table, alias), on=_on(join, from_alias, alias), join_type="left"
        )
    if conditions:
        select = select.where(exp.and_(*conditions))
    if group:
        select = select.group_by(*group)
    return _Side(select, list(aliases))


def time_dimension_of(metric: MetricVersion, layer: Layer) -> Dimension | None:
    """The time dimension a cut along time of this metric reads ("theo tháng"): that of its
    first measure's table (time_dimension_for); None when that table has none."""
    _, _, by_entity = _measures(metric, layer)
    return time_dimension_for(metric, layer, next(iter(by_entity)))


def _groups(
    metric: MetricVersion, layer: Layer, by_entity: Mapping[uuid.UUID, list[str]]
) -> list[tuple[uuid.UUID, bool, list[str]]]:
    """The metric's measures by the table they are read on and whether they are read on the
    period's last day: all of them for an end_of_period metric; in a sum_over_period one, the
    measures declared `semi` (a balance), apart from those that add up over time."""
    groups: list[tuple[uuid.UUID, bool, list[str]]] = []
    for entity, codes in by_entity.items():
        if metric.period_semantics == "end_of_period":
            groups.append((entity, True, codes))
            continue
        if metric.period_semantics != "sum_over_period":
            groups.append((entity, False, codes))
            continue
        stock = [c for c in codes if layer.measures[c].additivity == "semi"]
        flow = [c for c in codes if c not in stock]
        groups += [
            (entity, balance, part) for balance, part in ((False, flow), (True, stock)) if part
        ]
    return groups


def _ordered(select: exp.Select, request: Request, dims: Sequence[exp.Expression]) -> exp.Select:
    if not dims:
        return select
    if request.top_n is not None:
        value_order = exp.column("value", quoted=True)
        select = select.order_by(value_order.desc() if request.top_desc else value_order.asc())
        return select.limit(request.top_n)
    return select.order_by(*[d.copy() for d in dims])


def compile_metric(request: Request, layer: Layer, *, dialect: str) -> Compiled:
    metric = request.metric
    tree, measures, by_entity = _measures(metric, layer)
    tree = _safe_division(tree)
    groups = _groups(metric, layer, by_entity)
    grain = request.grain or metric.time_grain
    if grain not in GRAINS:
        raise CompileError("CONTRACT_MISMATCH", f"unknown time grain {grain}")
    by_day = metric.period_semantics == "average_over_period"
    codes = list(request.group_by)

    sides: list[_Side] = []
    if len(groups) == 1:
        entity, balance, _ = groups[0]
        side = _side(
            request,
            layer,
            entity,
            [("value", _swap(tree, measures))],
            grain,
            by_day,
            dialect=dialect,
            balance=balance,
        )
        sides.append(side)
        select = side.select
        # Ordered by the dimensions' own expressions, as before compiler 2.
        dims = list(select.args["group"].expressions) if select.args.get("group") else []
        if by_day:
            dims = dims[: len(codes)]
        else:
            select = _ordered(select, request, dims)
    else:
        # Aggregated per entity (and apart for a balance), then combined by the dimensions (the
        # fan trap, module docstring).
        names = {code: f"m_{code}" for code in measures}
        for raw in metric.filters:
            code = filter_of(raw).dimension
            if not any(_reaches(layer, entity, code) for entity in by_entity):
                raise CompileError("CONTRACT_MISMATCH", f"filter on an unknown dimension {code}")
        parts: list[exp.Select] = []
        for entity, balance, entity_codes in groups:
            side = _side(
                request,
                layer,
                entity,
                [(names[code], measures[code]) for code in entity_codes],
                grain,
                by_day,
                shared=True,
                dialect=dialect,
                balance=balance,
            )
            sides.append(side)
            keys = [*codes, _DAY] if by_day else codes
            parts.append(
                exp.select(
                    *(exp.column(k, quoted=True) for k in keys),
                    *(
                        exp.column(names[code], quoted=True)
                        if code in entity_codes
                        else exp.alias_(exp.Null(), names[code], quoted=True)
                        for code in measures
                    ),
                ).from_(side.select.subquery("s"))
            )
        union: exp.Query = parts[0]
        for part in parts[1:]:
            union = exp.union(union, part, distinct=False)
        keys = [*codes, _DAY] if by_day else codes
        maxed = {code: exp.Max(this=exp.column(names[code], quoted=True)) for code in measures}
        # A side with no row for a member holds NULL there. For a sum or a count that is the
        # side's zero: MAX(a) + MAX(b) was NULL for Components (sold to resellers only) and the
        # cross-channel revenue lost the resellers' 5.4M. With no side holding any figure the
        # value stays NULL: there is no data, not a zero.
        zeroed = {
            code: exp.Coalesce(this=maxed[code].copy(), expressions=[exp.Literal.number(0)])
            if isinstance(measures[code], exp.Sum | exp.Count)
            else maxed[code]
            for code in measures
        }
        combined = _swap(tree, zeroed)
        if zeroed != maxed:
            combined = exp.Case(
                ifs=[
                    exp.If(
                        this=exp.and_(
                            *(exp.Is(this=m.copy(), expression=exp.Null()) for m in maxed.values())
                        ),
                        true=exp.Null(),
                    )
                ],
                default=combined,
            )
        select = exp.select(
            *(exp.column(k, quoted=True) for k in keys),
            exp.alias_(combined, "value", quoted=True),
        ).from_(union.subquery("u"))
        if keys:
            select = select.group_by(*(exp.column(k, quoted=True) for k in keys))
        dims = [exp.column(code, quoted=True) for code in codes]
        if not by_day:
            select = _ordered(select, request, dims)

    if by_day:
        # The mean of the metric's daily values over the period, per member.
        dims = [exp.column(code, quoted=True) for code in codes]
        select = exp.select(
            *dims,
            exp.alias_(exp.Avg(this=exp.column("value", quoted=True)), "value", quoted=True),
        ).from_(select.subquery("d"))
        if dims:
            select = _ordered(select.group_by(*[d.copy() for d in dims]), request, dims)

    sql = select.sql(dialect=dialect)
    dataset_ids = list(dict.fromkeys(d for side in sides for d in side.dataset_ids))
    return Compiled(
        sql=sql,
        dialect=dialect,
        metric_version_id=metric.id,
        dataset_ids=tuple(dataset_ids),
        columns=(*codes, "value"),
        unit=metric.unit,
        decimals=metric.decimals,
        certified=metric.certified,
        sql_sha256=hashlib.sha256(sql.encode()).hexdigest(),
    )
