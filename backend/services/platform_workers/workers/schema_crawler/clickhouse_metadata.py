"""ClickHouse table metadata for the Schema Catalog.

Same contract as snowflake_metadata / postgres_metadata. Included because the DB
Connector node offers ClickHouse as a target alongside Postgres, and a catalog
that covers only some of the engines the agent can query leaves the rest falling
back to dumping information_schema into the prompt — the exact gap this work
closes for Postgres.

Reads `system.tables` / `system.columns` rather than information_schema: the
system tables carry the row count and the modification time, which
information_schema does not expose.
"""
import contextlib
import logging
from typing import Any

from services.platform_workers.workers.schema_crawler.identifiers import backtick_identifier

logger = logging.getLogger(__name__)

DEFAULT_DATABASE = "default"

_TABLES_SQL = """
SELECT name, total_rows, metadata_modification_time
FROM system.tables
WHERE database = {db:String}
  AND engine NOT IN ('View', 'MaterializedView')
"""

_COLUMNS_SQL = """
SELECT table, name, type, comment, position
FROM system.columns
WHERE database = {db:String}
  AND table IN {tables:Array(String)}
ORDER BY table, position
"""


def _client(connection_params: dict[str, Any]) -> Any:
    # Lazy import so a crawler image built without the driver still starts and
    # still crawls every other engine — one unavailable backend must not take
    # the whole worker down.
    try:
        import clickhouse_connect
    except ImportError as e:  # pragma: no cover - depends on image contents
        raise RuntimeError(
            "ClickHouse warehouse configured but clickhouse-connect is not installed "
            "in schema-crawler-worker"
        ) from e

    return clickhouse_connect.get_client(
        host=connection_params.get("host", ""),
        port=int(connection_params.get("port") or 8123),
        username=connection_params.get("user") or "default",
        password=connection_params.get("password") or "",
        database=connection_params.get("database") or DEFAULT_DATABASE,
        connect_timeout=int(connection_params.get("connect_timeout") or 10),
    )


def discover_and_describe(
    connection_params: dict[str, Any], selected_tables: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Returns {table_name: {schema_name, table_name, columns, row_count, last_altered}}.

    connection_params: {host, port, user, password, database}

    `schema_name` carries the ClickHouse *database*: it is the level the catalog
    keys on, and the one a DB Connector connection points at.
    """
    database = connection_params.get("database") or DEFAULT_DATABASE
    client = _client(connection_params)
    try:
        table_rows = client.query(_TABLES_SQL, parameters={"db": database})
        table_stats = {
            row[0]: {"row_count": row[1], "last_altered": row[2]}
            for row in table_rows.result_rows
        }

        names = selected_tables or list(table_stats)
        columns_by_table: dict[str, list[dict[str, Any]]] = {}
        if names:
            column_rows = client.query(
                _COLUMNS_SQL, parameters={"db": database, "tables": list(names)},
            )
            for table, name, ctype, comment, _position in column_rows.result_rows:
                columns_by_table.setdefault(table, []).append({
                    "name": name,
                    "type": ctype,
                    # ClickHouse has no nullable flag of its own — it is encoded
                    # in the type as Nullable(T), so derive it rather than
                    # reporting every column as non-nullable.
                    "nullable": str(ctype).startswith("Nullable("),
                    "comment": comment or None,
                })
    finally:
        with contextlib.suppress(Exception):
            client.close()

    result: dict[str, dict[str, Any]] = {}
    for table_name in names:
        columns = columns_by_table.get(table_name, [])
        if not columns:
            logger.warning("clickhouse_metadata | table %s has no columns, skipping", table_name)
            continue
        stats = table_stats.get(table_name, {})
        result[table_name] = {
            "schema_name": database,
            "table_name": table_name,
            "columns": columns,
            "row_count": stats.get("row_count"),
            "last_altered": stats.get("last_altered"),
        }
    return result


def max_timestamp(
    connection_params: dict[str, Any], schema: str, table: str, column: str,
) -> Any:
    """MAX(column) for one table — the data freshness watermark.

    `schema` carries the ClickHouse database, matching discover_and_describe.
    Identifiers are backtick-quoted (ClickHouse accepts both forms; backticks
    avoid any ambiguity with string literals) and the caller validates them
    first — see postgres_metadata.max_timestamp for why interpolation.
    """
    q = backtick_identifier
    client = _client({**connection_params, "database": schema or connection_params.get("database")})
    try:
        target = f"{q(schema)}.{q(table)}" if schema else q(table)
        result = client.query(f"SELECT max({q(column)}) FROM {target}")
        rows = result.result_rows
        return rows[0][0] if rows else None
    finally:
        with contextlib.suppress(Exception):
            client.close()
