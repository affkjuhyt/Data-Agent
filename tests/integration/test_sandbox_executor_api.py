"""Integration tests for services/sandbox-executor/main.py — FastAPI
TestClient driving the real app, real Docker daemon, real sandbox-runner
container per call (no mocking of docker_runner/security_visitor/sql_guard
— those are exercised end to end). Requires Docker and the sandbox-runner
image to already be built locally; skipped otherwise.

Snowflake (/execute/sql) is NOT exercised here — no real warehouse is
configured in CI/dev (see "Mock for now" decision); execute_sql's HTTP
plumbing is covered by unit tests on sql_guard/snowflake_readonly instead.
"""
from __future__ import annotations

import importlib
import pathlib
import shutil
import sys

import docker
import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "services" / "sandbox-executor")


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


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_execute_python_runs_real_container_and_returns_stdout(client):
    resp = client.post("/execute/python", json={
        "code": "import pandas as pd\nprint(pd.DataFrame({'a':[1,2]}).sum().to_dict())",
        "timeout_s": 10,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "'a': 3" in body["stdout"]
    assert body["exit_code"] == 0


def test_execute_python_rejects_blocked_import_before_spawning_container(client):
    resp = client.post("/execute/python", json={"code": "import os\nos.system('echo hi')"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "security validation failed" in body["error"]


def test_execute_python_rejects_doc_obfuscation_example(client):
    resp = client.post("/execute/python", json={"code": "getattr(__builtins__, 'ev'+'al')('1+1')"})
    body = resp.json()
    assert body["success"] is False


def test_execute_python_times_out_on_infinite_loop(client):
    # `time` isn't in ALLOWED_MODULES, so a busy loop (no import needed)
    # stands in for time.sleep() here.
    resp = client.post("/execute/python", json={
        "code": "while True:\n    pass",
        "timeout_s": 2,
    })
    assert resp.status_code == 200
    body = resp.json()
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
    resp = client.post("/execute/python", json={
        "code": "import pandas as pd\npd.read_csv('http://example.com/data.csv')",
        "timeout_s": 15,
    })
    body = resp.json()
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
    resp = client.post("/execute/python", json={
        "code": "data = bytearray(b'\\x01' * (400 * 1024 * 1024))\nprint(len(data))",
        "timeout_s": 15,
        "memory_mb": 256,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["exit_code"] == 137  # SIGKILL — OOM killer
    assert body["stdout"] == ""  # never reached the print()


def test_execute_python_memory_limit_allows_allocation_under_the_cap(client):
    resp = client.post("/execute/python", json={
        "code": "data = bytearray(b'\\x01' * (50 * 1024 * 1024))\nprint(len(data))",
        "timeout_s": 15,
        "memory_mb": 256,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["stdout"].strip() == str(50 * 1024 * 1024)
