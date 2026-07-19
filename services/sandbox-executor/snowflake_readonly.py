"""Read-only Snowflake SQL execution (doc §1.5).

Connects via a service account that only has USAGE on the schema and SELECT
on individual tables (granted outside this code, at the Snowflake role
level) — no INSERT/UPDATE/DELETE/DDL grant exists for this role at all. The
sql_guard SELECT-only check below is the second, cheaper layer in front of
that.
"""
from typing import Any, cast
import asyncio
import logging
import threading

import snowflake.connector
from snowflake.connector import DictCursor

from sql_guard import validate_select_only, SQLGuardError

logger = logging.getLogger(__name__)

MAX_ROWS = 10_000


class SnowflakeQueryError(Exception):
    pass


# Snowflake auth takes ~1-2s; for an interactive agent calling execute_sql
# repeatedly, opening a fresh connection per call adds that latency to every
# single tool call. Connections are pooled by their full param set (password
# included, since a different password is a different identity) and reused
# across calls/requests within this process.
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
        _connections[key] = conn
        return conn


def _drop_connection(connection_params: dict[str, Any], conn: "snowflake.connector.SnowflakeConnection") -> None:
    with _connections_lock:
        _connections.pop(_connection_key(connection_params), None)
    try:
        conn.close()
    except Exception:
        pass


async def execute_readonly_query(sql: str, connection_params: dict[str, Any]) -> dict[str, Any]:
    """Returns {"rows": list[dict], "row_count": int, "is_truncated": bool}.

    connection_params: {account, user, password, warehouse, database, schema, role}
    """
    try:
        validate_select_only(sql)
    except SQLGuardError as e:
        raise SnowflakeQueryError(f"SQL rejected: {e}") from e

    def _query() -> list[Any]:
        conn = _get_connection(connection_params)
        try:
            cursor = conn.cursor(DictCursor)
            # Inner sql on its own line — defense in depth alongside
            # sql_guard's comment rejection: even if a comment slipped
            # through, it would only swallow the rest of *its own* line, not
            # the closing paren/LIMIT that follow on the next one.
            cursor.execute(
                f"SELECT * FROM (\n{sql}\n) AS _sandboxed_query LIMIT {MAX_ROWS + 1}"  # noqa: S608 — sql is guard-validated SELECT-only
            )
            return cast(list[Any], cursor.fetchall())
        except Exception:
            # Connection may be dead (idle timeout, network blip) — drop it
            # from the pool so the next call reconnects instead of reusing
            # a broken connection forever.
            _drop_connection(connection_params, conn)
            raise

    try:
        rows = await asyncio.to_thread(_query)
    except Exception as e:
        raise SnowflakeQueryError(f"query execution failed: {e}") from e

    is_truncated = len(rows) > MAX_ROWS
    rows = rows[:MAX_ROWS]
    return {"rows": rows, "row_count": len(rows), "is_truncated": is_truncated}
