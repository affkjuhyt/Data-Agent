"""Read-only SQL backend loading and job execution orchestration."""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any, Callable


@functools.cache
def load_backend(backend: str) -> tuple[Callable[..., Any], type[Exception]]:
    if backend == "postgres":
        from postgres_readonly import PostgresQueryError, execute_readonly_query

        return execute_readonly_query, PostgresQueryError
    if backend == "snowflake":
        from snowflake_readonly import SnowflakeQueryError, execute_readonly_query

        return execute_readonly_query, SnowflakeQueryError
    if backend == "clickhouse":
        from clickhouse_readonly import ClickHouseQueryError, execute_readonly_query

        return execute_readonly_query, ClickHouseQueryError
    raise ValueError(f"unknown SQL backend: {backend!r}")


async def run_sql_job(
    payload: dict[str, Any],
    *,
    backend_loader: Callable[[str], tuple[Callable[..., Any], type[Exception]]],
    validate_select_only: Callable[[str, str], None],
    guard_error: type[Exception],
    minio_store: Any,
    logger: logging.Logger,
) -> dict[str, Any]:
    sql = payload["sql"]
    job_id = payload["job_id"]
    connection = dict(payload["connection"])
    backend = connection.pop("type")

    try:
        validate_select_only(sql, backend)
    except guard_error as exc:
        logger.warning(
            "execute_sql | rejected by sql_guard (backend=%s): %s",
            backend,
            exc,
        )
        return {
            "success": False,
            "error": f"SQL rejected: {exc}",
            "rejected_by_guard": True,
        }

    try:
        execute_readonly_query, backend_error = backend_loader(backend)
    except Exception as exc:
        return {
            "success": False,
            "error": f"SQL backend {backend!r} unavailable: {exc}",
        }

    minio_key = f"sql/{job_id}.sql"
    await asyncio.to_thread(minio_store.put_code, minio_key, sql)
    try:
        result = await execute_readonly_query(sql, connection)
    except backend_error as exc:
        return {"success": False, "error": str(exc)}
    finally:
        await asyncio.to_thread(minio_store.delete_code, minio_key)

    return {
        "success": True,
        "rows": result["rows"],
        "row_count": result["row_count"],
        "is_truncated": result["is_truncated"],
        "truncation_reason": result.get("truncation_reason", ""),
        "cells_truncated": result.get("cells_truncated", 0),
        "result_bytes": result.get("result_bytes", 0),
        "backend": backend,
    }

