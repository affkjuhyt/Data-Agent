"""Snowflake INFORMATION_SCHEMA crawl (doc §2.3).

Deliberately a thin, synchronous, standalone module (not the async
SnowflakeConnector in services/database_connector) — this worker is a
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
import logging
import os
import threading
import time

import snowflake.connector
from snowflake.connector import DictCursor

logger = logging.getLogger(__name__)

# A hanging/slow Snowflake account (suspended warehouse, network blip, quota
# throttling) must not be able to stall the whole cron pass for every other
# tenant queued up behind it in crawl_all()'s sequential loop — see
# docs/Schema_Crawler_Risk_Remediation_Plan.md #3.
LOGIN_TIMEOUT_SECONDS = int(os.environ.get("SNOWFLAKE_LOGIN_TIMEOUT_SECONDS", "10"))
NETWORK_TIMEOUT_SECONDS = int(os.environ.get("SNOWFLAKE_NETWORK_TIMEOUT_SECONDS", "30"))
STATEMENT_TIMEOUT_SECONDS = int(os.environ.get("SNOWFLAKE_STATEMENT_TIMEOUT_SECONDS", "60"))

# fetchmany() batch size for INFORMATION_SCHEMA reads — avoids loading an
# entire TABLES/COLUMNS result set into memory at once for warehouses with
# very large schemas (#4).
FETCH_BATCH_SIZE = int(os.environ.get("SNOWFLAKE_CRAWLER_FETCH_BATCH_SIZE", "5000"))

# Snowflake caps the number of literals/binds a single statement can carry
# comfortably; chunking also keeps parse time bounded regardless of how many
# tables a warehouse's selected_tables config lists (#5).
IN_CLAUSE_CHUNK_SIZE = int(os.environ.get("SNOWFLAKE_CRAWLER_IN_CLAUSE_CHUNK_SIZE", "1000"))

# How long an unused pooled connection is kept alive before being closed and
# evicted. Kept generously above CRAWL_INTERVAL_SECONDS's per-warehouse
# cadence isn't assumed here (this module doesn't import main.py's config) —
# callers (main.py's crawl_all()) decide how often to call evict_idle_connections().
CONNECTION_IDLE_TTL_SECONDS = int(os.environ.get("SNOWFLAKE_CRAWLER_CONNECTION_IDLE_TTL_SECONDS", "172800"))  # 48h

_connections: "dict[tuple, tuple[snowflake.connector.SnowflakeConnection, float]]" = {}
_connections_lock = threading.Lock()


def _connection_key(connection_params: dict) -> tuple:
    return (
        connection_params["account"],
        connection_params["user"],
        connection_params["password"],
        connection_params.get("warehouse"),
        connection_params.get("database"),
        connection_params.get("schema"),
        connection_params.get("role"),
    )


def _get_connection(connection_params: dict):
    key = _connection_key(connection_params)
    now = time.monotonic()
    with _connections_lock:
        cached = _connections.get(key)
        if cached is not None:
            conn, _ = cached
            try:
                if not conn.is_closed():
                    _connections[key] = (conn, now)
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
        login_timeout=LOGIN_TIMEOUT_SECONDS,
        network_timeout=NETWORK_TIMEOUT_SECONDS,
        session_parameters={"STATEMENT_TIMEOUT_IN_SECONDS": STATEMENT_TIMEOUT_SECONDS},
    )
    with _connections_lock:
        _connections[key] = (conn, now)
    return conn


def _drop_connection(connection_params: dict, conn) -> None:
    with _connections_lock:
        _connections.pop(_connection_key(connection_params), None)
    try:
        conn.close()
    except Exception:
        pass


def evict_idle_connections(max_idle_seconds: int = CONNECTION_IDLE_TTL_SECONDS) -> int:
    """Closes and drops any pooled connection not used for max_idle_seconds.

    Meant to be called periodically from main.py's cron tick (crawl_all()) —
    without this, _connections grows monotonically (one entry per distinct
    connection-params tuple ever seen, including every password rotation)
    and every entry's Snowflake session is kept alive forever via
    client_session_keep_alive=True. Returns the number of connections evicted.
    """
    now = time.monotonic()
    with _connections_lock:
        stale = [key for key, (_, last_used) in _connections.items() if now - last_used > max_idle_seconds]
        evicted = [_connections.pop(key)[0] for key in stale]
    for conn in evicted:
        try:
            conn.close()
        except Exception:
            pass
    if evicted:
        logger.info("snowflake_metadata | evicted %d idle connection(s)", len(evicted))
    return len(evicted)


def _fetch_all_batched(cursor) -> list:
    rows = []
    while True:
        batch = cursor.fetchmany(FETCH_BATCH_SIZE)
        if not batch:
            break
        rows.extend(batch)
    return rows


def _chunked(items: list, size: int) -> "list[list]":
    return [items[i:i + size] for i in range(0, len(items), size)]


def discover_and_describe(connection_params: dict, selected_tables: list[str] | None = None) -> dict[str, dict]:
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
        table_stats = {row["TABLE_NAME"]: row for row in _fetch_all_batched(cursor)}

        names = selected_tables or list(table_stats.keys())

        columns_by_table: dict[str, list[dict]] = {}
        for chunk in _chunked(names, IN_CLAUSE_CHUNK_SIZE):
            placeholders = ", ".join(["%s"] * len(chunk))
            cursor.execute(
                f"""SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COMMENT, ORDINAL_POSITION
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = CURRENT_SCHEMA() AND TABLE_NAME IN ({placeholders})
                    ORDER BY TABLE_NAME, ORDINAL_POSITION""",
                tuple(chunk),
            )
            for r in _fetch_all_batched(cursor):
                columns_by_table.setdefault(r["TABLE_NAME"], []).append({
                    "name": r["COLUMN_NAME"],
                    "type": r["DATA_TYPE"],
                    "nullable": r["IS_NULLABLE"] == "YES",
                    "comment": r["COMMENT"],
                })

        result: dict[str, dict] = {}
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
