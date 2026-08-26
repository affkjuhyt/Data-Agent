"""Read-only Postgres SQL execution — local/dev/CI stand-in for
snowflake_readonly.py (docs/sandbox_report.md).

Selected only when SANDBOX_SQL_BACKEND=postgres (main.py); the default stays
"snowflake" in production. This exists so `/execute/sql` can be exercised
end-to-end (real connection, real rows) by developers/CI who don't have a
real Snowflake account, without touching the Snowflake code path at all.

Read-only is enforced the same two ways as the Snowflake path: a limited DB
role (infra/sql/create-extra-dbs.sql — `sandbox_reader`, CONNECT+USAGE+SELECT
only, no INSERT/UPDATE/DELETE/DDL grant) and the sql_guard SELECT-only check
below.
"""
from typing import Any
import asyncio
import logging

import asyncpg

from sql_guard import validate_select_only, SQLGuardError

logger = logging.getLogger(__name__)

MAX_ROWS = 10_000


class PostgresQueryError(Exception):
    pass


# Mirrors snowflake_readonly.py's connection-pooling-by-param-set: opening a
# fresh connection per call adds needless auth/round-trip latency for an
# interactive agent calling execute_sql repeatedly. asyncpg is asyncio-native
# (unlike snowflake.connector), so no thread offload or threading.Lock here —
# everything runs directly on the event loop, guarded by an asyncio.Lock.
_connections: dict[tuple[Any, ...], "asyncpg.Connection"] = {}
_connections_lock = asyncio.Lock()


def _connection_key(connection_params: dict[str, Any]) -> tuple[Any, ...]:
    return (
        connection_params["host"],
        connection_params.get("port", 5432),
        connection_params["user"],
        connection_params["password"],
        connection_params["dbname"],
    )


async def _get_connection(connection_params: dict[str, Any]) -> "asyncpg.Connection":
    key = _connection_key(connection_params)
    async with _connections_lock:
        conn = _connections.get(key)
        if conn is not None:
            if not conn.is_closed():
                return conn
            _connections.pop(key, None)

        conn = await asyncpg.connect(
            host=connection_params["host"],
            port=connection_params.get("port", 5432),
            user=connection_params["user"],
            password=connection_params["password"],
            database=connection_params["dbname"],
        )
        _connections[key] = conn
        return conn


async def _drop_connection(connection_params: dict[str, Any], conn: "asyncpg.Connection") -> None:
    async with _connections_lock:
        _connections.pop(_connection_key(connection_params), None)
    try:
        await conn.close()
    except Exception:
        pass


async def execute_readonly_query(sql: str, connection_params: dict[str, Any]) -> dict[str, Any]:
    """Returns {"rows": list[dict], "row_count": int, "is_truncated": bool}.

    connection_params: {host, port, user, password, dbname}
    """
    try:
        validate_select_only(sql)
    except SQLGuardError as e:
        raise PostgresQueryError(f"SQL rejected: {e}") from e

    conn = await _get_connection(connection_params)
    try:
        # Same wrapping as snowflake_readonly.py: inner sql on its own line
        # so a trailing "--" comment (even if sql_guard somehow missed it)
        # only swallows its own line, not the closing paren/LIMIT below.
        records = await conn.fetch(
            f"SELECT * FROM (\n{sql}\n) AS _sandboxed_query LIMIT {MAX_ROWS + 1}"  # noqa: S608 — sql is guard-validated SELECT-only
        )
    except Exception as e:
        # Connection may be dead (idle timeout, network blip) — drop it from
        # the pool so the next call reconnects instead of reusing a broken
        # connection forever.
        await _drop_connection(connection_params, conn)
        raise PostgresQueryError(f"query execution failed: {e}") from e

    rows = [dict(r) for r in records]
    is_truncated = len(rows) > MAX_ROWS
    rows = rows[:MAX_ROWS]
    return {"rows": rows, "row_count": len(rows), "is_truncated": is_truncated}
