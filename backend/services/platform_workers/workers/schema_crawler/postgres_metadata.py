"""Postgres table metadata for the Schema Catalog.

Same contract as snowflake_metadata.discover_and_describe, so main.py's crawl
loop, the LLM description step and the Qdrant payload need no engine-specific
branches beyond picking this module.

Why it did not exist: the catalog was written for Snowflake, while the DB
Connector node — the thing that actually runs the agent's SQL — points at
Postgres. So the curated, embedded, tenant-filtered catalog described one set of
databases and the agent queried a different one, and every SQL generation fell
back to dumping information_schema into the prompt.

Connect-and-close per crawl rather than pooling like the Snowflake module: a
crawl is a periodic job, not a request path, and holding an idle connection open
to every tenant's warehouse between passes costs more than reconnecting.
"""
import contextlib
import logging
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

from services.platform_workers.workers.schema_crawler.identifiers import quote_identifier

logger = logging.getLogger(__name__)

DEFAULT_SCHEMA = "public"

# reltuples is the planner's estimate, refreshed by ANALYZE/autovacuum. An exact
# COUNT(*) per table would make a crawl O(warehouse size) and lock nothing
# useful into place — the catalog uses row_count to hint at table significance,
# not to answer questions, so an estimate is the right trade. -1 means "never
# analysed", which is not a count and must not be reported as one.
_TABLES_SQL = """
SELECT t.table_name,
       CASE WHEN c.reltuples < 0 THEN NULL ELSE c.reltuples::bigint END AS row_count
FROM information_schema.tables t
LEFT JOIN pg_class c     ON c.relname = t.table_name
LEFT JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = t.table_schema
WHERE t.table_schema = %s
  AND t.table_type = 'BASE TABLE'
"""

# col_description reads COMMENT ON COLUMN, the Postgres equivalent of
# Snowflake's INFORMATION_SCHEMA.COLUMNS.COMMENT — the human-written note the
# LLM description step leans on when it exists.
_COLUMNS_SQL = """
SELECT c.table_name,
       c.column_name,
       c.data_type,
       c.is_nullable,
       col_description(pgc.oid, c.ordinal_position::int) AS comment
FROM information_schema.columns c
LEFT JOIN pg_class pgc     ON pgc.relname = c.table_name
LEFT JOIN pg_namespace pgn ON pgn.oid = pgc.relnamespace AND pgn.nspname = c.table_schema
WHERE c.table_schema = %s
  AND c.table_name = ANY(%s)
ORDER BY c.table_name, c.ordinal_position
"""


def _connect(connection_params: dict[str, Any]) -> Any:
    return psycopg2.connect(
        host=connection_params.get("host", ""),
        port=int(connection_params.get("port") or 5432),
        user=connection_params.get("user", ""),
        password=connection_params.get("password", ""),
        dbname=connection_params.get("database", ""),
        connect_timeout=int(connection_params.get("connect_timeout") or 10),
    )


def discover_and_describe(
    connection_params: dict[str, Any], selected_tables: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Returns {table_name: {schema_name, table_name, columns, row_count, last_altered}}.

    connection_params: {host, port, user, password, database, schema}
    selected_tables: restrict to these names; None = every base table in the
    configured schema.

    `last_altered` is always None: Postgres keeps no per-table modification
    timestamp in information_schema. main.py's change detection hashes the
    column set instead, so a schema change is still noticed — only the cheap
    timestamp shortcut is unavailable.
    """
    schema = connection_params.get("schema") or DEFAULT_SCHEMA

    conn = _connect(connection_params)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(_TABLES_SQL, (schema,))
            table_stats = {row["table_name"]: row for row in cursor.fetchall()}

            names = selected_tables or list(table_stats.keys())
            columns_by_table: dict[str, list[dict[str, Any]]] = {}
            if names:
                # ANY(%s) with a list rather than an IN (...) built from
                # placeholders: the parameter stays one value whatever the table
                # count, so no statement is assembled from a variable number of
                # interpolations.
                cursor.execute(_COLUMNS_SQL, (schema, list(names)))
                for r in cursor.fetchall():
                    columns_by_table.setdefault(r["table_name"], []).append({
                        "name": r["column_name"],
                        "type": r["data_type"],
                        "nullable": r["is_nullable"] == "YES",
                        "comment": r["comment"],
                    })
    finally:
        with contextlib.suppress(Exception):
            conn.close()

    result: dict[str, dict[str, Any]] = {}
    for table_name in names:
        columns = columns_by_table.get(table_name, [])
        if not columns:
            # A table the caller named that does not exist, or one with no
            # readable columns under this role. Skipping keeps a partial crawl
            # useful instead of failing the whole warehouse.
            logger.warning("postgres_metadata | table %s has no columns, skipping", table_name)
            continue
        stats = table_stats.get(table_name, {})
        result[table_name] = {
            "schema_name": schema,
            "table_name": table_name,
            "columns": columns,
            "row_count": stats.get("row_count"),
            "last_altered": None,
        }
    return result


def max_timestamp(
    connection_params: dict[str, Any], schema: str, table: str, column: str,
) -> Any:
    """MAX(column) for one table — the data freshness watermark.

    Separate from discover_and_describe because it is opt-in per table: it reads
    the table itself rather than the catalog, so it only runs where an operator
    declared a freshness_column. On a large unindexed table MAX() is a full
    scan, which is why this is never inferred and never applied warehouse-wide.

    Identifiers are quoted, and the caller validates them first
    (identifiers.safe_identifier) — they reach the statement by interpolation
    because a column name cannot be a bind parameter.
    """
    q = quote_identifier
    conn = _connect(connection_params)
    try:
        with conn.cursor() as cursor:
            target = f"{q(schema)}.{q(table)}" if schema else q(table)
            cursor.execute(f"SELECT MAX({q(column)}) FROM {target}")
            row = cursor.fetchone()
            return row[0] if row else None
    finally:
        with contextlib.suppress(Exception):
            conn.close()
