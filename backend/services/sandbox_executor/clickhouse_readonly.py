"""Read-only ClickHouse SQL execution — third backend alongside
snowflake_readonly.py and postgres_readonly.py.

Exists because the DB Connector canvas node (workflow_runtime
handlers/data.py) already offers ClickHouse as a target type. Before this
module, that path ran agent-authored SQL straight through
``clickhouse_connect`` inside workflow_runtime with no guard; routing it here
puts it behind the same layers as every other SQL path:

  1. **The server refuses to write** — the session runs under ClickHouse's own
     ``readonly=2``, where INSERT/ALTER/CREATE/DROP are rejected by the engine
     whatever the user is granted. This is the layer that still holds when the
     credentials are a tenant's own (the DB Connector case) — which is exactly
     when a grant-based assumption does not.
  2. A limited DB user (CONNECT+SELECT only). Worth having, but grant it in
     ClickHouse — this module cannot enforce it.
  3. The sql_guard SELECT-only check below.

``clickhouse_connect`` is synchronous, so the query runs in a worker thread
via ``asyncio.to_thread`` — the same client the old libs/database_connector ClickHouse connector used (since removed).
Unlike postgres_readonly.py there is no connection reuse: clickhouse_connect
clients are HTTP-session based and cheap to create, and per-call clients keep
one tenant's session from being handed to another.
"""
from typing import Any
import asyncio
import logging

import result_limits
from sql_guard import validate_select_only, SQLGuardError

logger = logging.getLogger(__name__)

MAX_ROWS = result_limits.MAX_ROWS

# Server-side ceiling on one query, mirroring postgres_readonly's
# STATEMENT_TIMEOUT_MS: read-only bounds what a query may CHANGE, not what it
# may cost, and MAX_ROWS only bounds the result. Sits under the caller's own
# wall-clock budget (_QUERY_TIMEOUT_S in workflow_runtime's data handler).
MAX_EXECUTION_TIME_S = result_limits.QUERY_TIMEOUT_S

# Session settings, tried most-restrictive first.
#
# ClickHouse refuses a setting change when the session is ALREADY at least as
# restrictive as the change asks for: a profile pinned to readonly=2 rejects
# any attempt to set `readonly` at all, and one pinned to readonly=1 rejects
# every setting. So a rejection here means the server is enforcing more than we
# asked for, and the honest response is to ask for less rather than to fail the
# query — but only downwards, and only in answer to that specific refusal.
# The top rung also carries the RESULT bounds. ClickHouse is the only one of the
# three engines that will enforce a byte budget and a memory ceiling itself,
# which is the only place a runaway join can be stopped before it is computed
# rather than after. They sit above this service's own caps so the accumulator —
# which knows how to report the cut — normally makes it; these catch the result
# whose individual rows are enormous, where the row cap sees nothing wrong.
#
# result_overflow_mode="break" returns the rows produced so far instead of
# raising: for a question whose first 10k rows are a perfectly good sample, a
# truncated answer that says it is truncated beats an error.
_RESULT_BOUNDS = {
    "max_result_rows": result_limits.MAX_ROWS + 1,
    "max_result_bytes": result_limits.MAX_RESULT_BYTES * 2,
    "result_overflow_mode": "break",
    "max_memory_usage": result_limits.CLICKHOUSE_MAX_MEMORY_BYTES,
}

_SETTINGS_LADDER: tuple[dict[str, Any], ...] = (
    {"readonly": 2, "max_execution_time": MAX_EXECUTION_TIME_S, **_RESULT_BOUNDS},
    {"readonly": 2, "max_execution_time": MAX_EXECUTION_TIME_S},
    {"max_execution_time": MAX_EXECUTION_TIME_S},
    {},
)


def _is_readonly_setting_error(exc: BaseException) -> bool:
    """Did ClickHouse refuse a SETTING because the session is already read-only?

    Error 164 (READONLY) — "Cannot modify 'x' setting in readonly mode". The
    text is matched as well as the code because clickhouse_connect surfaces
    server errors through several exception types and does not always carry the
    numeric code through. Misreading some other failure as this one costs one
    retry of a read query with fewer settings — which is why the ladder only
    ever descends.
    """
    if getattr(exc, "code", None) == 164:
        return True
    return "readonly mode" in str(exc).lower()


class ClickHouseQueryError(Exception):
    pass


def _new_client(connection_params: dict[str, Any], settings: dict[str, Any]) -> Any:
    # Lazy import so sandbox_executor still starts (and every non-ClickHouse
    # backend still works) on an image built without the driver.
    try:
        import clickhouse_connect
    except ImportError as e:  # pragma: no cover - depends on image contents
        raise ClickHouseQueryError(
            "ClickHouse backend requested but clickhouse-connect is not installed in sandbox_executor"
        ) from e

    return clickhouse_connect.get_client(
        host=connection_params["host"],
        port=int(connection_params.get("port") or 8123),
        username=connection_params.get("user") or "default",
        password=connection_params.get("password") or "",
        database=connection_params.get("database") or "default",
        # Applied to the handshake query as well, so a profile that refuses
        # these says so here rather than midway through the real query.
        settings=settings,
    )


async def execute_readonly_query(sql: str, connection_params: dict[str, Any]) -> dict[str, Any]:
    """Returns {"rows": list[dict], "row_count": int, "is_truncated": bool}.

    connection_params: {host, port, user, password, database}
    """
    try:
        validate_select_only(sql, "clickhouse")
    except SQLGuardError as e:
        raise ClickHouseQueryError(f"SQL rejected: {e}") from e

    # Same wrapping as postgres_readonly.py / snowflake_readonly.py: inner sql
    # on its own line so a trailing "--" comment (even if sql_guard somehow
    # missed it) only swallows its own line, not the closing paren/LIMIT.
    wrapped = f"SELECT * FROM (\n{sql}\n) AS _sandboxed_query LIMIT {MAX_ROWS + 1}"  # noqa: S608 — sql is guard-validated SELECT-only

    def _run() -> list[dict[str, Any]]:
        refusal: Exception | None = None
        for settings in _SETTINGS_LADDER:
            try:
                client = _new_client(connection_params, settings)
            except Exception as e:
                if not (settings and _is_readonly_setting_error(e)):
                    raise
                refusal = e
                continue
            try:
                result = client.query(wrapped)
                # Materialised before the client is closed below, not after:
                # QueryResult is only guaranteed to be self-contained for a
                # non-streaming query, and that is not a property to lean on
                # from outside the block that owns the client.
                cols = result.column_names
                rows = [dict(zip(cols, row, strict=False)) for row in result.result_rows]
            except Exception as e:
                # Only a refusal of the SETTINGS is retried, and only while a
                # less demanding rung is left. Every other failure is the
                # query's own and reaches the caller unchanged.
                if not (settings and _is_readonly_setting_error(e)):
                    raise
                refusal = e
                continue
            finally:
                try:
                    client.close()
                except Exception:
                    pass
            return rows

        # The last rung asks for nothing, so getting here means the server
        # refused even that — not something to paper over silently.
        raise ClickHouseQueryError(f"ClickHouse rejected the session settings: {refusal}")

    try:
        rows = await asyncio.to_thread(_run)
    except ClickHouseQueryError:
        raise
    except Exception as e:
        raise ClickHouseQueryError(f"query execution failed: {e}") from e

    # clickhouse_connect hands over a finished result, so this bounds what
    # travels onward rather than what the driver already allocated. The
    # server-side settings above are what bound the latter.
    result: dict[str, Any] = result_limits.cap_rows(rows).as_dict()
    return result
