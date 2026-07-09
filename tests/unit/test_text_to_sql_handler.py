"""_text_to_sql_handler (services/workflow-runtime/nodes/sandbox/tool_executor/handlers/data.py)
— DB Connector's core logic: auto schema introspection (via information_schema,
cached through Redis), 1 LLM call that both picks relevant DB(s) and writes
SQL (or a simpler direct-SQL prompt when there's only 1 connection), and
parallel execution across DBs via asyncio.gather. Split into 3 test classes
matching the 3 functions: _get_schema_text, _run_one_query, _text_to_sql_handler
(the top-level orchestrator, tested with the first two mocked out)."""
from __future__ import annotations

import json
import pathlib
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "services" / "workflow-runtime")

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


def _stub_asyncpg(monkeypatch, connect_mock) -> None:
    fake_module = types.ModuleType("asyncpg")
    fake_module.connect = connect_mock
    monkeypatch.setitem(sys.modules, "asyncpg", fake_module)


def _fake_conn(fetch_return=None, fetch_side_effect=None) -> MagicMock:
    conn = MagicMock()
    conn.close = AsyncMock()
    if fetch_side_effect is not None:
        conn.fetch = AsyncMock(side_effect=fetch_side_effect)
    else:
        conn.fetch = AsyncMock(return_value=fetch_return or [])
    return conn


def _stub_redis_cache(monkeypatch, get_return=None) -> types.ModuleType:
    fake_module = types.ModuleType("redis_cache")
    fake_module.cache_get_schema = AsyncMock(return_value=get_return)
    fake_module.cache_set_schema = AsyncMock(return_value=None)
    monkeypatch.setitem(sys.modules, "redis_cache", fake_module)
    return fake_module


def _llm_text(content: str) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content))]
    return resp


class TestGetSchemaText:
    async def test_cache_hit_skips_db_introspection(self, monkeypatch):
        import nodes.sandbox.tool_executor.handlers.data as data
        _stub_redis_cache(monkeypatch, get_return="orders(id int)")
        connect_mock = AsyncMock()
        _stub_asyncpg(monkeypatch, connect_mock)

        result = await data._get_schema_text(
            "tenant-1", {"host": "h", "port": 5432, "database": "d", "user": "u", "password": "p"},
        )

        assert result == "orders(id int)"
        connect_mock.assert_not_called()

    async def test_cache_miss_introspects_and_caches(self, monkeypatch):
        import nodes.sandbox.tool_executor.handlers.data as data
        fake_redis = _stub_redis_cache(monkeypatch, get_return=None)
        rows = [
            {"table_name": "orders", "column_name": "id", "data_type": "integer"},
            {"table_name": "orders", "column_name": "total", "data_type": "numeric"},
            {"table_name": "customers", "column_name": "id", "data_type": "integer"},
        ]
        conn = _fake_conn(fetch_return=rows)
        _stub_asyncpg(monkeypatch, AsyncMock(return_value=conn))

        result = await data._get_schema_text(
            "tenant-1", {"host": "h", "port": 5432, "database": "d", "user": "u", "password": "p"},
        )

        assert result == "orders(id integer, total numeric)\ncustomers(id integer)"
        conn.close.assert_awaited_once()
        fake_redis.cache_set_schema.assert_awaited_once_with("tenant-1", "h", 5432, "d", result)

    async def test_introspection_failure_returns_empty_string_not_raise(self, monkeypatch):
        import nodes.sandbox.tool_executor.handlers.data as data
        _stub_redis_cache(monkeypatch, get_return=None)
        _stub_asyncpg(monkeypatch, AsyncMock(side_effect=RuntimeError("connection refused")))

        result = await data._get_schema_text("tenant-1", {"host": "h", "database": "d"})

        assert result == ""


class TestRunOneQuery:
    async def test_success_on_first_attempt(self, monkeypatch):
        import nodes.sandbox.tool_executor.handlers.data as data
        conn = _fake_conn(fetch_return=[{"total": 100}])
        _stub_asyncpg(monkeypatch, AsyncMock(return_value=conn))

        result, error = await data._run_one_query(
            "Sales", "SELECT SUM(total) FROM orders", {"host": "h", "database": "d"},
            "doanh thu", "orders(total numeric)", "planner-model",
        )

        assert error is None
        assert result["metadata"]["db_name"] == "Sales"
        assert result["metadata"]["row_count"] == 1
        conn.close.assert_awaited_once()

    async def test_retries_and_succeeds_on_second_attempt(self, monkeypatch):
        import nodes.sandbox.tool_executor.handlers.data as data
        conn = _fake_conn(fetch_side_effect=[RuntimeError("syntax error"), [{"total": 1}]])
        _stub_asyncpg(monkeypatch, AsyncMock(return_value=conn))
        monkeypatch.setattr(data.litellm, "completion", MagicMock(
            return_value=_llm_text("SELECT SUM(total) FROM orders_fixed"),
        ))

        result, error = await data._run_one_query(
            "Sales", "SELECT bad sql", {"host": "h", "database": "d"}, "q", "schema", "model",
        )

        assert error is None
        assert result is not None
        assert conn.fetch.await_count == 2

    async def test_exhausts_retries_and_returns_error_with_db_name(self, monkeypatch):
        import nodes.sandbox.tool_executor.handlers.data as data
        conn = _fake_conn(fetch_side_effect=RuntimeError("always fails"))
        _stub_asyncpg(monkeypatch, AsyncMock(return_value=conn))
        monkeypatch.setattr(data.litellm, "completion", MagicMock(return_value=_llm_text("SELECT 1")))

        result, error = await data._run_one_query(
            "Sales", "SELECT 1", {"host": "h", "database": "d"}, "q", "schema", "model",
        )

        assert result is None
        assert error["db_name"] == "Sales"
        assert "always fails" in error["error"]
        assert conn.fetch.await_count == 3


class TestTextToSqlHandler:
    async def test_no_connections_configured(self):
        import nodes.sandbox.tool_executor.handlers.data as data

        results, errors = await data._text_to_sql_handler({"query": "q"}, {"tool_config": {}})

        assert results == []
        assert errors[0]["error"] == "no database connection configured"

    async def test_no_query_provided(self):
        import nodes.sandbox.tool_executor.handlers.data as data
        cfg = {"tool_config": {"db_connections": {"Sales": {"host": "h", "database": "d"}}}}

        results, errors = await data._text_to_sql_handler({}, cfg)

        assert results == []
        assert "no query" in errors[0]["error"]

    async def test_single_connection_skips_db_selection_step(self, monkeypatch):
        """1 kết nối → prompt sinh SQL trực tiếp, KHÔNG dùng response_format
        json_object (đó chỉ dành cho case nhiều kết nối) — tránh phí 1 tầng
        suy luận thừa cho case phổ biến nhất."""
        import nodes.sandbox.tool_executor.handlers.data as data
        monkeypatch.setattr(data, "_get_schema_text", AsyncMock(return_value="orders(id int)"))
        completion_mock = MagicMock(return_value=_llm_text("SELECT * FROM orders"))
        monkeypatch.setattr(data.litellm, "completion", completion_mock)
        monkeypatch.setattr(data, "_run_one_query", AsyncMock(
            return_value=({"id": "sql_1", "metadata": {"db_name": "Sales"}}, None),
        ))

        cfg = {"tool_config": {"db_connections": {"Sales": {"host": "h", "database": "d"}}}}
        results, errors = await data._text_to_sql_handler({"query": "doanh thu"}, cfg)

        assert results == [{"id": "sql_1", "metadata": {"db_name": "Sales"}}]
        assert errors == []
        _, kwargs = completion_mock.call_args
        assert "response_format" not in kwargs

    async def test_multi_connection_llm_selects_subset_runs_in_parallel(self, monkeypatch):
        import nodes.sandbox.tool_executor.handlers.data as data
        monkeypatch.setattr(data, "_get_schema_text", AsyncMock(side_effect=lambda t, c: f"schema-for-{c['database']}"))
        monkeypatch.setattr(data.litellm, "completion", MagicMock(return_value=_llm_text(json.dumps({
            "queries": [
                {"db_name": "Sales", "sql": "SELECT 1"},
                {"db_name": "Warehouse", "sql": "SELECT 2"},
            ],
        }))))
        run_query_mock = AsyncMock(side_effect=[
            ({"id": "sql_1", "metadata": {"db_name": "Sales"}}, None),
            ({"id": "sql_2", "metadata": {"db_name": "Warehouse"}}, None),
        ])
        monkeypatch.setattr(data, "_run_one_query", run_query_mock)

        cfg = {"tool_config": {"db_connections": {
            "Sales":     {"host": "h1", "database": "d1"},
            "Warehouse": {"host": "h2", "database": "d2"},
            "HR":        {"host": "h3", "database": "d3"},
        }}}
        results, errors = await data._text_to_sql_handler({"query": "doanh thu 2 kho"}, cfg)

        assert len(results) == 2
        assert {r["metadata"]["db_name"] for r in results} == {"Sales", "Warehouse"}
        assert errors == []
        assert run_query_mock.call_count == 2

    async def test_llm_chooses_unknown_db_name_is_skipped_not_crashed(self, monkeypatch):
        import nodes.sandbox.tool_executor.handlers.data as data
        monkeypatch.setattr(data, "_get_schema_text", AsyncMock(return_value="schema"))
        monkeypatch.setattr(data.litellm, "completion", MagicMock(return_value=_llm_text(json.dumps({
            "queries": [{"db_name": "DoesNotExist", "sql": "SELECT 1"}],
        }))))
        run_query_mock = AsyncMock()
        monkeypatch.setattr(data, "_run_one_query", run_query_mock)

        cfg = {"tool_config": {"db_connections": {
            "Sales": {"host": "h", "database": "d"}, "Warehouse": {"host": "h2", "database": "d2"},
        }}}
        results, errors = await data._text_to_sql_handler({"query": "q"}, cfg)

        run_query_mock.assert_not_called()
        assert results == []
        assert "did not select" in errors[0]["error"]

    async def test_partial_failure_among_multiple_dbs_still_returns_successes(self, monkeypatch):
        import nodes.sandbox.tool_executor.handlers.data as data
        monkeypatch.setattr(data, "_get_schema_text", AsyncMock(return_value="schema"))
        monkeypatch.setattr(data.litellm, "completion", MagicMock(return_value=_llm_text(json.dumps({
            "queries": [
                {"db_name": "Sales", "sql": "SELECT 1"},
                {"db_name": "Warehouse", "sql": "SELECT 2"},
            ],
        }))))
        run_query_mock = AsyncMock(side_effect=[
            ({"id": "sql_1", "metadata": {"db_name": "Sales"}}, None),
            (None, {"tool_name": "text_to_sql", "db_name": "Warehouse", "error": "connection refused"}),
        ])
        monkeypatch.setattr(data, "_run_one_query", run_query_mock)

        cfg = {"tool_config": {"db_connections": {
            "Sales": {"host": "h", "database": "d"}, "Warehouse": {"host": "h2", "database": "d2"},
        }}}
        results, errors = await data._text_to_sql_handler({"query": "q"}, cfg)

        assert len(results) == 1
        assert results[0]["metadata"]["db_name"] == "Sales"
        assert len(errors) == 1
        assert errors[0]["db_name"] == "Warehouse"
