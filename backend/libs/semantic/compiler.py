"""Metric definition -> SQL. The half that decides *how* the number is computed.

This is the deliberate move of arithmetic away from the language model. The
model still reads the question and may propose a breakdown or a period, but the
aggregation, the expression, the default filters and the business time column
come from an approved contract, so the same metric produces the same arithmetic
every time — which is the only way "revenue was 4.2bn" becomes a number anyone
can be held to.

Everything interpolated is either a validated identifier (models.validate_identifier)
or a literal escaped by type. Nothing from the question reaches the SQL as text.
The output still goes through sandbox-executor's sql_guard and a read-only role,
but that is the outer layer — correctness here is not delegated to it.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

from libs.semantic.models import (
    Aggregation,
    FilterOp,
    InvalidMetricDefinition,
    MetricDefinition,
    MetricFilter,
    TimeGrain,
    UnsupportedRequest,
    aggregation_template,
    validate_identifier,
)

# Row cap on the compiled statement itself. sandbox-executor wraps every query
# in its own MAX_ROWS limit too; this one keeps a careless grouping from
# building a huge intermediate result before that wrapper ever sees it.
DEFAULT_LIMIT = 1000

_DIALECTS = ("postgres", "clickhouse")

# Truncating to a grain differs per engine and there is no portable spelling.
_POSTGRES_TRUNC = "DATE_TRUNC('{unit}', {col})"

# ClickHouse has no DATE_TRUNC taking a unit argument in older versions;
# toStartOf* is the idiomatic and universally available form.
_CLICKHOUSE_TRUNC: dict[TimeGrain, str] = {
    TimeGrain.DAY: "toStartOfDay({col})",
    TimeGrain.WEEK: "toStartOfWeek({col})",
    TimeGrain.MONTH: "toStartOfMonth({col})",
    TimeGrain.QUARTER: "toStartOfQuarter({col})",
    TimeGrain.YEAR: "toStartOfYear({col})",
}


@dataclass(frozen=True)
class CompiledMetric:
    sql: str
    metric_ref: str
    # Everything a provenance block needs, derived from the contract rather than
    # re-stated by the model: the answer can say which formula, which filters and
    # which date column produced the number.
    aggregation: str
    unit: str
    grain: str
    dimensions: tuple[str, ...]
    applied_filters: tuple[str, ...]
    time_column: str = ""


def _quote_literal(value: object) -> str:
    """Escape one literal for direct inclusion in SQL.

    Values reaching here come from a stored contract or from caller-supplied
    filter values, never from raw question text, but they are still escaped by
    type rather than trusted: booleans and numbers are emitted bare, everything
    else becomes a single-quoted string with embedded quotes doubled.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (_dt.datetime, _dt.date)):
        return "'" + value.isoformat() + "'"
    text = str(value)
    if "\x00" in text:
        raise UnsupportedRequest("filter value contains a null byte")
    return "'" + text.replace("'", "''") + "'"


def _render_filter(f: MetricFilter) -> str:
    col = validate_identifier(f.column, "filter column")
    if f.op is FilterOp.IS_NULL:
        return f"{col} IS NULL"
    if f.op is FilterOp.IS_NOT_NULL:
        return f"{col} IS NOT NULL"
    if f.op in (FilterOp.IN, FilterOp.NOT_IN):
        # MetricFilter.__post_init__ guarantees a non-empty sequence for these
        # ops; the isinstance keeps that guarantee legible to the type checker
        # rather than asserting it with a silenced ignore.
        if not isinstance(f.value, (list, tuple)):
            raise UnsupportedRequest(f"{f.op.value} filter on {f.column} needs a list of values")
        values = ", ".join(_quote_literal(v) for v in f.value)
        keyword = "IN" if f.op is FilterOp.IN else "NOT IN"
        return f"{col} {keyword} ({values})"
    operator = {
        FilterOp.EQ: "=", FilterOp.NEQ: "<>",
        FilterOp.GT: ">", FilterOp.GTE: ">=",
        FilterOp.LT: "<", FilterOp.LTE: "<=",
    }[f.op]
    return f"{col} {operator} {_quote_literal(f.value)}"


def _truncate(col: str, grain: TimeGrain, dialect: str) -> str:
    if dialect == "postgres":
        return _POSTGRES_TRUNC.format(unit=grain.value, col=col)
    template = _CLICKHOUSE_TRUNC.get(grain)
    if template is None:
        raise UnsupportedRequest(f"clickhouse cannot bucket by {grain.value}")
    return template.format(col=col)


def compile_metric(
    definition: MetricDefinition,
    *,
    dialect: str = "postgres",
    dimensions: list[str] | None = None,
    grain: TimeGrain | None = None,
    time_range: tuple[str, str] | None = None,
    extra_filters: list[MetricFilter] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> CompiledMetric:
    """Compile one metric request into a single SELECT.

    `dimensions` are business keys, not columns — an unknown key raises rather
    than being passed through, so a hallucinated breakdown fails loudly instead
    of producing SQL against a column that does not exist.
    """
    if dialect not in _DIALECTS:
        raise UnsupportedRequest(f"unsupported dialect {dialect!r} (expected one of {list(_DIALECTS)})")

    effective_grain = grain if grain is not None else definition.default_grain
    if effective_grain is not TimeGrain.NONE and not definition.time_column:
        raise UnsupportedRequest(
            f"metric {definition.ref} cannot be bucketed by {effective_grain.value}: no time_column in its contract"
        )
    if time_range and not definition.time_column:
        raise UnsupportedRequest(
            f"metric {definition.ref} cannot be filtered by time: no time_column in its contract"
        )

    agg_expr = aggregation_template(definition.aggregation).format(expr=definition.expression)
    select_parts = [f"{agg_expr} AS value"]
    group_parts: list[str] = []

    if effective_grain is not TimeGrain.NONE:
        bucket = _truncate(definition.time_column, effective_grain, dialect)
        select_parts.insert(0, f"{bucket} AS period")
        group_parts.append(bucket)

    resolved_dimensions: list[str] = []
    for key in dimensions or []:
        dim = definition.dimension(key)          # raises UnsupportedRequest on unknown key
        select_parts.insert(len(select_parts) - 1, f"{dim.column} AS {dim.key}")
        group_parts.append(dim.column)
        resolved_dimensions.append(dim.key)

    # Contract filters first, then caller filters. Order is cosmetic in SQL but
    # makes the rendered WHERE readable in a provenance block: what the metric
    # always applies, then what this question added.
    all_filters = list(definition.default_filters) + list(extra_filters or [])
    where_parts = [_render_filter(f) for f in all_filters]

    if time_range:
        start, end = time_range
        col = definition.time_column
        where_parts.append(f"{col} >= {_quote_literal(_validate_date(start))}")
        # Half-open interval: `< end` never double-counts a boundary row the way
        # BETWEEN does, and needs no timestamp-vs-date special casing.
        where_parts.append(f"{col} < {_quote_literal(_validate_date(end))}")

    sql = f"SELECT {', '.join(select_parts)} FROM {definition.source_table}"
    if where_parts:
        sql += " WHERE " + " AND ".join(where_parts)
    if group_parts:
        sql += " GROUP BY " + ", ".join(group_parts)
        sql += " ORDER BY " + ", ".join(group_parts)
    sql += f" LIMIT {int(limit)}"

    return CompiledMetric(
        sql=sql,
        metric_ref=definition.ref,
        aggregation=definition.aggregation.value,
        unit=definition.unit,
        grain=effective_grain.value,
        dimensions=tuple(resolved_dimensions),
        applied_filters=tuple(where_parts),
        time_column=definition.time_column,
    )


def _validate_date(value: str) -> str:
    """Accept only ISO dates/datetimes.

    A time range is the one input most likely to be model-authored, so it is
    parsed rather than pattern-matched: anything `fromisoformat` rejects never
    becomes a literal.
    """
    text = str(value).strip()
    try:
        _dt.datetime.fromisoformat(text)
    except ValueError:
        try:
            _dt.date.fromisoformat(text)
        except ValueError as e:
            raise UnsupportedRequest(f"time range bound is not an ISO date: {value!r}") from e
    return text


def definition_summary(definition: MetricDefinition) -> str:
    """One-line human description, for the answer's provenance block and for
    telling the model which metrics exist."""
    bits = [f"{definition.ref}: {definition.name}"]
    if definition.description:
        bits.append(definition.description)
    bits.append(f"{definition.aggregation.value}({definition.expression}) on {definition.source_table}")
    if definition.unit:
        bits.append(f"unit={definition.unit}")
    if definition.time_column:
        bits.append(f"business date={definition.time_column}")
    if definition.dimensions:
        bits.append("by " + "/".join(d.key for d in definition.dimensions))
    if definition.owner:
        bits.append(f"owner={definition.owner}")
    return " | ".join(bits)


__all__ = [
    "Aggregation",
    "CompiledMetric",
    "DEFAULT_LIMIT",
    "InvalidMetricDefinition",
    "compile_metric",
    "definition_summary",
]
