"""Column lineage of a read-only query (DF-15, F-23): which source column each output column
of a SELECT comes from.

A pipeline step or a derived dataset was linked to its inputs table by table only
(`tables_of`); a steward asking "where does `total` come from" got the table list. The query is
checked by the read-only guard first (nothing else is traced), qualified against the catalogue's
schema (so `*` expands and an unqualified column finds its table), then each output column is
walked back through CTEs, subqueries and every branch of a UNION with sqlglot's lineage.

The answer is always usable: an output column that cannot be traced (a `*` with no schema, a
column no table of the schema has) is listed in `unresolved`, and the tables read are always
there, so a caller can write a table-level edge where a column edge is missing. A query that
takes longer than `timeout_s` to trace falls back to tables only. An output column made of
constants alone has no edge: a literal is no source, and its value is never in an edge.
"""

from __future__ import annotations

import concurrent.futures
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.lineage import Node, lineage
from sqlglot.optimizer.qualify import qualify

from libs.sql_guard import SqlRejected, check_read_only, tables_of

# Tracing is pure CPU on the caller's thread pool; a pathological query (hundreds of nested
# CTEs) must not hold a worker. The thread cannot be killed: it is left to finish, its answer
# dropped, and the caller gets the tables.
TIMEOUT_S = 2.0
_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="sql-lineage")

# {schema: {table: {column: type}}}, sqlglot's MappingSchema shape.
Schema = Mapping[str, Mapping[str, Mapping[str, str]]]


@dataclass(frozen=True)
class Edge:
    source_schema: str | None
    source_table: str
    source_column: str
    output_column: str
    via: str = "sql"


@dataclass(frozen=True)
class Lineage:
    edges: tuple[Edge, ...]
    # Every (schema, table) the query reads, whatever was traced.
    tables: frozenset[tuple[str | None, str]]
    # Output columns with no traced source (not constants): a `*` without schema, an unknown
    # column, a construct sqlglot cannot follow.
    unresolved: tuple[str, ...] = field(default_factory=tuple)
    # False when tracing was cut short (timeout, a query sqlglot cannot qualify): edges is empty
    # and only `tables` holds.
    complete: bool = True


def column_lineage(
    sql: str,
    schema: Schema | None = None,
    dialect: str = "postgres",
    timeout_s: float = TIMEOUT_S,
) -> Lineage:
    """The lineage of a read-only query. Raises SqlRejected for anything the guard refuses."""
    tree = check_read_only(sql, dialect)
    tables = frozenset(tables_of(tree, dialect))
    future = _POOL.submit(_trace, tree, schema, dialect)
    try:
        edges, unresolved = future.result(timeout=timeout_s)
    except concurrent.futures.TimeoutError:
        return Lineage((), tables, (), complete=False)
    except (SqlglotError, SqlRejected, ValueError, KeyError, AttributeError, RecursionError):
        # sqlglot raises more than its own errors on what it cannot follow (a KeyError on an
        # unknown CTE column); none of them makes the tables wrong.
        return Lineage((), tables, (), complete=False)
    return Lineage(tuple(edges), tables, tuple(unresolved))


def _trace(tree: exp.Expr, schema: Schema | None, dialect: str) -> tuple[list[Edge], list[str]]:
    qualified = qualify(
        tree.copy(),
        schema=dict(schema) if schema else None,
        dialect=dialect,
        validate_qualify_columns=False,
        identify=False,
    )
    if not isinstance(qualified, exp.Query):
        raise ValueError("the guard admits a query only")
    edges: list[Edge] = []
    unresolved: list[str] = []
    seen: set[Edge] = set()
    for name in qualified.named_selects:
        if name == "*":
            unresolved.append(name)
            continue
        # Already qualified: the schema did its work there; lineage takes one by signature.
        # A name given as text is normalised (folded to lower case on PostgreSQL), so a quoted
        # "Amount" would not be found: it is passed as the quoted column it already is.
        node = lineage(
            exp.column(name, quoted=True), qualified, schema={}, dialect=dialect, copy=True
        )
        traced, untraced = _leaves(node, name, dialect)
        for edge in traced:
            if edge not in seen:
                seen.add(edge)
                edges.append(edge)
        if untraced and not traced:
            unresolved.append(name)
    return edges, unresolved


def _leaves(node: Node, output: str, dialect: str) -> tuple[list[Edge], bool]:
    """The source columns under one output column, and whether some branch reached no table
    through a column (a constant-only branch is neither)."""
    traced: list[Edge] = []
    untraced = False
    for leaf in node.walk():
        if leaf.downstream:
            continue
        source = leaf.source
        if isinstance(source, exp.Table) and isinstance(source.this, exp.Identifier):
            column = exp.maybe_parse(leaf.name, into=exp.Column, dialect=dialect)
            db = source.args.get("db")
            traced.append(
                Edge(
                    source_schema=db.name if isinstance(db, exp.Identifier) else None,
                    source_table=source.name,
                    source_column=column.name,
                    output_column=output,
                )
            )
        elif any(True for _ in _columns(leaf.expression)):
            untraced = True
    return traced, untraced


def _columns(expression: Any) -> Any:
    if isinstance(expression, exp.Expr):
        yield from expression.find_all(exp.Column)
