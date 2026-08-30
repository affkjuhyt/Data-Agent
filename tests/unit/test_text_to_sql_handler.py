"""_text_to_sql_handler (backend/services/workflow-runtime/nodes/sandbox/tool_executor/handlers/data.py)
— DB Connector's core logic: schema introspection (cached through Redis), 1 LLM
call that both picks relevant DB(s) and writes SQL (or a simpler direct-SQL
prompt when there's only 1 connection), and parallel execution across DBs via
asyncio.gather.

Every database round-trip goes through sandbox-executor's /execute/sql — this
module opens no connection of its own. The tests stub `_sandbox_sql` (the single
seam where that HTTP call happens) rather than a database driver; a test that
needs to stub `asyncpg` here would mean the unguarded path had come back.

Split into 4 test classes: _sandbox_connection (wire-shape mapping),
_get_schema_text, _run_one_query, _text_to_sql_handler (the top-level
orchestrator, tested with the first three mocked out)."""
from __future__ import annotations

import json
import pathlib
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "workflow-runtime")

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


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


def _ok(rows, truncation=None):
    """_sandbox_sql success tuple: (rows, error, rejected_by_guard, truncation)."""
    return (rows, None, False, truncation)


def _err(message, *, rejected_by_guard=False):
    return (None, message, rejected_by_guard, None)


class TestSandboxConnection:
    """The canvas says `database`; sandbox-executor's Postgres params model says
    `dbname`. Getting this mapping wrong is a 422 at submit time, so pin it."""

    def test_postgres_maps_database_to_dbname_and_defaults_port(self):
        import nodes.sandbox.tool_executor.handlers.data as data

        out = data._sandbox_connection({"host": "h", "database": "d", "user": "u", "password": "p"})

        assert out == {"type": "postgres", "host": "h", "user": "u", "password": "p", "port": 5432, "dbname": "d"}

    def test_clickhouse_keeps_database_and_defaults_to_8123(self):
        import nodes.sandbox.tool_executor.handlers.data as data

        out = data._sandbox_connection({"type": "clickhouse", "host": "h", "database": "d", "user": "u", "password": "p"})

        assert out == {"type": "clickhouse", "host": "h", "user": "u", "password": "p", "port": 8123, "database": "d"}

    def test_explicit_port_is_preserved(self):
        import nodes.sandbox.tool_executor.handlers.data as data

        assert data._sandbox_connection({"host": "h", "database": "d", "port": 6543})["port"] == 6543

    def test_missing_password_becomes_empty_string_not_none(self):
        """sandbox-executor's params model types password as str — None is a 422."""
        import nodes.sandbox.tool_executor.handlers.data as data

        assert data._sandbox_connection({"host": "h", "database": "d", "password": None})["password"] == ""


class TestGetSchemaText:
    """Only the SQL-dialect difference lives here now.

    Cache shape, catalog preference, relevance ranking and the prompt-size caps
    moved to tests/unit/test_schema_context_selection.py when schema selection
    stopped being "render everything" — they are that module's subject, not this
    one's, and keeping duplicates in both drifts.
    """

    async def test_postgres_introspection_uses_public_schema_predicate(self, monkeypatch):
        import nodes.sandbox.tool_executor.handlers.data as data
        _stub_redis_cache(monkeypatch, get_return=None)
        sandbox = AsyncMock(return_value=_ok([]))
        monkeypatch.setattr(data, "_sandbox_sql", sandbox)

        await data._get_schema_text("t", {"host": "h", "database": "d"}, "q")

        # Phase 1 (table listing) is the first call; the dialect difference
        # lives in its predicate.
        sql = sandbox.await_args_list[0].args[0]
        assert "information_schema.tables" in sql
        assert "table_schema = 'public'" in sql

    async def test_clickhouse_introspection_uses_currentdatabase_predicate(self, monkeypatch):
        import nodes.sandbox.tool_executor.handlers.data as data
        _stub_redis_cache(monkeypatch, get_return=None)
        sandbox = AsyncMock(return_value=_ok([]))
        monkeypatch.setattr(data, "_sandbox_sql", sandbox)

        await data._get_schema_text("t", {"type": "clickhouse", "host": "h", "database": "d"}, "q")

        assert "currentDatabase()" in sandbox.await_args_list[0].args[0]


class TestRunOneQuery:
    async def test_success_on_first_attempt_records_sandbox_provenance(self, monkeypatch):
        import nodes.sandbox.tool_executor.handlers.data as data
        monkeypatch.setattr(data, "_sandbox_sql", AsyncMock(return_value=_ok([{"total": 100}])))

        result, error = await data._run_one_query(
            "Sales", "SELECT SUM(total) FROM orders", {"host": "h", "database": "d"},
            "doanh thu", "orders(total numeric)", "planner-model",
        )

        assert error is None
        assert result["metadata"]["db_name"] == "Sales"
        assert result["metadata"]["row_count"] == 1
        assert result["metadata"]["executed_via"] == "sandbox-executor"

    async def test_retries_and_succeeds_on_second_attempt(self, monkeypatch):
        import nodes.sandbox.tool_executor.handlers.data as data
        sandbox = AsyncMock(side_effect=[_err("query execution failed: syntax error"), _ok([{"total": 1}])])
        monkeypatch.setattr(data, "_sandbox_sql", sandbox)
        monkeypatch.setattr(data.litellm, "acompletion", AsyncMock(
            return_value=_llm_text("SELECT SUM(total) FROM orders_fixed"),
        ))

        result, error = await data._run_one_query(
            "Sales", "SELECT bad sql", {"host": "h", "database": "d"}, "q", "schema", "model",
        )

        assert error is None
        assert result is not None
        assert sandbox.await_count == 2
        # The regenerated SQL is what actually ran on the retry.
        assert sandbox.await_args.args[0] == "SELECT SUM(total) FROM orders_fixed"

    async def test_exhausts_retries_and_returns_error_with_db_name(self, monkeypatch):
        import nodes.sandbox.tool_executor.handlers.data as data
        sandbox = AsyncMock(return_value=_err("query execution failed: always fails"))
        monkeypatch.setattr(data, "_sandbox_sql", sandbox)
        monkeypatch.setattr(data.litellm, "acompletion", AsyncMock(return_value=_llm_text("SELECT 1")))

        result, error = await data._run_one_query(
            "Sales", "SELECT 1", {"host": "h", "database": "d"}, "q", "schema", "model",
        )

        assert result is None
        assert error["db_name"] == "Sales"
        assert "always fails" in error["error"]
        assert sandbox.await_count == 3

    async def test_guard_rejection_is_not_retried(self, monkeypatch):
        """A non-read query is a policy event, not a transient fault: re-prompting
        cannot legitimately fix it, so it must cost exactly one round-trip and no
        LLM call."""
        import nodes.sandbox.tool_executor.handlers.data as data
        sandbox = AsyncMock(return_value=_err(
            "SQL rejected: only SELECT (or WITH ... SELECT) statements are allowed, got: DROP",
            rejected_by_guard=True,
        ))
        monkeypatch.setattr(data, "_sandbox_sql", sandbox)
        completion = AsyncMock()
        monkeypatch.setattr(data.litellm, "acompletion", completion)

        result, error = await data._run_one_query(
            "Sales", "DROP TABLE orders", {"host": "h", "database": "d"}, "q", "schema", "model",
        )

        assert result is None
        assert sandbox.await_count == 1
        completion.assert_not_awaited()
        assert error["rejected_by_guard"] is True
        assert error["db_name"] == "Sales"
        assert "rejected by guard" in error["error"]

    async def test_empty_result_set_is_success_not_error(self, monkeypatch):
        """0 rows means the query ran — judge_phase1 deliberately leaves the
        "ran fine but returned nothing" case to the LLM judge (graph.py:93-100),
        so this must not be reported as a tool error."""
        import nodes.sandbox.tool_executor.handlers.data as data
        monkeypatch.setattr(data, "_sandbox_sql", AsyncMock(return_value=_ok([])))

        result, error = await data._run_one_query(
            "Sales", "SELECT 1 WHERE false", {"host": "h", "database": "d"}, "q", "schema", "model",
        )

        assert error is None
        assert result["metadata"]["row_count"] == 0
        assert result["content"] == "[]"


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
        completion_mock = AsyncMock(return_value=_llm_text("SELECT * FROM orders"))
        monkeypatch.setattr(data.litellm, "acompletion", completion_mock)
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
        monkeypatch.setattr(data, "_get_schema_text", AsyncMock(side_effect=lambda t, c, q, **kw: f"schema-for-{c['database']}"))
        monkeypatch.setattr(data.litellm, "acompletion", AsyncMock(return_value=_llm_text(json.dumps({
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
        monkeypatch.setattr(data.litellm, "acompletion", AsyncMock(return_value=_llm_text(json.dumps({
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
        monkeypatch.setattr(data.litellm, "acompletion", AsyncMock(return_value=_llm_text(json.dumps({
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


class TestNoDirectDatabaseAccess:
    """Regression guard for the defect this change fixes. If someone reopens a
    direct driver connection in this module, the SQL stops passing through
    sql_guard, the read-only role and the row cap — and every test above would
    still pass, because they stub the seam one level up."""

    def test_module_does_not_import_a_database_driver(self):
        """Checks real import statements via the AST, not raw text — the module
        docstring names asyncpg when explaining why it no longer uses it, and a
        substring scan would fail on that prose while missing a driver imported
        under an alias."""
        import ast
        import inspect

        import nodes.sandbox.tool_executor.handlers.data as data

        drivers = {"asyncpg", "clickhouse_connect", "clickhouse_connector", "psycopg", "psycopg2", "sqlalchemy"}
        imported: set[str] = set()
        for node in ast.walk(ast.parse(inspect.getsource(data))):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
                # `from libs.database_connector import clickhouse_connector`
                imported.update(alias.name.split(".")[0] for alias in node.names)

        offenders = imported & drivers
        assert not offenders, (
            f"handlers/data.py imports {sorted(offenders)} — agent-authored SQL must reach the "
            "database only through sandbox-executor's /execute/sql, where sql_guard, the "
            "read-only role and the row cap apply"
        )

    async def test_sandbox_sql_posts_to_execute_sql_with_tagged_connection(self, monkeypatch):
        """The one place the HTTP call is made: assert the path and that the
        backend type travels with the connection, so sandbox-executor dispatches
        to the right engine instead of falling back to its default."""
        import nodes.sandbox.tool_executor.handlers.data as data

        captured = {}

        async def fake_submit(http, path, body, overall_timeout_s):
            captured["path"] = path
            captured["body"] = body
            return {"success": True, "rows": [{"n": 1}], "row_count": 1, "is_truncated": False}

        monkeypatch.setattr(data, "submit_and_poll", fake_submit)

        rows, error, rejected, _trunc = await data._sandbox_sql(
            "SELECT 1", {"type": "clickhouse", "host": "h", "database": "d", "user": "u", "password": "p"},
        )

        assert (rows, error, rejected) == ([{"n": 1}], None, False)
        assert captured["path"] == "/execute/sql"
        assert captured["body"]["sql"] == "SELECT 1"
        assert captured["body"]["connection"]["type"] == "clickhouse"

    async def test_sandbox_sql_propagates_guard_rejection_flag(self, monkeypatch):
        import nodes.sandbox.tool_executor.handlers.data as data

        async def fake_submit(http, path, body, overall_timeout_s):
            return {"success": False, "error": "SQL rejected: DDL token not allowed: DROP", "rejected_by_guard": True}

        monkeypatch.setattr(data, "submit_and_poll", fake_submit)

        rows, error, rejected, _trunc = await data._sandbox_sql("DROP TABLE t", {"host": "h", "database": "d"})

        assert rows is None
        assert rejected is True
        assert "DDL token not allowed" in error

    async def test_sandbox_sql_reports_overload_without_marking_it_a_guard_rejection(self, monkeypatch):
        import nodes.sandbox.tool_executor.handlers.data as data

        async def fake_submit(http, path, body, overall_timeout_s):
            return {"error": "queue full", "_http_status": 503}

        monkeypatch.setattr(data, "submit_and_poll", fake_submit)

        rows, error, rejected, _trunc = await data._sandbox_sql("SELECT 1", {"host": "h", "database": "d"})

        assert rows is None
        assert rejected is False
        assert "overloaded" in error
