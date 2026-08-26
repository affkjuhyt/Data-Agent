"""Read-only ClickHouse SQL execution — third backend alongside
snowflake_readonly.py and postgres_readonly.py.

Exists because the DB Connector canvas node (workflow-runtime
handlers/data.py) already offers ClickHouse as a target type. Before this
module, that path ran agent-authored SQL straight through
``clickhouse_connect`` inside workflow-runtime with no guard; routing it here
puts it behind the same two layers as every other SQL path: a limited DB user
(CONNECT+SELECT only — grant it in ClickHouse, this module cannot enforce it)
and the sql_guard SELECT-only check below.

``clickhouse_connect`` is synchronous, so the query runs in a worker thread
via ``asyncio.to_thread`` — mirroring libs/database_connector/clickhouse_connector.py.
Unlike postgres_readonly.py there is no connection reuse: clickhouse_connect
clients are HTTP-session based and cheap to create, and per-call clients keep
one tenant's session from being handed to another.
"""
from typing import Any
import asyncio
import logging

from sql_guard import validate_select_only, SQLGuardError

logger = logging.getLogger(__name__)

MAX_ROWS = 10_000


class ClickHouseQueryError(Exception):
    pass


def _new_client(connection_params: dict[str, Any]) -> Any:
    # Lazy import so sandbox-executor still starts (and every non-ClickHouse
    # backend still works) on an image built without the driver.
    try:
        import clickhouse_connect
    except ImportError as e:  # pragma: no cover - depends on image contents
        raise ClickHouseQueryError(
            "ClickHouse backend requested but clickhouse-connect is not installed in sandbox-executor"
        ) from e

    return clickhouse_connect.get_client(
        host=connection_params["host"],
        port=int(connection_params.get("port") or 8123),
        username=connection_params.get("user") or "default",
        password=connection_params.get("password") or "",
        database=connection_params.get("database") or "default",
    )


async def execute_readonly_query(sql: str, connection_params: dict[str, Any]) -> dict[str, Any]:
    """Returns {"rows": list[dict], "row_count": int, "is_truncated": bool}.

    connection_params: {host, port, user, password, database}
    """
    try:
        validate_select_only(sql)
    except SQLGuardError as e:
        raise ClickHouseQueryError(f"SQL rejected: {e}") from e

    # Same wrapping as postgres_readonly.py / snowflake_readonly.py: inner sql
    # on its own line so a trailing "--" comment (even if sql_guard somehow
    # missed it) only swallows its own line, not the closing paren/LIMIT.
    wrapped = f"SELECT * FROM (\n{sql}\n) AS _sandboxed_query LIMIT {MAX_ROWS + 1}"  # noqa: S608 — sql is guard-validated SELECT-only

    def _run() -> list[dict[str, Any]]:
        client = _new_client(connection_params)
        try:
            result = client.query(wrapped)
            cols = result.column_names
            return [dict(zip(cols, row, strict=False)) for row in result.result_rows]
        finally:
            try:
                client.close()
            except Exception:
                pass

    try:
        rows = await asyncio.to_thread(_run)
    except ClickHouseQueryError:
        raise
    except Exception as e:
        raise ClickHouseQueryError(f"query execution failed: {e}") from e

    is_truncated = len(rows) > MAX_ROWS
    rows = rows[:MAX_ROWS]
    return {"rows": rows, "row_count": len(rows), "is_truncated": is_truncated}
