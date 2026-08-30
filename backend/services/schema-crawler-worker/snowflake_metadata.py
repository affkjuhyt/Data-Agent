"""Snowflake INFORMATION_SCHEMA crawl (doc §2.3).

Deliberately a thin, synchronous, standalone module (not the async
SnowflakeConnector in backend/libs/database_connector) — this worker is a
blocking cron/Kafka loop in the same style as memory-consumer/main.py, so a
sync call here costs nothing and keeps the worker self-contained the same
way memory-consumer doesn't import workflow-runtime's async Postgres client
either. discover_and_describe() is the one function tests monkeypatch to
avoid a real Snowflake account (see "Mock for now" in the implementation doc).

Connections are pooled by their full param set (same technique as
sandbox-executor/snowflake_readonly.py) — this worker calls
discover_and_describe() once per warehouse on every cron tick plus once per
DDL-triggered single-table crawl, so re-authenticating from scratch each
time would add ~1-2s of avoidable latency/load to every call.
"""
from typing import Any
import logging
import threading

import snowflake.connector
from snowflake.connector import DictCursor

logger = logging.getLogger(__name__)

_connections: dict[tuple[Any, ...], "snowflake.connector.SnowflakeConnection"] = {}
_connections_lock = threading.Lock()


def _connection_key(connection_params: dict[str, Any]) -> tuple[Any, ...]:
    return (
        connection_params["account"],
        connection_params["user"],
        connection_params["password"],
        connection_params.get("warehouse"),
        connection_params.get("database"),
        connection_params.get("schema"),
        connection_params.get("role"),
    )


def _get_connection(connection_params: dict[str, Any]) -> "snowflake.connector.SnowflakeConnection":
    key = _connection_key(connection_params)
    with _connections_lock:
        conn = _connections.get(key)
        if conn is not None:
            try:
                if not conn.is_closed():
                    return conn
            except Exception:
                pass
            _connections.pop(key, None)

    # Connect outside the lock — this is a blocking network round-trip
    # (~1-2s), and holding _connections_lock for its duration would stall
    # every other thread trying to fetch/cache a connection for any
    # warehouse, not just this one.
    conn = snowflake.connector.connect(
        account=connection_params["account"],
        user=connection_params["user"],
        password=connection_params["password"],
        warehouse=connection_params.get("warehouse"),
        database=connection_params.get("database"),
        schema=connection_params.get("schema"),
        role=connection_params.get("role"),
        client_session_keep_alive=True,
    )
    with _connections_lock:
        _connections[key] = conn
    return conn


def _drop_connection(connection_params: dict[str, Any], conn: "snowflake.connector.SnowflakeConnection") -> None:
    with _connections_lock:
        _connections.pop(_connection_key(connection_params), None)
    try:
        conn.close()
    except Exception:
        pass


def discover_and_describe(connection_params: dict[str, Any], selected_tables: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """Returns {table_name: {schema_name, table_name, columns, row_count, last_altered}}.

    connection_params: {account, user, password, warehouse, database, schema, role}
    selected_tables: restrict to these table names; None = every base/dynamic
    table in the configured schema (doc §2.3 — "Worker thu thập ... metadata
    cấp bảng").
    """
    conn = _get_connection(connection_params)
    try:
        cursor = conn.cursor(DictCursor)
        cursor.execute("""
            SELECT TABLE_NAME, ROW_COUNT, LAST_ALTERED
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = CURRENT_SCHEMA()
              AND TABLE_TYPE IN ('BASE TABLE', 'DYNAMIC TABLE')
        """)
        table_stats = {row["TABLE_NAME"]: row for row in cursor.fetchall()}

        names = selected_tables or list(table_stats.keys())

        columns_by_table: dict[str, list[dict[str, Any]]] = {}
        if names:
            placeholders = ", ".join(["%s"] * len(names))
            cursor.execute(
                f"""SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COMMENT, ORDINAL_POSITION
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = CURRENT_SCHEMA() AND TABLE_NAME IN ({placeholders})
                    ORDER BY TABLE_NAME, ORDINAL_POSITION""",
                tuple(names),
            )
            for r in cursor.fetchall():
                columns_by_table.setdefault(r["TABLE_NAME"], []).append({
                    "name": r["COLUMN_NAME"],
                    "type": r["DATA_TYPE"],
                    "nullable": r["IS_NULLABLE"] == "YES",
                    "comment": r["COMMENT"],
                })

        result: dict[str, dict[str, Any]] = {}
        for table_name in names:
            columns = columns_by_table.get(table_name, [])
            if not columns:
                logger.warning("snowflake_metadata | table %s has no columns, skipping", table_name)
                continue
            stats = table_stats.get(table_name, {})
            result[table_name] = {
                "schema_name": connection_params.get("schema", ""),
                "table_name": table_name,
                "columns": columns,
                "row_count": stats.get("ROW_COUNT"),
                "last_altered": stats.get("LAST_ALTERED"),
            }
        return result
    except Exception:
        # Connection may be dead (idle timeout, network blip) — drop it from
        # the pool so the next call reconnects instead of reusing a broken
        # connection forever.
        _drop_connection(connection_params, conn)
        raise


def max_timestamp(
    connection_params: dict[str, Any], schema: str, table: str, column: str,
) -> Any:
    """MAX(column) for one table — the data freshness watermark.

    Identifiers are left UNQUOTED here, unlike the other engines. Snowflake
    folds an unquoted identifier to upper case and stores it that way, so
    INFORMATION_SCHEMA reports `CREATED_AT` for a column an operator wrote as
    `created_at`; quoting would make the lookup case-sensitive and fail on the
    spelling a human actually typed. The caller validates both names as plain
    identifiers (identifiers.safe_identifier) before they get here.
    """
    conn = _get_connection(connection_params)
    try:
        cursor = conn.cursor()
        target = f"{schema}.{table}" if schema else table
        cursor.execute(f"SELECT MAX({column}) FROM {target}")
        row = cursor.fetchone()
        return row[0] if row else None
    except Exception:
        _drop_connection(connection_params, conn)
        raise
