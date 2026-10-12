"""The semantic compiler (tab 5 G5.2): a metric version, the dimensions and filters the question
asked for, and a period → one SQL statement in the source's dialect.

Deterministic and pure: it reads the semantic layer as loaded (loader.py), never the database, and
never an LLM. What it does:

  * the metric's expression is written over measure codes (e.g. `no_xau / du_no * 100`); each code
    is replaced by its measure's own aggregate expression (`SUM(npl_amt)`);
  * the measures' entity gives the base table (catalog.dataset schema.table); a requested
    dimension on another entity is joined through an active many-to-one relationship from the base
    (one hop; anything else is NO_JOIN_PATH, never a guessed join);
  * the metric's default filters and the question's dimension filters become WHERE clauses with
    values as literals sqlglot quotes (no text is spliced into SQL);
  * the period follows period_semantics: `sum_over_period` aggregates the whole range,
    `end_of_period` the last day of the range that has data (a balance is not summed over days),
    `average_over_period` is not compiled yet (UNSUPPORTED);
  * output: the requested dimensions, then `value`, grouped and ordered by the dimensions.

The SQL hash and COMPILER_VERSION go on the receipt (qa.answer_receipt, ADR-43), so a figure can be
traced to exactly what produced it.
"""

from __future__ import annotations

import datetime
import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

COMPILER_VERSION = "v3-compiler-1"


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


@dataclass(frozen=True)
class Measure:
    code: str
    expression: str
    entity_id: uuid.UUID


@dataclass(frozen=True)
class Dimension:
    id: uuid.UUID
    code: str
    name: str
    expression: str
    entity_id: uuid.UUID
    is_time: bool = False


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


@dataclass(frozen=True)
class Layer:
    """The part of a Data Space's semantic layer one compile needs."""

    measures: dict[str, Measure]
    dimensions: dict[str, Dimension]  # by code
    entity_tables: dict[uuid.UUID, Table]  # entity id → its primary dataset
    joins: tuple[Join, ...] = ()


@dataclass(frozen=True)
class Period:
    start: datetime.date  # inclusive
    end: datetime.date  # exclusive


@dataclass
class Request:
    metric: MetricVersion
    group_by: Sequence[str] = ()  # dimension codes
    filters: dict[str, Sequence[str]] = field(default_factory=dict)  # dimension code → values
    period: Period | None = None


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


def _measure_entity(metric: MetricVersion, layer: Layer) -> tuple[exp.Expression, uuid.UUID]:
    try:
        tree = parse_one(metric.expression)
    except ParseError as exc:
        raise CompileError("CONTRACT_MISMATCH", "the metric's expression does not parse") from exc
    entities: set[uuid.UUID] = set()
    measures: dict[str, exp.Expression] = {}
    for column in tree.find_all(exp.Column):
        measure = layer.measures.get(column.name)
        if measure is None:
            raise CompileError(
                "CONTRACT_MISMATCH", f"the metric names a measure that is not defined: {column.name}"
            )
        entities.add(measure.entity_id)
        try:
            measures[column.name] = parse_one(measure.expression)
        except ParseError as exc:
            raise CompileError("CONTRACT_MISMATCH", f"measure {measure.code} does not parse") from exc
    if not measures:
        raise CompileError("CONTRACT_MISMATCH", "the metric's expression names no measure")
    if len(entities) != 1:
        # Measures of two entities need pre-aggregation per entity (fan/chasm traps, G5.2).
        raise CompileError("NO_JOIN_PATH", "the metric combines measures of several tables")

    # Matched by name: transform() works on a copy, so the nodes found above are not the ones
    # it visits. The metric's expression names only measures (checked above).
    def swap(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Column) and node.name in measures:
            return exp.paren(measures[node.name].copy(), copy=False)
        return node

    return tree.transform(swap), next(iter(entities))


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


def compile_metric(request: Request, layer: Layer, *, dialect: str) -> Compiled:
    metric = request.metric
    value_expr, base_entity = _measure_entity(metric, layer)
    base = layer.entity_tables.get(base_entity)
    if base is None:
        raise CompileError("NO_DATA_FOR_PERIOD", "the metric's table is not in the catalogue")

    aliases: dict[uuid.UUID, str] = {base.dataset_id: "t0"}
    joins: list[tuple[Table, str, Join]] = []

    def alias_for(dim: Dimension) -> str:
        table = layer.entity_tables.get(dim.entity_id)
        if table is None:
            raise CompileError("NO_JOIN_PATH", f"dimension {dim.code} has no table")
        if table.dataset_id in aliases:
            return aliases[table.dataset_id]
        join = next(
            (
                j
                for j in layer.joins
                if j.from_dataset_id == base.dataset_id and j.to_dataset_id == table.dataset_id
            ),
            None,
        )
        if join is None:
            raise CompileError("NO_JOIN_PATH", f"no approved join from the metric to {dim.name}")
        alias = f"t{len(aliases)}"
        aliases[table.dataset_id] = alias
        joins.append((table, alias, join))
        return alias

    def dimension(code: str) -> Dimension:
        dim = layer.dimensions.get(code)
        if dim is None:
            raise CompileError("UNRESOLVED_ENTITY", f"unknown dimension {code}")
        if (
            metric.allowed_dimension_ids
            and dim.id not in metric.allowed_dimension_ids
            and not dim.is_time
        ):
            # BR-81: a metric is cut only along the dimensions its owner allowed.
            raise CompileError(
                "CONTRACT_MISMATCH", f"{metric.display_name} is not cut by {dim.name}"
            )
        return dim

    # The value expression's columns belong to the base table.
    for column in value_expr.find_all(exp.Column):
        if not column.table:
            column.set("table", exp.to_identifier("t0"))

    select = exp.select()
    group: list[exp.Expression] = []
    for code in request.group_by:
        dim = dimension(code)
        expr = _qualify(dim.expression, alias_for(dim))
        select = select.select(exp.alias_(expr, code, quoted=True))
        group.append(expr)
    select = select.select(exp.alias_(value_expr, "value", quoted=True))
    select = select.from_(_table(base, "t0"))
    for table, alias, join in joins:
        on = exp.and_(
            *(
                exp.EQ(
                    this=exp.column(fc, table="t0", quoted=True),
                    expression=exp.column(tc, table=alias, quoted=True),
                )
                for fc, tc in zip(join.from_columns, join.to_columns, strict=True)
            )
        )
        select = select.join(_table(table, alias), on=on, join_type="left")

    conditions: list[exp.Expression] = []
    filters: list[tuple[str, Sequence[object], bool]] = []
    for f in metric.filters:
        code = str(f.get("dimension") or f.get("column") or "")
        values = f.get("values", f.get("value"))
        values = values if isinstance(values, list) else [values]
        filters.append((code, values, str(f.get("op", "in")) in ("!=", "not_in", "<>")))
    filters += [(code, list(values), False) for code, values in request.filters.items()]
    for code, values, negated in filters:
        on = layer.dimensions.get(code)
        if on is None:
            raise CompileError("CONTRACT_MISMATCH", f"filter on an unknown dimension {code}")
        expr = _qualify(on.expression, alias_for(on))
        cond: exp.Expression = exp.In(this=expr, expressions=[_literal(v) for v in values])
        conditions.append(exp.not_(cond) if negated else cond)

    if request.period is not None:
        time_dim = next(
            (d for d in layer.dimensions.values() if d.is_time and d.entity_id == base_entity), None
        )
        if time_dim is None:
            raise CompileError("NO_DATA_FOR_PERIOD", "the metric's table has no time dimension")
        when = _qualify(time_dim.expression, "t0")
        start, end = (
            exp.cast(exp.Literal.string(d.isoformat()), "DATE")
            for d in (request.period.start, request.period.end)
        )
        in_range = exp.and_(exp.GTE(this=when, expression=start), exp.LT(this=when, expression=end))
        if metric.period_semantics == "end_of_period" and time_dim.code not in request.group_by:
            # A balance is read on the last day of the range that has data, not summed over days.
            last = (
                exp.select(exp.Max(this=_qualify(time_dim.expression, "l")))
                .from_(_table(base, "l"))
                .where(
                    exp.and_(
                        exp.GTE(this=_qualify(time_dim.expression, "l"), expression=start.copy()),
                        exp.LT(this=_qualify(time_dim.expression, "l"), expression=end.copy()),
                    )
                )
            )
            conditions.append(exp.EQ(this=when, expression=exp.paren(last.subquery(), copy=False)))
        elif metric.period_semantics == "average_over_period":
            raise CompileError("UNSUPPORTED", "averages over a period are not compiled yet")
        else:
            conditions.append(in_range)

    if conditions:
        select = select.where(exp.and_(*conditions))
    if group:
        select = select.group_by(*group).order_by(*[g.copy() for g in group])

    sql = select.sql(dialect=dialect)
    return Compiled(
        sql=sql,
        dialect=dialect,
        metric_version_id=metric.id,
        dataset_ids=tuple(aliases),
        columns=(*request.group_by, "value"),
        unit=metric.unit,
        decimals=metric.decimals,
        certified=metric.certified,
        sql_sha256=hashlib.sha256(sql.encode()).hexdigest(),
    )
