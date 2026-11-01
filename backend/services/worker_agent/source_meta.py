"""A source's tables, keys and column profiles on the engines other than PostgreSQL (GEN-12).

schema_scans read every source through asyncpg and pg_catalog, so a MySQL or SQL Server source
could not be scanned or profiled at all, and a ClickHouse profile failed. Here the metadata comes
from INFORMATION_SCHEMA, which both MySQL and SQL Server serve (foreign keys are the one place
they differ), and each profile query is written once and rendered by sqlglot in the source's
dialect. Queries go through the executor's own drivers, so they keep its read-only transaction,
its timeout and the source's TLS settings; nothing here returns a raw row of a table.

PostgreSQL stays on pg_catalog in schema_scans: it also gives the planner's row estimates.
"""

from __future__ import annotations

import decimal
import json
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import httpx
from sqlglot import exp

from libs.dataagent_db.models import catalog
from libs.sql_guard import dialect_of
from services.worker_agent.query.executor import SourceDriverError, driver_for
from services.worker_agent.query.scope import default_schema
from services.worker_agent.understand.value_retrieval import MAX_CATEGORY_CARDINALITY

log = logging.getLogger(__name__)

Run = Callable[[str], Awaitable[tuple[list[dict[str, str]], list[list[Any]]]]]
TOP_VALUES = 10


# ClickHouse reads backslash escapes inside quoted identifiers, so a quote-doubling quoter can be
# escaped out of; a name it profiles must be a plain identifier (as its scan already requires).
_CLICKHOUSE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _in_list(values: Sequence[str], dialect: str) -> str:
    """String literals rendered by sqlglot in the source's dialect: on MySQL a backslash escapes,
    so doubling quotes alone would let a schema name like `a\\' OR 1=1 --` close the string."""
    return ", ".join(exp.Literal.string(v).sql(dialect=dialect) for v in values)


def schemas_of(source: catalog.Source) -> list[str]:
    """The schemas a scan reads: the source's own list, else its one default (the database on
    MySQL, dbo on SQL Server)."""
    if source.scan_schemas:
        return [str(s) for s in source.scan_schemas]
    return [default_schema(source) or ("dbo" if source.kind == "sqlserver" else "public")]


def _columns_sql(kind: str, schemas: Sequence[str]) -> str:
    dialect = dialect_of(kind)
    rows = "t.TABLE_ROWS" if kind == "mysql" else "0"
    return f"""
        SELECT c.TABLE_SCHEMA, c.TABLE_NAME, t.TABLE_TYPE, {rows}, c.COLUMN_NAME, c.DATA_TYPE,
               c.IS_NULLABLE, c.ORDINAL_POSITION
        FROM INFORMATION_SCHEMA.COLUMNS c
        JOIN INFORMATION_SCHEMA.TABLES t
          ON t.TABLE_SCHEMA = c.TABLE_SCHEMA AND t.TABLE_NAME = c.TABLE_NAME
        WHERE c.TABLE_SCHEMA IN ({_in_list(schemas, dialect)})
        ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION
    """


def _keys_sql(kind: str, schemas: Sequence[str]) -> str:
    dialect = dialect_of(kind)
    # MySQL names every primary key PRIMARY: the table belongs in the join.
    return f"""
        SELECT tc.TABLE_SCHEMA, tc.TABLE_NAME, tc.CONSTRAINT_NAME, tc.CONSTRAINT_TYPE, k.COLUMN_NAME
        FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
        JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE k
          ON k.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND k.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
         AND k.TABLE_SCHEMA = tc.TABLE_SCHEMA AND k.TABLE_NAME = tc.TABLE_NAME
        WHERE tc.CONSTRAINT_TYPE IN ('PRIMARY KEY', 'UNIQUE')
          AND tc.TABLE_SCHEMA IN ({_in_list(schemas, dialect)})
    """


def _foreign_keys_sql(kind: str, schemas: Sequence[str]) -> str:
    dialect = dialect_of(kind)
    if kind == "mysql":
        return f"""
            SELECT k.TABLE_SCHEMA, k.TABLE_NAME, k.CONSTRAINT_NAME, k.COLUMN_NAME,
                   k.REFERENCED_TABLE_SCHEMA, k.REFERENCED_TABLE_NAME, k.REFERENCED_COLUMN_NAME
            FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE k
            WHERE k.REFERENCED_TABLE_NAME IS NOT NULL AND k.TABLE_SCHEMA IN ({_in_list(schemas, dialect)})
            ORDER BY k.TABLE_SCHEMA, k.TABLE_NAME, k.CONSTRAINT_NAME, k.ORDINAL_POSITION
        """
    # SQL Server keeps the referenced side as the unique constraint the key points at.
    return f"""
        SELECT k.TABLE_SCHEMA, k.TABLE_NAME, k.CONSTRAINT_NAME, k.COLUMN_NAME,
               p.TABLE_SCHEMA, p.TABLE_NAME, p.COLUMN_NAME
        FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS r
        JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE k
          ON k.CONSTRAINT_SCHEMA = r.CONSTRAINT_SCHEMA AND k.CONSTRAINT_NAME = r.CONSTRAINT_NAME
        JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE p
          ON p.CONSTRAINT_SCHEMA = r.UNIQUE_CONSTRAINT_SCHEMA
         AND p.CONSTRAINT_NAME = r.UNIQUE_CONSTRAINT_NAME AND p.ORDINAL_POSITION = k.ORDINAL_POSITION
        WHERE k.TABLE_SCHEMA IN ({_in_list(schemas, dialect)})
        ORDER BY k.TABLE_SCHEMA, k.TABLE_NAME, k.CONSTRAINT_NAME, k.ORDINAL_POSITION
    """


async def scan(
    source: catalog.Source,
    tables: list[str] | None,
    run: Run,
    normalized_type: Callable[[str], str],
) -> list[dict[str, object]]:
    """The tables, columns, primary keys and foreign keys of a MySQL or SQL Server source, in the
    shape schema_scans writes to the catalog. `run` executes one SQL on the source."""
    schemas = schemas_of(source)
    requested = set(tables or [])
    _, column_rows = await run(_columns_sql(source.kind, schemas))
    _, key_rows = await run(_keys_sql(source.kind, schemas))
    _, fk_rows = await run(_foreign_keys_sql(source.kind, schemas))

    constraints: dict[tuple[str, str, str], tuple[str, list[str]]] = {}
    for schema, table, name, kind, column in key_rows:
        constraints.setdefault((str(schema), str(table), str(name)), (str(kind), []))[1].append(
            str(column)
        )
    primary = {
        (s, t, c)
        for (s, t, _), (kind, columns) in constraints.items()
        if kind == "PRIMARY KEY"
        for c in columns
    }
    unique_sets = {(s, t, frozenset(columns)) for (s, t, _), (_, columns) in constraints.items()}

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for (
        schema,
        table,
        table_type,
        row_estimate,
        column,
        data_type,
        nullable,
        ordinal,
    ) in column_rows:
        key = (str(schema), str(table))
        if requested and f"{key[0]}.{key[1]}" not in requested:
            continue
        entry = grouped.setdefault(
            key,
            {
                "schema_name": key[0],
                "table_name": key[1],
                "object_kind": "view" if str(table_type).upper() == "VIEW" else "table",
                "row_estimate": max(int(row_estimate or 0), 0),
                "columns": [],
                "foreign_keys": [],
            },
        )
        entry["columns"].append(
            {
                "name": str(column),
                "ordinal": int(ordinal),
                "data_type": str(data_type),
                "normalized_type": normalized_type(str(data_type)),
                "is_nullable": str(nullable).upper() == "YES",
                "is_key": (key[0], key[1], str(column)) in primary,
            }
        )

    by_constraint: dict[tuple[str, str, str], list[tuple[str, str, str, str]]] = {}
    for schema, table, name, column, parent_schema, parent_table, parent_column in fk_rows:
        by_constraint.setdefault((str(schema), str(table), str(name)), []).append(
            (str(column), str(parent_schema), str(parent_table), str(parent_column))
        )
    for (schema, table, _), pairs in by_constraint.items():
        found = grouped.get((schema, table))
        if found is None:
            continue
        # A child whose key columns are themselves a primary or unique key is one-to-one.
        child_unique = (schema, table, frozenset(c for c, *_ in pairs)) in unique_sets
        for column, parent_schema, parent_table, parent_column in pairs:
            found["foreign_keys"].append(
                {
                    "child_column": column,
                    "parent_schema": parent_schema,
                    "parent_table": parent_table,
                    "parent_column": parent_column,
                    "child_unique": child_unique,
                }
            )
    return list(grouped.values())


def stats_sql(schema: str, table: str, column: str, dialect: str) -> str:
    """Row count, nulls, distinct values, min and max as text: one row, in `dialect`."""
    col = exp.column(column, quoted=True)
    source = exp.table_(table, db=schema, quoted=True)
    statement = exp.select(
        exp.alias_(exp.Count(this=exp.Star()), "sample_rows"),
        exp.alias_(
            exp.Sum(
                this=exp.Case()
                .when(exp.Is(this=col.copy(), expression=exp.Null()), exp.Literal.number(1))
                .else_(exp.Literal.number(0))
            ),
            "null_count",
        ),
        exp.alias_(exp.Count(this=exp.Distinct(expressions=[col.copy()])), "distinct_est"),
        exp.alias_(exp.cast(exp.Min(this=col.copy()), "TEXT"), "min_value"),
        exp.alias_(exp.cast(exp.Max(this=col.copy()), "TEXT"), "max_value"),
    ).from_(source)
    return statement.sql(dialect=dialect)


def top_values_sql(
    schema: str, table: str, column: str, dialect: str, limit: int = TOP_VALUES
) -> str:
    """The most frequent values as text with their counts, in `dialect` (TOP on SQL Server)."""
    col = exp.column(column, quoted=True)
    statement = (
        exp.select(
            exp.alias_(exp.cast(col.copy(), "TEXT"), "value"),
            exp.alias_(exp.Count(this=exp.Star()), "value_count"),
        )
        .from_(exp.table_(table, db=schema, quoted=True))
        .where(exp.Not(this=exp.Is(this=col.copy(), expression=exp.Null())))
        .group_by(col.copy())
        .order_by(exp.Ordered(this=exp.column("value_count"), desc=True), exp.column("value"))
        .limit(limit)
    )
    return statement.sql(dialect=dialect)


async def profile(
    source: catalog.Source,
    tables: list[dict[str, object]],
    run: Run,
    is_sensitive: Callable[[str], bool],
    shown: Callable[[str, Any], str],
) -> dict[tuple[str, str, str], dict[str, object]]:
    """Bounded aggregates per column, as schema_scans' PostgreSQL profile gives them. A column the
    engine cannot aggregate (a SQL Server text or image column has no MIN) is left unprofiled
    rather than failing the table."""
    dialect = dialect_of(source.kind)
    out: dict[tuple[str, str, str], dict[str, object]] = {}
    for table in tables:
        schema_name, table_name = str(table["schema_name"]), str(table["table_name"])
        for column in table["columns"]:  # type: ignore[attr-defined]
            name = str(column["name"])
            if dialect == "clickhouse" and not all(
                _CLICKHOUSE_NAME.fullmatch(n) for n in (schema_name, table_name, name)
            ):
                log.warning(
                    "profile skipped %s.%s.%s: not a plain name", schema_name, table_name, name
                )
                continue
            try:
                _, stats = await run(stats_sql(schema_name, table_name, name, dialect))
                category = int(stats[0][2] or 0) <= MAX_CATEGORY_CARDINALITY
                top: list[list[Any]] = []
                if not is_sensitive(name):
                    limit = MAX_CATEGORY_CARDINALITY if category else TOP_VALUES
                    _, top = await run(
                        top_values_sql(schema_name, table_name, name, dialect, limit)
                    )
            except SourceDriverError:
                log.warning("profile skipped %s.%s.%s", schema_name, table_name, name)
                continue
            sample_rows, nulls, distinct, low, high = stats[0]
            sample_rows = int(sample_rows or 0)
            out[(schema_name, table_name, name)] = {
                "sample_rows": sample_rows,
                "null_ratio": decimal.Decimal(str(int(nulls or 0) / sample_rows))
                if sample_rows
                else decimal.Decimal("0"),
                "distinct_est": int(distinct or 0),
                "min_value": None if is_sensitive(name) else (None if low is None else str(low)),
                "max_value": None if is_sensitive(name) else (None if high is None else str(high)),
                "top_values": [{"value": shown(name, v), "count": int(n)} for v, n in top][
                    :TOP_VALUES
                ],
                "category_values": [{"value": shown(name, v), "count": int(n)} for v, n in top]
                if category
                else [],
            }
    return out


def driver_run(source: catalog.Source, user: str, password: str) -> Run:
    """`run` over the executor's driver for the source's engine, with the source's timeout."""
    driver = driver_for(source.kind)
    timeout_s = min(int(source.query_timeout_s or 120), 300)

    async def run(sql: str) -> tuple[list[dict[str, str]], list[list[Any]]]:
        result: tuple[list[dict[str, str]], list[list[Any]]] = await driver(
            source, user, password, sql, timeout_s
        )
        return result

    return run


def clickhouse_run(
    client: httpx.AsyncClient, url: str, database: str, auth: tuple[str, str]
) -> Run:
    """`run` over ClickHouse's HTTP interface, rows as JSON arrays."""

    async def run(sql: str) -> tuple[list[dict[str, str]], list[list[Any]]]:
        response = await client.post(
            url,
            params={"database": database, "default_format": "JSONCompact", "readonly": "1"},
            content=sql.encode(),
            auth=auth,
        )
        if response.status_code != 200:
            raise SourceDriverError(f"clickhouse query failed ({response.status_code})")
        body = json.loads(response.text)
        columns = [{"name": m["name"], "type": m["type"]} for m in body.get("meta", [])]
        return columns, [list(row) for row in body.get("data", [])]

    return run
