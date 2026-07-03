"""Integration test for workflow-runtime's execute_python/execute_sql tool
handlers (nodes/sandbox/tool_executor/handlers/code_interpreter.py) talking
over real HTTP to a *live* sandbox-executor instance — closes the gap that
test_sandbox_executor_api.py only drove sandbox-executor's own FastAPI app
directly, never the workflow-runtime side that calls it.

sandbox-executor's own app runs in-process via uvicorn on a real localhost
port (no need to build/run its own Docker image for this — its code still
uses the real Docker daemon to spawn real sandbox-runner containers
underneath, exactly as it does in production). Requires Docker + the
sandbox-runner:latest image; skipped otherwise.
"""
from __future__ import annotations

import pathlib
import shutil
import sys
import threading
import time

import docker
import pytest
import uvicorn

pytestmark = pytest.mark.integration

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SANDBOX_DIR = str(REPO_ROOT / "services" / "sandbox-executor")
_RUNTIME_DIR = str(REPO_ROOT / "services" / "workflow-runtime")
_PORT = 8599


def _docker_and_image_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        client = docker.from_env()
        client.images.get("sandbox-runner:latest")
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _docker_and_image_available(), reason="Docker daemon or sandbox-runner:latest image not available"),
]


@pytest.fixture(scope="module")
def live_sandbox_executor():
    """Runs sandbox-executor's real FastAPI app on a real port in a
    background thread for the duration of this test module."""
    added = [p for p in (_SANDBOX_DIR,) if p not in sys.path]
    sys.path[:0] = added
    try:
        sys.modules.pop("main", None)
        import main as sandbox_main

        config = uvicorn.Config(sandbox_main.app, host="127.0.0.1", port=_PORT, log_level="warning")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        for _ in range(50):
            if server.started:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("sandbox-executor did not start in time")

        yield f"http://127.0.0.1:{_PORT}"

        server.should_exit = True
        thread.join(timeout=5)
    finally:
        sys.modules.pop("main", None)
        for p in added:
            if p in sys.path:
                sys.path.remove(p)


@pytest.fixture
def handler_mod(live_sandbox_executor, monkeypatch):
    added = [p for p in (_RUNTIME_DIR,) if p not in sys.path]
    sys.path[:0] = added
    try:
        import importlib
        mod = importlib.import_module("nodes.sandbox.tool_executor.handlers.code_interpreter")
        importlib.reload(mod)
        monkeypatch.setattr(mod, "_SANDBOX_EXECUTOR_URL", live_sandbox_executor)
        yield mod
    finally:
        for p in added:
            if p in sys.path:
                sys.path.remove(p)


async def test_execute_python_handler_runs_real_container_via_live_service(handler_mod):
    result, error = await handler_mod._execute_python_handler(
        call={"arguments": {"code": "import pandas as pd\nprint(pd.DataFrame({'a':[1,2]}).sum().to_dict())"}},
        cfg={},
    )

    assert error is None, error
    assert result["source"] == "tool/execute_python"
    assert "'a': 3" in result["content"]
    assert result["metadata"]["exit_code"] == 0


async def test_execute_python_handler_surfaces_security_rejection_as_tool_error(handler_mod):
    result, error = await handler_mod._execute_python_handler(
        call={"arguments": {"code": "import os\nos.system('echo hi')"}},
        cfg={},
    )

    assert result is None
    assert error["tool_name"] == "execute_python"
    assert "security validation failed" in error["error"]


async def test_execute_python_handler_no_code_provided_is_a_handler_level_error(handler_mod):
    result, error = await handler_mod._execute_python_handler(call={"arguments": {}}, cfg={})

    assert result is None
    assert error["error"] == "no code provided"


async def test_execute_sql_handler_rejects_when_no_connection_configured(handler_mod):
    result, error = await handler_mod._execute_sql_handler(
        call={"arguments": {"sql": "SELECT 1"}},
        cfg={},
    )

    assert result is None
    assert "not configured" in error["error"]


async def test_execute_sql_handler_calls_sandbox_executor_and_surfaces_query_error(handler_mod):
    """No real Snowflake account in this environment — connection is
    configured (so the handler proceeds past its own validation) but the
    real snowflake.connector.connect() call against a fake account fails;
    this proves the full real HTTP round-trip to sandbox-executor and back,
    with the failure surfaced as a normal tool error, not an exception."""
    result, error = await handler_mod._execute_sql_handler(
        call={"arguments": {"sql": "SELECT 1"}},
        cfg={"tool_config": {"execute_sql": {"connection": {
            "account": "nonexistent-test-account", "user": "u", "password": "p",
            "warehouse": "WH", "database": "DB", "schema": "ANALYTICS", "role": None,
        }}}},
    )

    assert result is None
    assert error["tool_name"] == "execute_sql"
