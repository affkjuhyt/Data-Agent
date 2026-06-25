"""Read-only Snowflake SQL execution (doc §1.5).

Connects via a service account that only has USAGE on the schema and SELECT
on individual tables (granted outside this code, at the Snowflake role
level) — no INSERT/UPDATE/DELETE/DDL grant exists for this role at all. The
sql_guard SELECT-only check below is the second, cheaper layer in front of
that.
"""
import asyncio
import logging

import snowflake.connector
from snowflake.connector import DictCursor

from sql_guard import validate_select_only, SQLGuardError

logger = logging.getLogger(__name__)

MAX_ROWS = 10_000


class SnowflakeQueryError(Exception):
    pass


async def execute_readonly_query(sql: str, connection_params: dict) -> dict:
    """Returns {"rows": list[dict], "row_count": int, "is_truncated": bool}.

    connection_params: {account, user, password, warehouse, database, schema, role}
    """
    try:
        validate_select_only(sql)
    except SQLGuardError as e:
        raise SnowflakeQueryError(f"SQL rejected: {e}") from e

    def _query():
        conn = snowflake.connector.connect(
            account=connection_params["account"],
            user=connection_params["user"],
            password=connection_params["password"],
            warehouse=connection_params.get("warehouse"),
            database=connection_params.get("database"),
            schema=connection_params.get("schema"),
            role=connection_params.get("role"),
            client_session_keep_alive=False,
        )
        try:
            cursor = conn.cursor(DictCursor)
            # Fetch one extra row to detect truncation without a separate COUNT(*).
            cursor.execute(f"SELECT * FROM ({sql}) AS _sandboxed_query LIMIT {MAX_ROWS + 1}")  # noqa: S608 — sql is guard-validated SELECT-only
            return cursor.fetchall()
        finally:
            conn.close()

    try:
        rows = await asyncio.to_thread(_query)
    except Exception as e:
        raise SnowflakeQueryError(f"query execution failed: {e}") from e

    is_truncated = len(rows) > MAX_ROWS
    rows = rows[:MAX_ROWS]
    return {"rows": rows, "row_count": len(rows), "is_truncated": is_truncated}
