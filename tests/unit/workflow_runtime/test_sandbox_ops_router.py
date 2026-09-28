"""/api/sandbox/* (routers/sandbox_ops.py) — the ops dashboard's read-only relay
to sandbox_executor.

The router declares no guard of its own: app.create_app attaches
get_current_user when it includes it. These tests mount it the same way and
stand sandbox_executor up behind an httpx.MockTransport, so the real proxy
code runs and what is checked is what reaches the upstream and what comes back.
"""
from __future__ import annotations

import ast
import pathlib
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from services.workflow_runtime.auth import get_current_user
from services.workflow_runtime.routers import sandbox_ops
from services.workflow_runtime.runtime_services import sandbox_proxy

pytestmark = pytest.mark.unit

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
APP_PY = REPO_ROOT / "backend" / "services" / "workflow_runtime" / "app.py"
GATEWAY_KEY = "sandbox-test-gateway-key"
HEADERS = {
    "X-Kong-Verified": "true",
    "X-Gateway-Key": GATEWAY_KEY,
    "X-User-Id": "ops-1",
    "X-User-Roles": "platform-admin",
    "X-Tenant-Id": "00000000-0000-0000-0000-0000000000aa",
}


class _Upstream:
    """sandbox_executor as seen over HTTP; records every path it is asked for."""

    def __init__(self) -> None:
        self.paths: list[str] = []
        self.down = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        path = request.url.path
        self.paths.append(path)
        if path == "/status":
            return httpx.Response(200, json={"python": {"running": 1}, "sql": {"running": 0}})
        if path.endswith("/stream"):
            return httpx.Response(200, content=b"event: status\ndata: {\"state\": \"done\"}\n\n")
        if path == "/execute/python/missing":
            return httpx.Response(404, json={"error": "job not found"})
        return httpx.Response(200, json={"path": path, "stdout": "42\n"})


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setenv("GATEWAY_KEY", GATEWAY_KEY)
    monkeypatch.delenv("KB_GATEWAY_KEY", raising=False)

    upstream = _Upstream()
    real_client = httpx.AsyncClient

    def client_factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return real_client(*args, transport=httpx.MockTransport(upstream.handle), **kwargs)

    # Patched on the proxy module's own reference, not on httpx itself, so the
    # TestClient's transport is untouched.
    monkeypatch.setattr(sandbox_proxy, "httpx", SimpleNamespace(AsyncClient=client_factory))

    app = FastAPI()
    app.include_router(sandbox_ops.router, dependencies=[Depends(get_current_user)])
    return SimpleNamespace(client=TestClient(app), upstream=upstream)


def test_routes_are_registered() -> None:
    routes = {(m, r.path) for r in sandbox_ops.router.routes for m in r.methods}
    assert routes == {
        ("GET", "/api/sandbox/status"),
        ("GET", "/api/sandbox/job/{kind}/{job_id}"),
        ("GET", "/api/sandbox/stream/{kind}/{job_id}"),
    }


def test_app_mounts_the_router_behind_authentication() -> None:
    """The router has no guard of its own, so dropping it from create_app's
    authenticated loop would expose sandbox job output to anyone who can reach
    the port. Read from source: importing app.py configures logging and
    LiteLLM process-wide."""
    tree = ast.parse(APP_PY.read_text())
    guarded: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple)):
            continue
        includes = [
            call for call in ast.walk(node)
            if isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "include_router"
            and any(kw.arg == "dependencies" for kw in call.keywords)
        ]
        if includes:
            guarded |= {ast.unparse(elt) for elt in node.iter.elts}
    assert "sandbox_ops.router" in guarded


class TestGuard:
    def test_caller_not_verified_by_kong_is_401(self, env: SimpleNamespace) -> None:
        resp = env.client.get("/api/sandbox/status")

        assert resp.status_code == 401
        assert env.upstream.paths == []

    def test_wrong_gateway_key_is_401(self, env: SimpleNamespace) -> None:
        resp = env.client.get("/api/sandbox/job/python/j1", headers={**HEADERS, "X-Gateway-Key": "guess"})

        assert resp.status_code == 401
        assert env.upstream.paths == []


class TestRelay:
    def test_status_is_relayed(self, env: SimpleNamespace) -> None:
        resp = env.client.get("/api/sandbox/status", headers=HEADERS)

        assert resp.status_code == 200
        assert resp.json() == {"python": {"running": 1}, "sql": {"running": 0}}
        assert env.upstream.paths == ["/status"]

    def test_job_detail_is_fetched_for_the_requested_kind_and_id(self, env: SimpleNamespace) -> None:
        resp = env.client.get("/api/sandbox/job/sql/j-42", headers=HEADERS)

        assert resp.status_code == 200
        assert resp.json()["path"] == "/execute/sql/j-42"

    def test_upstream_status_code_is_preserved(self, env: SimpleNamespace) -> None:
        resp = env.client.get("/api/sandbox/job/python/missing", headers=HEADERS)

        assert resp.status_code == 404
        assert resp.json() == {"error": "job not found"}

    def test_stream_relays_the_executors_sse_bytes_unchanged(self, env: SimpleNamespace) -> None:
        resp = env.client.get("/api/sandbox/stream/python/j-7", headers=HEADERS)

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert resp.text == 'event: status\ndata: {"state": "done"}\n\n'
        assert env.upstream.paths == ["/execute/python/j-7/stream"]

    def test_unreachable_executor_is_503(self, env: SimpleNamespace) -> None:
        env.upstream.down = True

        resp = env.client.get("/api/sandbox/status", headers=HEADERS)

        assert resp.status_code == 503
        assert "sandbox_executor unreachable" in resp.json()["error"]

    def test_unreachable_executor_ends_the_stream_with_an_error_event(self, env: SimpleNamespace) -> None:
        env.upstream.down = True

        resp = env.client.get("/api/sandbox/stream/sql/j-8", headers=HEADERS)

        assert resp.status_code == 200
        assert resp.text.startswith("event: error\n")


class TestKindValidation:
    @pytest.mark.parametrize("path", ["/api/sandbox/job/bash/j1", "/api/sandbox/stream/bash/j1"])
    def test_unknown_kind_is_400_and_never_reaches_the_executor(self, env: SimpleNamespace, path: str) -> None:
        resp = env.client.get(path, headers=HEADERS)

        assert resp.status_code == 400
        assert resp.json() == {"error": "kind must be 'python' or 'sql'"}
        assert env.upstream.paths == []
