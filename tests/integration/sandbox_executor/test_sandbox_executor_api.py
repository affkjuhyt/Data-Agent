"""Integration tests for backend/services/sandbox_executor/main.py — FastAPI
TestClient driving the real app, real Docker daemon, real sandbox-runner
container per call (no mocking of docker_runner/security_visitor/sql_guard
— those are exercised end to end). Requires Docker and the sandbox-runner
image to already be built locally; skipped otherwise.

docs/1.md "Kế hoạch 1" mục i: POST /execute/python no longer runs inline —
it enqueues and returns 202 + {job_id}, and the caller polls GET
.../{job_id} until a terminal status. `_post_and_poll` below is that
client-side loop, standing in for what code_interpreter.py's
`_submit_and_poll` does against the real service.

Snowflake (/execute/sql) is NOT exercised end-to-end here — no real
warehouse is configured in CI/dev (see "Mock for now" decision);
execute_sql's queueing/HTTP plumbing is covered by the request-validation
test below, and its query logic by unit tests on sql_guard/
snowflake_readonly instead.
"""
from __future__ import annotations

import importlib
import json
import pathlib
import shutil
import sys
import time

import docker
import pytest

from tests.integration._sandbox import SKIP_REASON, docker_and_image_available

pytestmark = pytest.mark.integration

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "sandbox_executor")


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_and_image_available(), reason=SKIP_REASON),
]


@pytest.fixture
def client():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    try:
        from fastapi.testclient import TestClient
        import main as sandbox_main
        importlib.reload(sandbox_main)
        with TestClient(sandbox_main.app) as tc:
            yield tc
    finally:
        for p in added:
            if p in sys.path:
                sys.path.remove(p)


def _post_and_poll(client, path: str, body: dict, max_wait_s: float = 30.0) -> tuple[int, dict]:
    """Mirrors code_interpreter.py's _submit_and_poll against the real
    HTTP surface: POST to enqueue (202 + job_id, or an immediate 503 if
    the queue itself is full), then poll GET {path}/{job_id} with backoff
    until a terminal status. Returns (final_http_status, body)."""
    resp = client.post(path, json=body)
    if resp.status_code == 503:
        return resp.status_code, resp.json()
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    assert resp.json()["status"] == "queued"

    deadline = time.monotonic() + max_wait_s
    delay = 0.2
    while True:
        poll = client.get(f"{path}/{job_id}")
        data = poll.json()
        if data.get("status") in ("queued", "running"):
            assert time.monotonic() < deadline, f"job {job_id} did not reach a terminal status within {max_wait_s}s"
            time.sleep(delay)
            delay = min(delay * 1.5, 1.0)
            continue
        return poll.status_code, data


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_get_unknown_job_id_returns_404(client):
    resp = client.get("/execute/python/does-not-exist")
    assert resp.status_code == 404


def test_execute_sql_missing_account_rejected_before_queueing(client):
    """Pydantic-level validation (SnowflakeConnectionParams.account is
    required) still runs synchronously on the POST itself, before a job
    is ever enqueued — a malformed request shouldn't consume a queue slot
    or a worker cycle."""
    resp = client.post("/execute/sql", json={"sql": "SELECT 1", "connection": {"user": "u", "password": "p"}})
    assert resp.status_code == 422


def test_execute_python_runs_real_container_and_returns_stdout(client):
    status, body = _post_and_poll(client, "/execute/python", {
        "code": "import pandas as pd\nprint(pd.DataFrame({'a':[1,2]}).sum().to_dict())",
        "timeout_s": 10,
    })
    assert status == 200
    assert body["success"] is True
    assert "'a': 3" in body["stdout"]
    assert body["exit_code"] == 0


def test_execute_python_rejects_blocked_import_before_spawning_container(client):
    status, body = _post_and_poll(client, "/execute/python", {"code": "import os\nos.system('echo hi')"})
    assert status == 200
    assert body["success"] is False
    assert "security validation failed" in body["error"]


def test_execute_python_rejects_doc_obfuscation_example(client):
    status, body = _post_and_poll(client, "/execute/python", {"code": "getattr(__builtins__, 'ev'+'al')('1+1')"})
    assert status == 200
    assert body["success"] is False


def test_execute_python_times_out_on_infinite_loop(client):
    # `time` isn't in ALLOWED_MODULES, so a busy loop (no import needed)
    # stands in for time.sleep() here.
    status, body = _post_and_poll(client, "/execute/python", {
        "code": "while True:\n    pass",
        "timeout_s": 2,
    })
    assert status == 200
    assert body["success"] is False
    assert body.get("timed_out") is True


def test_execute_python_network_is_disabled(client):
    """--network none (doc §1.4) is defense-in-depth behind the AST
    whitelist: `socket`/`urllib`/`requests` are already blocked at
    validation time (see test_execute_python_rejects_blocked_import_*), so
    this proves the Docker-level isolation independently — pandas is an
    ALLOWED module, and pd.read_csv() against an http URL goes out through
    urllib internally without the agent's code ever importing a blocked
    module itself. --network none must still make that fail."""
    status, body = _post_and_poll(client, "/execute/python", {
        "code": "import pandas as pd\npd.read_csv('http://example.com/data.csv')",
        "timeout_s": 15,
    })
    assert status == 200
    assert body["success"] is False
    assert body.get("timed_out") is not True
    assert "Error" in body["stderr"] or "error" in body["stderr"].lower()


def test_execute_python_memory_limit_is_enforced(client):
    """--memory 256m (doc §1.4) — allocating well past the limit must get
    the process OOM-killed (exit code 137), not silently allowed to grow.

    bytearray(N) (zero-filled) is NOT a valid way to test this: CPython's
    calloc-backed zero allocation can be satisfied by the kernel's shared
    zero-page without committing real RSS, so a 400MB zero-filled
    bytearray succeeds even under a 256MB cgroup limit. Filling with
    non-zero bytes forces real physical pages to be committed.
    """
    status, body = _post_and_poll(client, "/execute/python", {
        "code": "data = bytearray(b'\\x01' * (400 * 1024 * 1024))\nprint(len(data))",
        "timeout_s": 15,
        "memory_mb": 256,
    })
    assert status == 200
    assert body["success"] is False
    assert body["exit_code"] == 137  # SIGKILL — OOM killer
    assert body["stdout"] == ""  # never reached the print()


def test_execute_python_memory_limit_allows_allocation_under_the_cap(client):
    status, body = _post_and_poll(client, "/execute/python", {
        "code": "data = bytearray(b'\\x01' * (50 * 1024 * 1024))\nprint(len(data))",
        "timeout_s": 15,
        "memory_mb": 256,
    })
    assert status == 200
    assert body["success"] is True
    assert body["stdout"].strip() == str(50 * 1024 * 1024)


def test_execute_python_stream_endpoint_reports_done_event(client):
    """docs/1.md mục i's SSE dashboard hook (mục h) — a status stream a
    dashboard can subscribe to instead of only polling. Proves the
    transport end to end (real event-stream response, real job
    lifecycle)."""
    resp = client.post("/execute/python", json={"code": "print('hello-stream')", "timeout_s": 10})
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    final_payload = None
    with client.stream("GET", f"/execute/python/{job_id}/stream") as stream_resp:
        assert stream_resp.status_code == 200
        for line in stream_resp.iter_lines():
            if not line.startswith("data:"):
                continue
            payload = json.loads(line[len("data:"):].strip())
            if payload.get("status") == "done":
                final_payload = payload
                break

    assert final_payload is not None, "stream never reported a done event"
    assert final_payload["success"] is True
    assert "hello-stream" in final_payload["stdout"]


def test_execute_python_stream_endpoint_emits_separate_output_events(client):
    """docs/1.md mục h: "stream trạng thái/stdout-stderr của lần exec
    đang chạy" — each print() must land as its own separate "output" SSE
    event carrying just that chunk, not only the final "done" event's
    complete/glommed-together stdout. This proves that shape/ordering
    through FastAPI's TestClient; it does NOT check wall-clock timing —
    TestClient's own streaming support was observed (empirically, while
    building this feature) to buffer the entire SSE response and deliver
    it to test code all at once regardless of how incrementally the
    server actually sent it, so timing assertions here would not be
    meaningful. See test_sandbox_handler_wiring.py's
    test_execute_python_stream_emits_output_incrementally_over_real_http
    for the real-HTTP-connection version of this test that DOES verify
    genuine incremental (not batched-at-the-end) delivery timing."""
    code = "for i in range(3):\n    print(f'chunk-{i}')\n    x = sum(range(15_000_000))\n"
    resp = client.post("/execute/python", json={"code": code, "timeout_s": 15})
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    output_events = []  # (stream, text)
    with client.stream("GET", f"/execute/python/{job_id}/stream") as stream_resp:
        assert stream_resp.status_code == 200
        for line in stream_resp.iter_lines():
            if not line.startswith("data:"):
                continue
            payload = json.loads(line[len("data:"):].strip())
            if "stream" in payload and "text" in payload:
                output_events.append((payload["stream"], payload["text"]))
            if payload.get("status") == "done":
                break

    assert [text.strip() for stream, text in output_events if stream == "stdout"] == [
        "chunk-0", "chunk-1", "chunk-2",
    ], f"expected 3 separate stdout output events in order, got: {output_events}"
