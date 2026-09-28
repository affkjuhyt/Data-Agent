"""Fixtures for the flow_builder router tests.

flow_builder's Dockerfile copies the service directory flat into /app, so its
modules import each other by bare name (`from auth import ...`, `from db_pg
import ...`, `from routers import agents`). Those names (app, auth, config,
schemas, utils, routers, ...) are generic enough to collide with any other
service loaded in the same process, so the service dir goes on sys.path and
the bare modules into sys.modules only for the life of this suite's module
fixture, and the previous entries are put back afterwards.

The app under test is the real `create_app()`: authentication is attached at
include time there, not per route, so testing a router on its own would miss
exactly the guard these tests are meant to pin. TestClient is used without a
`with` block, which keeps the lifespan (Postgres/Mongo connect, health loops)
from running.
"""
from __future__ import annotations

import importlib
import sys
import types
from collections.abc import Iterator
from typing import Any

import pytest

from .._loader import SERVICES

FLOW_BUILDER_DIR = SERVICES / "flow_builder"
_BARE_MODULES = (
    "app", "auth", "config", "db_mongo", "db_pg", "dependencies", "main",
    "schemas", "utils", "routers",
)

GATEWAY_KEY = "gw-test-key"
TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"


def _is_bare(name: str) -> bool:
    return any(name == m or name.startswith(m + ".") for m in _BARE_MODULES)


@pytest.fixture(scope="module")
def fb() -> Iterator[types.SimpleNamespace]:
    """The flow_builder app module and its routers, imported by bare name."""
    saved = {name: mod for name, mod in sys.modules.items() if _is_bare(name)}
    for name in saved:
        del sys.modules[name]
    entry = str(FLOW_BUILDER_DIR)
    sys.path.insert(0, entry)
    try:
        app_module = importlib.import_module("app")
        yield types.SimpleNamespace(
            app_module=app_module,
            agents=importlib.import_module("routers.agents"),
            canvas=importlib.import_module("routers.canvas"),
            workflows=importlib.import_module("routers.workflows"),
            pg_client=importlib.import_module(
                "libs.database_connector.postgres_connector"
            ).client,
        )
    finally:
        sys.path.remove(entry)
        for name in [n for n in sys.modules if _is_bare(n)]:
            del sys.modules[name]
        sys.modules.update(saved)


@pytest.fixture
def client(fb: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> Any:
    from fastapi.testclient import TestClient

    monkeypatch.setenv("GATEWAY_KEY", GATEWAY_KEY)
    monkeypatch.delenv("KB_GATEWAY_KEY", raising=False)
    monkeypatch.delenv("FLOW_BUILDER_ALLOWED_SERVICES", raising=False)
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
