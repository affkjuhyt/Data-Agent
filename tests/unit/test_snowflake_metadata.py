"""schema-crawler-worker's snowflake_metadata.py — connect timeouts, batched
fetch, chunked IN-clause, idle-connection eviction (see
docs/Schema_Crawler_Risk_Remediation_Plan.md #3, #4, #5, #6).

snowflake.connector.connect is monkeypatched throughout — no real Snowflake
account is contacted, same technique as
tests/unit/test_snowflake_readonly.py for sandbox-executor's analogous pool.
"""
from __future__ import annotations

import pathlib
import sys
import time
from unittest.mock import MagicMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "services" / "schema-crawler-worker")


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    sys.modules.pop("snowflake_metadata", None)
    yield
    sys.modules.pop("snowflake_metadata", None)
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


def _fake_connection(fetchmany_batches: "list[list] | None" = None):
    """MagicMock standing in for a snowflake.connector.SnowflakeConnection.
    fetchmany_batches is consumed in order across every fetchmany() call on
    the cursor (regardless of which query issued it) — tests that don't care
    about row content just pass batches of empty lists."""
    conn = MagicMock()
    conn.is_closed.return_value = False
    cursor = MagicMock()
    cursor.fetchmany.side_effect = list(fetchmany_batches or []) + [[]] * 50
    conn.cursor.return_value = cursor
    return conn


@pytest.mark.unit
def test_connect_sets_login_network_and_statement_timeouts(monkeypatch):
    import snowflake_metadata as sm

    conn = _fake_connection()
    connect_spy = MagicMock(return_value=conn)
    monkeypatch.setattr(sm.snowflake.connector, "connect", connect_spy)

    sm.discover_and_describe(_params(), selected_tables=[])

    _, kwargs = connect_spy.call_args
    assert kwargs["login_timeout"] == sm.LOGIN_TIMEOUT_SECONDS
    assert kwargs["network_timeout"] == sm.NETWORK_TIMEOUT_SECONDS
    assert kwargs["session_parameters"] == {"STATEMENT_TIMEOUT_IN_SECONDS": sm.STATEMENT_TIMEOUT_SECONDS}


@pytest.mark.unit
def test_discover_and_describe_merges_multiple_fetchmany_batches(monkeypatch):
    import snowflake_metadata as sm

    monkeypatch.setattr(sm, "FETCH_BATCH_SIZE", 1)  # force multiple batches for 2 rows
    table_rows = [
        {"TABLE_NAME": "t1", "ROW_COUNT": 10, "LAST_ALTERED": None},
        {"TABLE_NAME": "t2", "ROW_COUNT": 20, "LAST_ALTERED": None},
    ]
    column_rows = [
        {"TABLE_NAME": "t1", "COLUMN_NAME": "id", "DATA_TYPE": "NUMBER", "IS_NULLABLE": "NO",
         "COMMENT": None, "ORDINAL_POSITION": 1},
        {"TABLE_NAME": "t2", "COLUMN_NAME": "id", "DATA_TYPE": "NUMBER", "IS_NULLABLE": "NO",
         "COMMENT": None, "ORDINAL_POSITION": 1},
    ]
    # fetchmany(1) called repeatedly: table_rows one at a time, then an empty
    # batch to terminate that query's loop, then column_rows one at a time
    # (the fixture appends its own empty-list terminators at the end).
    batches = [[r] for r in table_rows] + [[]] + [[r] for r in column_rows]
    conn = _fake_connection(fetchmany_batches=batches)
    monkeypatch.setattr(sm.snowflake.connector, "connect", MagicMock(return_value=conn))

    result = sm.discover_and_describe(_params())

    assert set(result.keys()) == {"t1", "t2"}
    assert result["t1"]["row_count"] == 10
    assert result["t2"]["row_count"] == 20


@pytest.mark.unit
def test_selected_tables_chunks_in_clause(monkeypatch):
    import snowflake_metadata as sm

    monkeypatch.setattr(sm, "IN_CLAUSE_CHUNK_SIZE", 2)
    conn = _fake_connection()
    monkeypatch.setattr(sm.snowflake.connector, "connect", MagicMock(return_value=conn))

    sm.discover_and_describe(_params(), selected_tables=["a", "b", "c", "d", "e"])

    # 1 execute for the TABLES query + ceil(5/2)=3 executes for COLUMNS chunks.
    assert conn.cursor.return_value.execute.call_count == 1 + 3


@pytest.mark.unit
def test_selected_tables_empty_list_skips_columns_query(monkeypatch):
    import snowflake_metadata as sm

    conn = _fake_connection()
    monkeypatch.setattr(sm.snowflake.connector, "connect", MagicMock(return_value=conn))

    result = sm.discover_and_describe(_params(), selected_tables=[])

    assert result == {}
    assert conn.cursor.return_value.execute.call_count == 1  # only the TABLES query


@pytest.mark.unit
def test_evict_idle_connections_closes_only_stale_entries(monkeypatch):
    import snowflake_metadata as sm

    fresh_conn = MagicMock()
    stale_conn = MagicMock()
    now = time.monotonic()
    sm._connections[("acct-fresh",)] = (fresh_conn, now)
    sm._connections[("acct-stale",)] = (stale_conn, now - 1000)

    evicted = sm.evict_idle_connections(max_idle_seconds=500)

    assert evicted == 1
    assert ("acct-stale",) not in sm._connections
    assert ("acct-fresh",) in sm._connections
    stale_conn.close.assert_called_once()
    fresh_conn.close.assert_not_called()
