"""Read-only Postgres SQL execution — local/dev/CI stand-in for
snowflake_readonly.py (docs/sandbox_report.md).

Selected only when SANDBOX_SQL_BACKEND=postgres (main.py); the default stays
"snowflake" in production. This exists so `/execute/sql` can be exercised
end-to-end (real connection, real rows) by developers/CI who don't have a
real Snowflake account, without touching the Snowflake code path at all.

Read-only is enforced three ways, in decreasing order of trust:

  1. **The server refuses to write.** Every query runs inside an explicit
     ``READ ONLY`` transaction, on a connection that also defaults to one.
     This is the only layer that depends neither on our reading of the SQL
     text nor on how the connection's role happens to be granted — Postgres
     raises 25006 for any write that reaches it.
  2. A limited DB role (deploy/docker/sql/create-extra-dbs.sql — `sandbox_reader`,
     CONNECT+USAGE+SELECT only, no INSERT/UPDATE/DELETE/DDL grant). True of
     the platform's own connection, and NOT something this service may assume
     of a DB Connector node, whose credentials are the tenant's own and
     usually own the tables they read.
  3. The sql_guard SELECT-only check below.

Layer 1 exists because of the gap between 2 and 3: a write the guard does not
recognise as one, run by a role that is allowed to perform it. Read-only bounds
what a query may CHANGE, never what it may cost — that is STATEMENT_TIMEOUT_MS
and MAX_ROWS below.
"""
from typing import Any
import asyncio
import logging

import asyncpg

import result_limits
from sql_guard import validate_select_only, SQLGuardError

logger = logging.getLogger(__name__)

# Re-exported so callers and tests keep one name for it; the value, and the
# byte/cell bounds a row count cannot express, live in result_limits.
MAX_ROWS = result_limits.MAX_ROWS

# Server-side ceiling on one query, in milliseconds. Sits under the caller's own
# wall-clock budget (_QUERY_TIMEOUT_S in workflow_runtime's data handler) so the
# database stops working on an abandoned query rather than holding a connection
# in the pool until it finishes on its own.
STATEMENT_TIMEOUT_MS = result_limits.QUERY_TIMEOUT_S * 1000

# A read-only transaction still holds a connection and a snapshot. This bounds
# how long one may sit open if the caller disappears between BEGIN and COMMIT
# (client timeout, cancelled turn); without it a pooled connection could stay
# pinned in an idle transaction indefinitely.
IDLE_IN_TRANSACTION_TIMEOUT_MS = 30_000


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
            server_settings={
                # sql_scan's Postgres rules assume a backslash inside '...' is an
                # ordinary character, which is what decides where a string — and
                # therefore a statement — ends. That is the default since 9.1,
                # but it is a GUC: a tenant's database can be configured with it
                # off, and then the guard would be reasoning about a different
                # language than the server speaks. Set per session so the
                # assumption is enforced rather than inherited.
                "standard_conforming_strings": "on",
                # Bounds SELECT pg_sleep(...) and any other read-shaped query
                # that costs time rather than rows. The guard cannot judge cost,
                # and MAX_ROWS only bounds the result.
                "statement_timeout": f"{STATEMENT_TIMEOUT_MS}",
                "idle_in_transaction_session_timeout": f"{IDLE_IN_TRANSACTION_TIMEOUT_MS}",
                # Belt to the explicit READ ONLY transaction's braces below.
                # The transaction is the enforcement; this covers anything that
                # ever runs on this connection OUTSIDE one — a future caller
                # here, a statement the driver issues itself — so the
                # connection cannot write even by accident. Sent in the startup
                # packet, so it holds from the first statement rather than from
                # whenever someone remembers to SET it.
                "default_transaction_read_only": "on",
            },
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
        validate_select_only(sql, "postgres")
    except SQLGuardError as e:
        raise PostgresQueryError(f"SQL rejected: {e}") from e

    conn = await _get_connection(connection_params)
    try:
        # The read-only guarantee, stated to the server instead of inferred
        # from the text. sql_guard can only reject what it RECOGNISES as a
        # write; a write reached through a volatile function — or through
        # anything else a SELECT is allowed to call — reads as a SELECT to
        # it. Inside this transaction Postgres refuses all of them (25006)
        # whatever the role is granted, which is the case that matters: a
        # DB Connector connection is the tenant's own credentials, not
        # `sandbox_reader`.
        async with conn.transaction(readonly=True):
            # Same wrapping as snowflake_readonly.py: inner sql on its own line
            # so a trailing "--" comment (even if sql_guard somehow missed it)
            # only swallows its own line, not the closing paren/LIMIT below.
            wrapped = (
                f"SELECT * FROM (\n{sql}\n) AS _sandboxed_query "  # noqa: S608 — sql is guard-validated SELECT-only
                f"LIMIT {result_limits.MAX_ROWS + 1}"
            )
            # Streamed through a server-side cursor rather than conn.fetch().
            # fetch() materialises the entire result before any bound can be
            # applied to it, so a wide join stayed under MAX_ROWS while
            # allocating hundreds of megabytes — the row cap bounded the shape
            # of the answer, never its size. Reading in batches lets the
            # accumulator stop the moment the byte budget is reached, so the
            # rows past that point are never fetched, never decoded and never
            # allocated. This is the only backend where the cap bounds PEAK
            # memory; the other two are handed a finished result by their
            # driver.
            #
            # A cursor requires the transaction that is already open above for
            # read-only enforcement, so it costs nothing extra here.
            accumulator = result_limits.RowAccumulator()
            cursor = await conn.cursor(wrapped)
            while True:
                batch = await cursor.fetch(result_limits.FETCH_BATCH_ROWS)
                if not batch:
                    break
                if not all(accumulator.add(dict(record)) for record in batch):
                    break
    except Exception as e:
        # Connection may be dead (idle timeout, network blip) — drop it from
        # the pool so the next call reconnects instead of reusing a broken
        # connection forever.
        await _drop_connection(connection_params, conn)
        raise PostgresQueryError(f"query execution failed: {e}") from e

    result: dict[str, Any] = accumulator.finish().as_dict()
    return result
