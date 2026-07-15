"""ClickHouse connector — same shape as the other connectors: a stateful client
(set_conn/open/close/health_check_loop) plus operation methods that own the query
logic, and two one-shot module helpers (``query`` / ``columns``) for callers that
use an ephemeral connect-per-query style (e.g. the workflow-runtime ``db_connector``
node, which mirrors the asyncpg-per-query pattern).

Uses ClickHouse's native HTTP interface (default port 8123) via ``clickhouse_connect``
— NOT the PostgreSQL wire-compat port, so ClickHouse SQL dialect and types work
properly. ``clickhouse_connect`` is synchronous, so calls run in a worker thread
via ``asyncio.to_thread`` to stay non-blocking.

conn dict shape (same keys the db_connector node already passes, plus ``type``):
    {"type": "clickhouse", "host": ..., "port": 8123, "database": ...,
     "user": ..., "password": ...}
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from libs.basemodel.services_databaseconnector.shared_model import HealthCheckLoopConfig, RetryConfig

log = logging.getLogger(__name__)

_COLUMNS_SQL = (
    "SELECT table_name, column_name, data_type FROM information_schema.columns "
    "WHERE table_schema = currentDatabase() ORDER BY table_name, ordinal_position"
)


def _new_client(conn: dict):
    """Build a synchronous clickhouse_connect client from a conn dict."""
    import clickhouse_connect  # lazy: keep the dep optional until CH is actually used

    return clickhouse_connect.get_client(
        host=conn.get("host", "") or "localhost",
        port=int(conn.get("port") or 8123),
        username=conn.get("user", "") or "default",
        password=conn.get("password", "") or "",
        database=conn.get("database", "") or "default",
    )


def _rows_as_dicts(result) -> list[dict[str, Any]]:
    cols = result.column_names
    return [dict(zip(cols, row, strict=False)) for row in result.result_rows]


# ── one-shot helpers (ephemeral connect-per-query) ──────────────────────────
async def query(conn: dict, sql: str) -> list[dict[str, Any]]:
    """Run one SQL against ClickHouse and return rows as dicts. Connect + close
    per call, mirroring the asyncpg usage in the db_connector node."""
    def _run() -> list[dict[str, Any]]:
        client = _new_client(conn)
        try:
            return _rows_as_dicts(client.query(sql))
        finally:
            client.close()

    return await asyncio.to_thread(_run)


async def columns(conn: dict) -> list[dict[str, Any]]:
    """Introspect columns of the current database as
    ``[{table_name, column_name, data_type}, ...]`` (same shape the Postgres path
    produces from information_schema)."""
    return await query(conn, _COLUMNS_SQL)


# ── stateful client (repo connector pattern) ────────────────────────────────
class ClickHouseClient:
    __slots__ = ("_client", "_conn", "_connected", "_healthy")

    def __init__(self) -> None:
        self._client = None
        self._conn: dict | None = None
        self._healthy: bool = False
        self._connected: bool = False

    def set_conn(self, conn: dict) -> None:
        self._conn = conn

    async def open(self, retry: RetryConfig | None = RetryConfig()) -> ClickHouseClient:
        if self._conn is None:
            raise RuntimeError("ClickHouse conn is not set — call set_conn() first")
        retry = retry or RetryConfig()
        for attempt in range(retry.count):
            try:
                self._client = await asyncio.to_thread(_new_client, self._conn)
                await asyncio.to_thread(self._client.ping)
                self._connected = True
                self._healthy = True
                log.info("ClickHouse connection established host=%s", self._conn.get("host"))
                return self
            except Exception as e:
                log.info("Attempt #%d/%d: ClickHouse connection failed — %s", attempt + 1, retry.count, e)
                await self.close()
                await asyncio.sleep(5)
        raise ConnectionError(f"Could not connect to ClickHouse after {retry.count} attempts")

    async def close(self) -> None:
        if self._client is not None:
            try:
                await asyncio.to_thread(self._client.close)
            finally:
                self._client = None
        self._connected = False
        self._healthy = False

    async def health_check_loop(self, config: HealthCheckLoopConfig | None = HealthCheckLoopConfig()) -> None:
        config = config or HealthCheckLoopConfig()
        log.info("ClickHouse health check loop started")
        while True:
            try:
                await asyncio.wait_for(asyncio.to_thread(self._db().ping), timeout=config.timeout_for_health_check)
                self._healthy = True
            except Exception as e:
                self._healthy = False
                log.warning("ClickHouse unhealthy: %s — attempting reconnect", e)
                await self._reconnect()
            await asyncio.sleep(config.interval)

    async def _reconnect(self) -> None:
        await self.close()
        try:
            await self.open(RetryConfig(count=1))
        except ConnectionError as e:
            log.warning("ClickHouse reconnect failed: %s", e)

    def is_healthy(self) -> bool:
        return self._healthy

    def is_connected(self) -> bool:
        return self._connected

    def _db(self):
        if self._client is None:
            raise RuntimeError("ClickHouseClient is not open — call open() first")
        return self._client

    async def run_query(self, sql: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(lambda: _rows_as_dicts(self._db().query(sql)))

    async def list_columns(self) -> list[dict[str, Any]]:
        return await self.run_query(_COLUMNS_SQL)


client = ClickHouseClient()
