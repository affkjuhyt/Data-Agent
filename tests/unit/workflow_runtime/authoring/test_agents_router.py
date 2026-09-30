"""workflow_runtime routers/agents.py: route table, gateway auth, tenant scoping.

The Postgres layer (authoring_repository) is replaced by an in-memory store that models what
row-level security gives the real one: a lookup scoped to tenant B does not see
tenant A's agent. What the tests pin is the router's side of that contract —
the tenant it passes down comes from the verified caller and nowhere else.
"""
from __future__ import annotations

import types
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from .conftest import TENANT_A, TENANT_B, kong_headers, route_table

pytestmark = pytest.mark.unit

AGENT_ID = "aaaaaaaa-0000-0000-0000-000000000001"


class FakeAgentStore:
    def __init__(self) -> None:
        self.agents: dict[str, dict[str, Any]] = {}
        self.cleared_canvases: list[str] = []
        self.versions_by_agent: dict[str, list[str]] = {}

    def add(self, agent_id: str, tenant_id: str, name: str = "agent") -> None:
        self.agents[agent_id] = {
            "id": uuid.UUID(agent_id),
            "name": name,
            "tenant_id": uuid.UUID(tenant_id),
            "created_at": datetime(2026, 9, 1, 8, 30, tzinfo=UTC),
        }

    def _visible(self, agent_id: str, tenant_id: str | None) -> dict[str, Any] | None:
        agent = self.agents.get(agent_id)
        if agent is None or (tenant_id is not None and str(agent["tenant_id"]) != tenant_id):
            return None
        return agent

    async def create_agent(self, name: str, description: str, tenant_id: str) -> dict[str, Any]:
        agent_id = str(uuid.uuid4())
        self.add(agent_id, tenant_id, name)
        return {"id": agent_id, "name": name, "description": description, "tenant_id": tenant_id}

    async def list_agents(self, tenant_id: str) -> list[dict[str, Any]]:
        return [a for a in self.agents.values() if str(a["tenant_id"]) == tenant_id]

    async def get_agent(self, agent_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
        return self._visible(agent_id, tenant_id)

    async def list_workflows(self, agent_id: str) -> list[dict[str, Any]]:
        return [{"id": uuid.UUID(int=1), "name": "main"}]

    async def delete_agent(self, agent_id: str, tenant_id: str | None = None) -> list[str]:
        if self._visible(agent_id, tenant_id) is None:
            raise ValueError("agent not found")
        del self.agents[agent_id]
        return self.versions_by_agent.get(agent_id, [])

    async def save_canvas(self, version_id: str, nodes: list[Any], edges: list[Any]) -> None:
        assert nodes == [] and edges == []
        self.cleared_canvases.append(version_id)

    async def update_agent_draft(
        self, agent_id: str, fields: dict[str, Any], tenant_id: str | None = None
    ) -> dict[str, Any]:
        return {"agent_id": agent_id, "updated": sorted(fields), "tenant_id": tenant_id}

    async def publish_agent(
        self, agent_id: str, workflow_id: str, kb_connection_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        if self._visible(agent_id, tenant_id) is None:
            raise ValueError("agent not found")
        if workflow_id == "no-published-version":
            raise ValueError("workflow has no published version")
        return {"agent_id": agent_id, "published": True}

    async def unpublish_agent(self, agent_id: str, tenant_id: str | None = None) -> dict[str, Any]:
        return {"agent_id": agent_id, "published": False}


@pytest.fixture
def store(fb: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> FakeAgentStore:
    fake = FakeAgentStore()
    for name in ("create_agent", "list_agents", "get_agent", "list_workflows", "delete_agent",
                 "save_canvas", "update_agent_draft", "publish_agent", "unpublish_agent"):
        monkeypatch.setattr(fb.agents, name, getattr(fake, name))
    return fake


def test_routes_registered(fb: types.SimpleNamespace) -> None:
    assert route_table(fb.agents.router) == {
        ("POST", "/api/agents"),
        ("GET", "/api/agents"),
        ("GET", "/api/agents/{agent_id}"),
        ("DELETE", "/api/agents/{agent_id}"),
        ("PATCH", "/api/agents/{agent_id}"),
        ("POST", "/api/agents/{agent_id}/publish"),
        ("POST", "/api/agents/{agent_id}/unpublish"),
    }
    # Mounted by the real app factory, not only declared on the router.
    assert route_table(fb.agents.router) <= route_table(fb.app_module.create_app())


class TestAuth:
    def test_no_gateway_headers_is_401(self, client: Any, store: FakeAgentStore) -> None:
        resp = client.get("/api/agents", headers={"X-Tenant-Id": TENANT_A})
        assert resp.status_code == 401

    def test_wrong_gateway_key_is_401(self, client: Any, store: FakeAgentStore) -> None:
        # X-Kong-Verified is only a string; the shared key is what proves Kong sent it.
        resp = client.get("/api/agents", headers=kong_headers(key="guessed"))
        assert resp.status_code == 401

    def test_verified_caller_without_tenant_is_403(self, client: Any, store: FakeAgentStore) -> None:
        store.add(AGENT_ID, TENANT_A)
        resp = client.get("/api/agents", headers=kong_headers(tenant_id=""))
        assert resp.status_code == 403

    def test_rejected_caller_creates_nothing(self, client: Any, store: FakeAgentStore) -> None:
        resp = client.post("/api/agents", json={"name": "x"}, headers={"X-Kong-Verified": "true"})
        assert resp.status_code == 401
        assert store.agents == {}


class TestHappyPath:
    def test_create_uses_caller_tenant(self, client: Any, store: FakeAgentStore) -> None:
        resp = client.post("/api/agents", json={"name": "Gold desk"}, headers=kong_headers(TENANT_B))
        assert resp.status_code == 201
        body = resp.json()
        assert body["tenant_id"] == TENANT_B
        assert body["description"] == ""
        assert str(store.agents[body["id"]]["tenant_id"]) == TENANT_B

    def test_list_is_tenant_scoped_and_serialized(self, client: Any, store: FakeAgentStore) -> None:
        store.add(AGENT_ID, TENANT_A, "mine")
        store.add(str(uuid.uuid4()), TENANT_B, "theirs")
        resp = client.get("/api/agents", headers=kong_headers(TENANT_A))
        assert resp.status_code == 200
        assert resp.json() == [{
            "id": AGENT_ID,
            "name": "mine",
            "tenant_id": TENANT_A,
            "created_at": "2026-09-01T08:30:00+00:00",
        }]

    def test_get_includes_workflows(self, client: Any, store: FakeAgentStore) -> None:
        store.add(AGENT_ID, TENANT_A)
        resp = client.get(f"/api/agents/{AGENT_ID}", headers=kong_headers(TENANT_A))
        assert resp.status_code == 200
        assert resp.json()["workflows"] == [{"id": str(uuid.UUID(int=1)), "name": "main"}]

    def test_delete_clears_every_version_canvas(self, client: Any, store: FakeAgentStore) -> None:
        store.add(AGENT_ID, TENANT_A)
        store.versions_by_agent[AGENT_ID] = ["v1", "v2"]
        resp = client.delete(f"/api/agents/{AGENT_ID}", headers=kong_headers(TENANT_A))
        assert resp.status_code == 204
        assert AGENT_ID not in store.agents
        assert store.cleared_canvases == ["v1", "v2"]

    def test_patch_passes_only_set_fields(self, client: Any, store: FakeAgentStore) -> None:
        resp = client.patch(
            f"/api/agents/{AGENT_ID}", json={"name": "n", "memory_enabled": False},
            headers=kong_headers(TENANT_A),
        )
        assert resp.status_code == 200
        assert resp.json() == {"agent_id": AGENT_ID, "updated": ["memory_enabled", "name"],
                               "tenant_id": TENANT_A}


class TestErrors:
    def test_other_tenants_agent_is_404(self, client: Any, store: FakeAgentStore) -> None:
        store.add(AGENT_ID, TENANT_A)
        resp = client.get(f"/api/agents/{AGENT_ID}", headers=kong_headers(TENANT_B))
        assert resp.status_code == 404

    def test_delete_other_tenants_agent_is_404_and_keeps_it(
        self, client: Any, store: FakeAgentStore
    ) -> None:
        store.add(AGENT_ID, TENANT_A)
        resp = client.delete(f"/api/agents/{AGENT_ID}", headers=kong_headers(TENANT_B))
        assert resp.status_code == 404
        assert AGENT_ID in store.agents

    def test_publish_value_error_is_400(self, client: Any, store: FakeAgentStore) -> None:
        store.add(AGENT_ID, TENANT_A)
        resp = client.post(
            f"/api/agents/{AGENT_ID}/publish", json={"workflow_id": "no-published-version"},
            headers=kong_headers(TENANT_A),
        )
        assert resp.status_code == 400
        assert "no published version" in resp.json()["detail"]

    def test_create_without_name_is_422(self, client: Any, store: FakeAgentStore) -> None:
        resp = client.post("/api/agents", json={"description": "d"}, headers=kong_headers())
        assert resp.status_code == 422
        assert store.agents == {}

    def test_publish_without_workflow_id_is_422(self, client: Any, store: FakeAgentStore) -> None:
        resp = client.post(f"/api/agents/{AGENT_ID}/publish", json={}, headers=kong_headers())
        assert resp.status_code == 422

    def test_db_down_is_503(
        self, client: Any, store: FakeAgentStore, fb: types.SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(fb.pg_client, "_connected", False)
        resp = client.get("/api/agents", headers=kong_headers())
        assert resp.status_code == 503
