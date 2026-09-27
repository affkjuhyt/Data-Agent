"""GET /api/fleet/stats (router/knowledge/fleet.py).

Driven through the real require_perm/authenticate chain and the real
get_fleet_stats, with only the storage clients faked. What is pinned is what a
caller can observe: who is let in, which tenant the counts are taken for, and
how a missing backing service surfaces.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import services.kb_backend.dependencies.auth as auth_mod
from libs.database_connector.postgres_connector import current_tenant
from services.kb_backend.dependencies.permissions import PermissionStore
from services.kb_backend.router.knowledge import fleet

pytestmark = pytest.mark.unit

TENANT = "a0000000-0000-0000-0000-00000000f1ee"
GATEWAY_KEY = "fleet-test-gateway-key"


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _PermissionSession:
    """Serves RolePermissions rows to the real PermissionStore."""

    def __init__(self, grants: list[tuple[str, str]]) -> None:
        self._grants = grants

    async def execute(self, _stmt: Any) -> _Rows:
        return _Rows(self._grants)


class _DataSession:
    def __init__(self, owner: _Postgres) -> None:
        self._owner = owner

    async def execute(self, stmt: Any) -> _Rows:
        self._owner.gold_stmt = stmt
        return _Rows(self._owner.gold_rows)

    async def scalar(self, stmt: Any) -> int:
        self._owner.warehouse_stmt = stmt
        return self._owner.warehouse_count


class _Postgres:
    def __init__(self, grants: list[tuple[str, str]]) -> None:
        self.grants = grants
        self.permissions_down = False
        self.gold_rows: list[tuple[str, int]] = [("doc", 3), ("web", 2), ("image", 1), ("video", 4)]
        self.warehouse_count = 2
        self.conflicts = [{"batch_id": "b1"}, {"batch_id": "b1"}, {"batch_id": "b2"}, {"batch_id": None}]
        self.gold_stmt: Any = None
        self.warehouse_stmt: Any = None
        self.read_requests: list[Any] = []
        # The tenant the request-scoped ContextVar held when each data session
        # was opened — that is what get_client() scopes the session to.
        self.session_tenants: list[str | None] = []

    def is_healthy(self) -> bool:
        return True

    @asynccontextmanager
    async def get_platform_client(self) -> Any:
        if self.permissions_down:
            raise RuntimeError("postgres is down")
        yield _PermissionSession(self.grants)

    @asynccontextmanager
    async def get_client(self, tenant_id: str | None = None) -> Any:
        self.session_tenants.append(tenant_id or current_tenant())
        yield _DataSession(self)

    async def read(self, request: Any) -> Any:
        self.read_requests.append(request)
        return SimpleNamespace(data=self.conflicts)


class _Qdrant:
    def is_healthy(self) -> bool:
        return True

    def get_client(self) -> Any:
        async def get_collections() -> Any:
            return SimpleNamespace(collections=["a", "b", "c"])
        return SimpleNamespace(get_collections=get_collections)


class _Neo4jResult:
    def __init__(self, value: int) -> None:
        self._value = value

    async def single(self) -> dict[str, int]:
        return {"c": self._value}


class _Neo4jSession:
    def __init__(self, owner: _Neo4j) -> None:
        self._owner = owner

    async def run(self, query: str, **params: Any) -> _Neo4jResult:
        self._owner.params.append(params)
        return _Neo4jResult(7 if "rel" in query.split("RETURN")[0] else 10)


class _Neo4j:
    def __init__(self) -> None:
        self.params: list[dict[str, Any]] = []
        self.healthy = True

    def is_healthy(self) -> bool:
        return self.healthy

    def get_client(self) -> Any:
        owner = self

        @asynccontextmanager
        async def session() -> Any:
            yield _Neo4jSession(owner)

        return SimpleNamespace(session=session)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setenv("GATEWAY_KEY", GATEWAY_KEY)
    monkeypatch.delenv("KB_GATEWAY_KEY", raising=False)
    # The module-level store caches grants across tests; a fresh one per test
    # keeps each test's RolePermissions rows the only ones in play.
    monkeypatch.setattr(auth_mod, "store", PermissionStore())

    postgres = _Postgres(grants=[("PLATFORM_ADMIN", "viewFleetStats")])
    neo4j = _Neo4j()
    app = FastAPI()
    app.include_router(fleet.router)
    app.state.postgres = postgres
    app.state.qdrant = _Qdrant()
    app.state.neo4j = neo4j
    return SimpleNamespace(app=app, client=TestClient(app), postgres=postgres, neo4j=neo4j)


def _headers(role: str = "platform-admin", tenant: str = TENANT, key: str = GATEWAY_KEY) -> dict[str, str]:
    return {
        "X-Kong-Verified": "true",
        "X-Gateway-Key": key,
        "X-User-Id": "u-1",
        "X-User-Roles": role,
        "X-Tenant-Id": tenant,
    }


def test_route_is_registered() -> None:
    routes = {(m, r.path) for r in fleet.router.routes for m in r.methods}
    assert ("GET", "/api/fleet/stats") in routes


class TestGuard:
    def test_request_not_from_kong_is_401(self, env: SimpleNamespace) -> None:
        resp = env.client.get("/api/fleet/stats", headers={"X-User-Roles": "platform-admin", "X-Tenant-Id": TENANT})

        assert resp.status_code == 401
        assert env.postgres.session_tenants == []

    def test_wrong_gateway_key_is_401(self, env: SimpleNamespace) -> None:
        resp = env.client.get("/api/fleet/stats", headers=_headers(key="not-the-key"))

        assert resp.status_code == 401

    def test_role_without_view_fleet_stats_is_403(self, env: SimpleNamespace) -> None:
        env.postgres.grants.append(("BUSINESS_OPERATOR", "viewDocuments"))

        resp = env.client.get("/api/fleet/stats", headers=_headers(role="business-operator"))

        assert resp.status_code == 403
        assert "viewFleetStats" in resp.json()["detail"]
        assert env.postgres.session_tenants == [], "no stats may be read for a refused caller"

    def test_unreadable_permission_matrix_is_503_not_403(self, env: SimpleNamespace) -> None:
        env.postgres.permissions_down = True

        resp = env.client.get("/api/fleet/stats", headers=_headers())

        assert resp.status_code == 503

    def test_malformed_tenant_is_400(self, env: SimpleNamespace) -> None:
        resp = env.client.get("/api/fleet/stats", headers=_headers(tenant="not-a-uuid"))

        assert resp.status_code == 400


class TestStats:
    def test_counts_are_aggregated_into_the_dashboard_shape(self, env: SimpleNamespace) -> None:
        resp = env.client.get("/api/fleet/stats", headers=_headers())

        assert resp.status_code == 200
        assert resp.json() == {
            "code": 200,
            "error": None,
            "data": {
                # image + video fold into one "media" tile.
                "content": {"documents": 3, "web": 2, "media": 5, "warehouses": 2},
                "qdrant_collections": 3,
                "neo4j_nodes": 10,
                "neo4j_relationships": 7,
                # Two pending rows share batch b1 and one has no batch at all.
                "unresolved_conflict_batches": 2,
            },
        }

    def test_the_callers_tenant_scopes_every_count(self, env: SimpleNamespace) -> None:
        env.client.get("/api/fleet/stats", headers=_headers())

        assert env.postgres.session_tenants and set(env.postgres.session_tenants) == {TENANT}
        assert uuid.UUID(TENANT) in env.postgres.gold_stmt.compile().params.values()
        assert [r.tenant_id for r in env.postgres.read_requests] == [TENANT]
        assert env.neo4j.params and all(p == {"tid": TENANT} for p in env.neo4j.params)

    def test_warehouse_count_is_filtered_to_the_callers_tenant(self, env: SimpleNamespace) -> None:
        """It used to filter only is_deleted, so the Warehouses tile counted every
        tenant's connections whenever the role bypassed RLS."""
        resp = env.client.get("/api/fleet/stats", headers=_headers())

        assert resp.status_code == 200 and env.postgres.warehouse_stmt is not None
        assert uuid.UUID(TENANT) in env.postgres.warehouse_stmt.compile().params.values()

    def test_missing_backing_service_is_503(self, env: SimpleNamespace) -> None:
        del env.app.state.qdrant

        resp = env.client.get("/api/fleet/stats", headers=_headers())

        assert resp.status_code == 503
        assert "qdrant" in resp.json()["detail"]

    def test_unhealthy_backing_service_is_503(self, env: SimpleNamespace) -> None:
        env.neo4j.healthy = False

        resp = env.client.get("/api/fleet/stats", headers=_headers())

        assert resp.status_code == 503
        assert "neo4j" in resp.json()["detail"]
