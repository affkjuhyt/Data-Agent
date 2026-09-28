"""Integration test for workflow_runtime's execute_python/execute_sql tool
handlers (nodes/sandbox/tool_executor/handlers/code_interpreter.py) talking
over real HTTP to a *live* sandbox_executor instance — closes the gap that
test_sandbox_executor_api.py only drove sandbox_executor's own FastAPI app
directly, never the workflow_runtime side that calls it.

sandbox_executor's own app runs in-process via uvicorn on a real localhost
port (no need to build/run its own Docker image for this — its code still
uses the real Docker daemon to spawn real sandbox-runner containers
underneath, exactly as it does in production). Requires Docker + the
sandbox-runner:latest image; skipped otherwise.
"""
from __future__ import annotations

import json
import pathlib
import sys
import threading
import time

import httpx
import pytest
import uvicorn

from tests.integration._sandbox import SKIP_REASON, docker_and_image_available

pytestmark = pytest.mark.integration

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SANDBOX_DIR = str(REPO_ROOT / "backend" / "services" / "sandbox_executor")
_PORT = 8599


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_and_image_available(), reason=SKIP_REASON),
]


@pytest.fixture(scope="module")
def live_sandbox_executor():
    """Runs sandbox_executor's real FastAPI app on a real port in a
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

        # sandbox_executor's lifespan now spins up the warm container pool
        # (docs/1.md Kế hoạch 1) on startup — real container creates take
        # longer than the old instant (no-op) startup did.
        for _ in range(300):
            if server.started:
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("sandbox_executor did not start in time")

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
    # The HTTP transport (and the URL it posts to) moved out of the handler
    # module into _sandbox_http, which reads SANDBOX_EXECUTOR_URL per call.
    from services.workflow_runtime.nodes.sandbox.tool_executor import _sandbox_http
    from services.workflow_runtime.nodes.sandbox.tool_executor.handlers import code_interpreter

    monkeypatch.setattr(_sandbox_http, "SANDBOX_EXECUTOR_URL", live_sandbox_executor)
    return code_interpreter


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


async def test_execute_python_stream_emits_output_incrementally_over_real_http(live_sandbox_executor):
    """docs/1.md mục h: "stream trạng thái/stdout-stderr của lần exec
    đang chạy" — genuinely incremental delivery, proven against a real
    HTTP connection (httpx) to the real running uvicorn server this
    module's fixture starts. Deliberately NOT driven through FastAPI's
    TestClient the way test_sandbox_executor_api.py's SSE tests are:
    TestClient's own streaming support was observed (empirically, while
    building this feature) to buffer the *entire* SSE response and
    deliver every line to test code at once, regardless of how
    incrementally the server actually sent it — a test built on
    TestClient here would pass even for a regression back to the old
    batched-at-the-end design, defeating the point of this test.
    """
    code = "for i in range(3):\n    print(f'chunk-{i}')\n    x = sum(range(15_000_000))\n"
    async with httpx.AsyncClient() as http:
        resp = await http.post(f"{live_sandbox_executor}/execute/python", json={"code": code, "timeout_s": 15})
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        output_events: list[tuple[float, str]] = []
        t0 = time.monotonic()
        async with http.stream(
            "GET", f"{live_sandbox_executor}/execute/python/{job_id}/stream", timeout=30,
        ) as stream_resp:
            assert stream_resp.status_code == 200
            async for line in stream_resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = json.loads(line[len("data:"):].strip())
                if "stream" in payload and "text" in payload:
                    output_events.append((time.monotonic() - t0, payload["text"].strip()))
                if payload.get("status") == "done":
                    break

    assert [text for _, text in output_events] == ["chunk-0", "chunk-1", "chunk-2"], output_events

    arrival_times = [t for t, _ in output_events]
    assert arrival_times[-1] - arrival_times[0] > 0.2, (
        f"output events arrived within {arrival_times[-1] - arrival_times[0]:.3f}s of each other "
        f"(arrival times: {arrival_times}) — expected genuine incremental delivery with real gaps "
        "between them, matching the ~0.3-0.4s CPU-bound gap between each print() under the pool's "
        "0.5-CPU limit"
    )


async def test_execute_sql_handler_calls_sandbox_executor_and_surfaces_query_error(handler_mod):
    """No real Snowflake account in this environment — connection is
    configured (so the handler proceeds past its own validation) but the
    real snowflake.connector.connect() call against a fake account fails;
    this proves the full real HTTP round-trip to sandbox_executor and back,
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
