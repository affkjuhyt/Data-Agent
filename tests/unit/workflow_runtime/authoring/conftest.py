"""Fixtures for the authoring router tests (agents, workflows, canvas).

These routers used to be the separate flow_builder service and now live inside
workflow_runtime. The app under test is the real `create_app()`: authentication
is attached at include time there, not per route, so testing a router on its own
would miss exactly the guard these tests are meant to pin. TestClient is used
without a `with` block, which keeps the lifespan (Postgres/Mongo connect, health
loops) from running.
"""
from __future__ import annotations

import importlib
import types
from typing import Any

import pytest

GATEWAY_KEY = "gw-test-key"
TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(scope="module")
def fb() -> types.SimpleNamespace:
    """The workflow_runtime app module and the authoring routers."""
    pkg = "services.workflow_runtime"
    return types.SimpleNamespace(
        app_module=importlib.import_module(f"{pkg}.app"),
        agents=importlib.import_module(f"{pkg}.routers.agents"),
        canvas=importlib.import_module(f"{pkg}.routers.canvas"),
        workflows=importlib.import_module(f"{pkg}.routers.workflows"),
        pg_client=importlib.import_module(
            "libs.database_connector.postgres_connector"
        ).client,
    )


@pytest.fixture
def client(fb: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> Any:
    from fastapi.testclient import TestClient

    monkeypatch.setenv("GATEWAY_KEY", GATEWAY_KEY)
    monkeypatch.delenv("KB_GATEWAY_KEY", raising=False)
    monkeypatch.delenv("RUNTIME_ALLOWED_SERVICES", raising=False)
    # ensure_db_available() reads the shared connector's flag; the lifespan that
    # would set it never runs here.
    monkeypatch.setattr(fb.pg_client, "_connected", True)
    return TestClient(fb.app_module.create_app())


def kong_headers(tenant_id: str = TENANT_A, *, key: str = GATEWAY_KEY) -> dict[str, str]:
    """Headers Kong injects after verifying a Keycloak JWT."""
    headers = {
        "X-Kong-Verified": "true",
        "X-Gateway-Key": key,
        "X-User-Id": "user-1",
        "X-User-Roles": "ai-engineer",
    }
    if tenant_id:
        headers["X-Tenant-Id"] = tenant_id
    return headers


def route_table(app: Any) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for route in app.routes:
        for method in getattr(route, "methods", None) or ():
            if method != "HEAD":
                out.add((method, route.path))
    return out
