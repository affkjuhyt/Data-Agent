"""execute_readonly_query connection handling for postgres_readonly.py — the
SANDBOX_SQL_BACKEND=postgres stand-in for snowflake_readonly.py
(docs/sandbox_report.md).

Mirrors test_snowflake_readonly.py: asyncpg.connect is monkeypatched to a
recorder so these tests run without a real Postgres instance/network, same
spirit as test_snowflake_readonly.py mocking snowflake.connector.connect.
"""
from __future__ import annotations

import pathlib
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "sandbox_executor")


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    sys.modules.pop("postgres_readonly", None)
    yield
    sys.modules.pop("postgres_readonly", None)
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


def _params(**overrides) -> dict:
    base = {
        "host": "localhost", "port": 5432, "user": "u1", "password": "pw1", "dbname": "db1",
    }
    base.update(overrides)
    return base


class _FakeTransaction:
    """asyncpg's Transaction — an async context manager, which a bare MagicMock
    is not. execute_readonly_query opens one (READ ONLY) around every query."""

    async def __aenter__(self) -> "_FakeTransaction":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _fake_connection(records=None, fetch_error=None):
    """A stand-in for an asyncpg.Connection on the streaming read-only path.

    Two things beyond a bare mock, because the code under test needs both:
    `transaction(readonly=True)` must work as an async context manager, and
    `cursor(sql)` must be awaitable and hand back batches — the query no longer
    goes through conn.fetch().
    """
    conn = MagicMock()
    conn.is_closed.return_value = False
    conn.close = AsyncMock()
    conn.transaction = MagicMock(return_value=_FakeTransaction())

    batches = [list(records or [])]

    async def _fetch(n):
        if fetch_error is not None:
            raise fetch_error
        return batches.pop(0) if batches else []

    cursor = MagicMock()
    cursor.fetch = AsyncMock(side_effect=_fetch)

    async def _open_cursor(sql, *args, **kwargs):
        return cursor

    conn.cursor = MagicMock(side_effect=_open_cursor)
    return conn


@pytest.mark.unit
async def test_connection_is_reused_across_calls(monkeypatch):
    import postgres_readonly as pr

    conn = _fake_connection(records=[{"a": 1}])
    connect_spy = AsyncMock(return_value=conn)
    monkeypatch.setattr(pr.asyncpg, "connect", connect_spy)

    params = _params()
    await pr.execute_readonly_query("SELECT a FROM t", params)
    await pr.execute_readonly_query("SELECT a FROM t", params)

    connect_spy.assert_awaited_once()


@pytest.mark.unit
async def test_different_connection_params_get_separate_connections(monkeypatch):
    import postgres_readonly as pr

    conns = [_fake_connection(), _fake_connection()]
    connect_spy = AsyncMock(side_effect=conns)
    monkeypatch.setattr(pr.asyncpg, "connect", connect_spy)

    await pr.execute_readonly_query("SELECT a FROM t", _params(host="h1"))
    await pr.execute_readonly_query("SELECT a FROM t", _params(host="h2"))

    assert connect_spy.await_count == 2


@pytest.mark.unit
async def test_closed_cached_connection_is_replaced(monkeypatch):
    import postgres_readonly as pr

    stale = _fake_connection()
    fresh = _fake_connection()
    connect_spy = AsyncMock(side_effect=[stale, fresh])
    monkeypatch.setattr(pr.asyncpg, "connect", connect_spy)

    params = _params()
    await pr.execute_readonly_query("SELECT a FROM t", params)
    stale.is_closed.return_value = True
    await pr.execute_readonly_query("SELECT a FROM t", params)

    assert connect_spy.await_count == 2


@pytest.mark.unit
async def test_query_failure_drops_connection_from_pool_and_reraises(monkeypatch):
    import postgres_readonly as pr

    broken = _fake_connection(fetch_error=RuntimeError("connection reset"))
    healthy = _fake_connection()
    connect_spy = AsyncMock(side_effect=[broken, healthy])
    monkeypatch.setattr(pr.asyncpg, "connect", connect_spy)

    params = _params()
    with pytest.raises(pr.PostgresQueryError):
        await pr.execute_readonly_query("SELECT a FROM t", params)

    assert pr._connection_key(params) not in pr._connections

    await pr.execute_readonly_query("SELECT a FROM t", params)
    assert connect_spy.await_count == 2


@pytest.mark.unit
async def test_wrapped_query_puts_inner_sql_on_its_own_line(monkeypatch):
    """Defense in depth alongside sql_guard's comment rejection: the LIMIT
    clause must not share a physical line with agent-authored SQL, so even
    an undetected trailing "--" comment couldn't swallow it."""
    import postgres_readonly as pr

    conn = _fake_connection()
    monkeypatch.setattr(pr.asyncpg, "connect", AsyncMock(return_value=conn))

    await pr.execute_readonly_query("SELECT a FROM t", _params())

    executed_sql = conn.cursor.call_args[0][0]
    lines = executed_sql.splitlines()
    assert "LIMIT" in lines[-1]
    assert lines[-1].strip().startswith(")")


@pytest.mark.unit
async def test_sql_guard_rejection_never_opens_a_connection(monkeypatch):
    import postgres_readonly as pr

    connect_spy = AsyncMock()
    monkeypatch.setattr(pr.asyncpg, "connect", connect_spy)

    with pytest.raises(pr.PostgresQueryError):
        await pr.execute_readonly_query("SELECT * FROM t -- comment", _params())

    connect_spy.assert_not_awaited()


@pytest.mark.unit
async def test_rows_are_converted_from_records_to_plain_dicts(monkeypatch):
    """asyncpg.Record isn't a dict — callers (main.py's JSON response) need
    plain dicts, mirroring DictCursor.fetchall()'s shape on the Snowflake
    side."""
    import postgres_readonly as pr

    conn = _fake_connection(records=[{"a": 1, "b": "x"}, {"a": 2, "b": "y"}])
    monkeypatch.setattr(pr.asyncpg, "connect", AsyncMock(return_value=conn))

    result = await pr.execute_readonly_query("SELECT a, b FROM t", _params())

    assert result["rows"] == [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]
    assert result["row_count"] == 2
    assert result["is_truncated"] is False
