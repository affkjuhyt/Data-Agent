"""Per-request SQL backend dispatch in backend/services/sandbox-executor/main.py.

/execute/sql used to pin `connection` to ONE shape chosen at import time by
SANDBOX_SQL_BACKEND, so it could serve Snowflake or Postgres but never both.
That is why the DB Connector node bypassed the service entirely and ran
LLM-written SQL through raw asyncpg with no guard: it needs several per-tenant
connections, possibly of different engines, within one agent turn.

These tests pin the replacement contract:
  * `connection.type` selects the backend per request;
  * omitting it falls back to SANDBOX_SQL_BACKEND (so the existing execute_sql
    Snowflake tool keeps working unchanged);
  * a malformed connection is still rejected at validation time, before
    anything is queued;
  * the guard runs at the dispatcher and flags rejections distinguishably.

Pure unit level: main.py is loaded with heavy deps (docker, minio, drivers)
stubbed out, so nothing here needs Docker or a database.
"""
from __future__ import annotations

import pathlib
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = REPO_ROOT / "backend" / "services" / "sandbox-executor"

pytestmark = pytest.mark.unit

pytest.importorskip("fastapi", reason="fastapi not installed in this environment")
pytest.importorskip("sqlparse", reason="sqlparse not installed in this environment")


def _load_main(monkeypatch, backend: str = "snowflake"):
    """Import backend/services/sandbox-executor/main.py fresh, with its Docker/MinIO
    dependencies stubbed and SANDBOX_SQL_BACKEND set."""
    from tests.unit._loader import load_module_from_path, stub_module

    monkeypatch.setenv("SANDBOX_SQL_BACKEND", backend)

    stub_module(
        "minio_store",
        ensure_bucket=MagicMock(),
        put_code=MagicMock(),
        delete_code=MagicMock(),
    )
    stub_module(
        "docker_runner",
        run_python_sandboxed=MagicMock(),
        # /execute/shell's executor — workflow-runtime's bash tool runs here now
        # instead of subprocess.run(shell=True) in its own process. The stub has
        # to carry every name main.py imports, or the import fails before any
        # test in this file runs.
        run_shell_sandboxed=MagicMock(),
        ExecutionTimeoutError=type("ExecutionTimeoutError", (Exception,), {}),
        SandboxRunError=type("SandboxRunError", (Exception,), {}),
        SandboxCircuitOpenError=type("SandboxCircuitOpenError", (Exception,), {}),
        get_pool=MagicMock(),
        shutdown_pool=MagicMock(),
    )
    for name in ("main", "sql_guard", "output", "job_queue", "security_visitor"):
        sys.modules.pop(name, None)

    return load_module_from_path("main", _SVC_DIR / "main.py", extra_syspath=[_SVC_DIR])


def _install_backend(main, monkeypatch, name: str, *, execute=None, error_type=None):
    """Replace main._load_backend so no real driver is imported."""
    err = error_type or type("FakeBackendError", (Exception,), {})
    execute = execute or AsyncMock(return_value={"rows": [{"n": 1}], "row_count": 1, "is_truncated": False})
    monkeypatch.setattr(main, "_load_backend", lambda backend: (execute, err))
    return execute, err


class TestConnectionValidation:
    def test_explicit_type_selects_that_backends_shape(self, monkeypatch):
        main = _load_main(monkeypatch, backend="snowflake")

        req = main.ExecuteSQLRequest(
            sql="SELECT 1",
            connection={"type": "postgres", "host": "h", "user": "u", "password": "p", "dbname": "d"},
        )

        assert req.connection["type"] == "postgres"
        assert req.connection["dbname"] == "d"
        assert req.connection["port"] == 5432

    def test_missing_type_falls_back_to_env_default(self, monkeypatch):
        """Backward compatibility: code_interpreter.py's execute_sql tool posts a
        bare Snowflake connection with no `type` and must keep working."""
        main = _load_main(monkeypatch, backend="snowflake")

        req = main.ExecuteSQLRequest(
            sql="SELECT 1", connection={"account": "a", "user": "u", "password": "p"},
        )

        assert req.connection["type"] == "snowflake"
        assert req.connection["account"] == "a"

    def test_missing_type_under_postgres_default_takes_postgres_shape(self, monkeypatch):
        main = _load_main(monkeypatch, backend="postgres")

        req = main.ExecuteSQLRequest(
            sql="SELECT 1", connection={"host": "h", "user": "u", "password": "p", "dbname": "d"},
        )

        assert req.connection["type"] == "postgres"

    def test_clickhouse_shape_defaults_to_port_8123(self, monkeypatch):
        main = _load_main(monkeypatch, backend="snowflake")

        req = main.ExecuteSQLRequest(
            sql="SELECT 1",
            connection={"type": "clickhouse", "host": "h", "user": "u", "password": "p", "database": "d"},
        )

        assert req.connection["port"] == 8123

    def test_snowflake_schema_alias_survives_validation(self, monkeypatch):
        """`schema` shadows BaseModel.schema(), so the model stores it as
        schema_name — the wire key must still come back out as `schema`."""
        main = _load_main(monkeypatch, backend="snowflake")

        req = main.ExecuteSQLRequest(
            sql="SELECT 1",
            connection={"account": "a", "user": "u", "password": "p", "schema": "PUBLIC"},
        )

        assert req.connection["schema"] == "PUBLIC"

    def test_missing_required_field_is_a_validation_error(self, monkeypatch):
        """Same property the pinned-model version had: a malformed connection is
        rejected before anything is queued (FastAPI turns this into a 422)."""
        from pydantic import ValidationError

        main = _load_main(monkeypatch, backend="snowflake")

        with pytest.raises(ValidationError):
            main.ExecuteSQLRequest(sql="SELECT 1", connection={"user": "u", "password": "p"})

    def test_unknown_type_is_a_validation_error(self, monkeypatch):
        from pydantic import ValidationError

        main = _load_main(monkeypatch, backend="snowflake")

        with pytest.raises(ValidationError):
            main.ExecuteSQLRequest(sql="SELECT 1", connection={"type": "oracle", "host": "h"})

    def test_unknown_env_default_fails_at_import(self, monkeypatch):
        with pytest.raises(ValueError, match="unknown SANDBOX_SQL_BACKEND"):
            _load_main(monkeypatch, backend="mysql")


class TestRunExecuteSQL:
    async def test_dispatches_to_the_requested_backend_without_the_type_key(self, monkeypatch):
        """`type` is routing metadata, not a connection parameter — passing it
        through to asyncpg.connect()/get_client() would be a TypeError."""
        main = _load_main(monkeypatch, backend="snowflake")
        execute, _ = _install_backend(main, monkeypatch, "postgres")

        result = await main._run_execute_sql({
            "sql": "SELECT 1", "job_id": "j1",
            "connection": {"type": "postgres", "host": "h", "user": "u", "password": "p", "dbname": "d"},
        })

        assert result["success"] is True
        assert result["backend"] == "postgres"
        passed_connection = execute.await_args.args[1]
        assert "type" not in passed_connection
        assert passed_connection["dbname"] == "d"

    async def test_guard_rejects_ddl_before_reaching_the_backend(self, monkeypatch):
        main = _load_main(monkeypatch, backend="postgres")
        execute, _ = _install_backend(main, monkeypatch, "postgres")

        result = await main._run_execute_sql({
            "sql": "DROP TABLE orders", "job_id": "j1",
            "connection": {"type": "postgres", "host": "h", "user": "u", "password": "p", "dbname": "d"},
        })

        assert result["success"] is False
        assert result["rejected_by_guard"] is True
        execute.assert_not_awaited()

    async def test_guard_rejects_multi_statement_before_reaching_the_backend(self, monkeypatch):
        main = _load_main(monkeypatch, backend="postgres")
        execute, _ = _install_backend(main, monkeypatch, "postgres")

        result = await main._run_execute_sql({
            "sql": "SELECT 1; DROP TABLE orders", "job_id": "j1",
            "connection": {"type": "postgres", "host": "h", "user": "u", "password": "p", "dbname": "d"},
        })

        assert result["rejected_by_guard"] is True
        execute.assert_not_awaited()

    async def test_cte_select_is_allowed_through(self, monkeypatch):
        main = _load_main(monkeypatch, backend="postgres")
        execute, _ = _install_backend(main, monkeypatch, "postgres")

        result = await main._run_execute_sql({
            "sql": "WITH t AS (SELECT 1 AS n) SELECT n FROM t", "job_id": "j1",
            "connection": {"type": "postgres", "host": "h", "user": "u", "password": "p", "dbname": "d"},
        })

        assert result["success"] is True
        execute.assert_awaited_once()

    async def test_backend_error_is_reported_without_the_guard_flag(self, monkeypatch):
        """An execution failure is retryable by the caller; a guard rejection is
        not. They must stay distinguishable in the response."""
        main = _load_main(monkeypatch, backend="postgres")
        err = type("FakeBackendError", (Exception,), {})
        _install_backend(
            main, monkeypatch, "postgres",
            execute=AsyncMock(side_effect=err("query execution failed: no such column")),
            error_type=err,
        )

        result = await main._run_execute_sql({
            "sql": "SELECT nope FROM orders", "job_id": "j1",
            "connection": {"type": "postgres", "host": "h", "user": "u", "password": "p", "dbname": "d"},
        })

        assert result["success"] is False
        assert result.get("rejected_by_guard") is not True
        assert "no such column" in result["error"]

    async def test_missing_driver_fails_only_that_request(self, monkeypatch):
        """A backend whose driver isn't in the image must not take the service
        down — it fails the one request that needed it."""
        main = _load_main(monkeypatch, backend="postgres")

        def boom(backend):
            raise ImportError("No module named 'clickhouse_connect'")

        monkeypatch.setattr(main, "_load_backend", boom)

        result = await main._run_execute_sql({
            "sql": "SELECT 1", "job_id": "j1",
            "connection": {"type": "clickhouse", "host": "h", "user": "u", "password": "p", "database": "d"},
        })

        assert result["success"] is False
        assert "unavailable" in result["error"]
        assert "clickhouse_connect" in result["error"]
