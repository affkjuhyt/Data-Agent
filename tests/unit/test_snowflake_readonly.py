"""execute_readonly_query connection handling (sandbox_schema_docs.docx §1.5).

Review context: the original implementation opened a brand-new
snowflake.connector.connect() per call and closed it in a `finally` —
Snowflake auth takes ~1-2s, so an interactive agent calling execute_sql
repeatedly paid that latency on every single tool call. snowflake.connector
itself is never hit here — `snowflake.connector.connect` is monkeypatched to
a recorder so these tests run without network/real credentials, same spirit
as test_sql_guard.py mocking nothing because sqlparse is pure-Python, and
test_schema_crawler_llm_embed.py using respx instead of a real LLM.
"""
from __future__ import annotations

import pathlib
import sys
from unittest.mock import MagicMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "services" / "sandbox-executor")


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    sys.modules.pop("snowflake_readonly", None)
    yield
    sys.modules.pop("snowflake_readonly", None)
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


def _params(**overrides) -> dict:
    base = {
        "account": "acct1", "user": "u1", "password": "pw1",
        "warehouse": "wh", "database": "db", "schema": "sch", "role": "role",
    }
    base.update(overrides)
    return base


def _fake_connection(rows=None):
    """A MagicMock standing in for a snowflake.connector.SnowflakeConnection."""
    conn = MagicMock()
    conn.is_closed.return_value = False
    cursor = MagicMock()
    cursor.fetchall.return_value = rows or []
    conn.cursor.return_value = cursor
    return conn


@pytest.mark.unit
async def test_connection_is_reused_across_calls(monkeypatch):
    import snowflake_readonly as sr

    conn = _fake_connection(rows=[{"a": 1}])
    connect_spy = MagicMock(return_value=conn)
    monkeypatch.setattr(sr.snowflake.connector, "connect", connect_spy)

    params = _params()
    await sr.execute_readonly_query("SELECT a FROM t", params)
    await sr.execute_readonly_query("SELECT a FROM t", params)

    connect_spy.assert_called_once()


@pytest.mark.unit
async def test_different_connection_params_get_separate_connections(monkeypatch):
    import snowflake_readonly as sr

    conns = [_fake_connection(), _fake_connection()]
    connect_spy = MagicMock(side_effect=conns)
    monkeypatch.setattr(sr.snowflake.connector, "connect", connect_spy)

    await sr.execute_readonly_query("SELECT a FROM t", _params(account="acct1"))
    await sr.execute_readonly_query("SELECT a FROM t", _params(account="acct2"))

    assert connect_spy.call_count == 2


@pytest.mark.unit
async def test_closed_cached_connection_is_replaced(monkeypatch):
    import snowflake_readonly as sr

    stale = _fake_connection()
    fresh = _fake_connection()
    connect_spy = MagicMock(side_effect=[stale, fresh])
    monkeypatch.setattr(sr.snowflake.connector, "connect", connect_spy)

    params = _params()
    await sr.execute_readonly_query("SELECT a FROM t", params)
    stale.is_closed.return_value = True
    await sr.execute_readonly_query("SELECT a FROM t", params)

    assert connect_spy.call_count == 2


@pytest.mark.unit
async def test_query_failure_drops_connection_from_pool_and_reraises(monkeypatch):
    import snowflake_readonly as sr

    broken = _fake_connection()
    broken.cursor.return_value.execute.side_effect = RuntimeError("connection reset")
    healthy = _fake_connection()
    connect_spy = MagicMock(side_effect=[broken, healthy])
    monkeypatch.setattr(sr.snowflake.connector, "connect", connect_spy)

    params = _params()
    with pytest.raises(sr.SnowflakeQueryError):
        await sr.execute_readonly_query("SELECT a FROM t", params)

    assert sr._connection_key(params) not in sr._connections

    await sr.execute_readonly_query("SELECT a FROM t", params)
    assert connect_spy.call_count == 2


@pytest.mark.unit
async def test_wrapped_query_puts_inner_sql_on_its_own_line(monkeypatch):
    """Defense in depth alongside sql_guard's comment rejection: the LIMIT
    clause must not share a physical line with agent-authored SQL, so even
    an undetected trailing "--" comment couldn't swallow it."""
    import snowflake_readonly as sr

    conn = _fake_connection()
    monkeypatch.setattr(sr.snowflake.connector, "connect", MagicMock(return_value=conn))

    await sr.execute_readonly_query("SELECT a FROM t", _params())

    executed_sql = conn.cursor.return_value.execute.call_args[0][0]
    lines = executed_sql.splitlines()
    assert "LIMIT" in lines[-1]
    assert lines[-1].strip().startswith(")")


@pytest.mark.unit
async def test_sql_guard_rejection_never_opens_a_connection(monkeypatch):
    import snowflake_readonly as sr

    connect_spy = MagicMock()
    monkeypatch.setattr(sr.snowflake.connector, "connect", connect_spy)

    with pytest.raises(sr.SnowflakeQueryError):
        await sr.execute_readonly_query("SELECT * FROM t -- comment", _params())

    connect_spy.assert_not_called()
