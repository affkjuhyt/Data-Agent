"""workflow_runtime routers/workflows.py: route table, auth, happy paths, error mapping.

Most routes here declare no auth dependency of their own; create_app() attaches
`get_current_user` at include time. The first test class pins that, since a
router-only test app would silently report those routes as open.

The fake store mirrors authoring_repository's real signatures. Workflows and WorkflowVersions
have no tenant_id column (backend/libs/basemodel/.../workflow_orm.py), so RLS
cannot scope them: whatever tenant check exists has to happen in the router.
"""
from __future__ import annotations

import types
from typing import Any

import pytest

from .conftest import TENANT_A, TENANT_B, kong_headers, patch_router_store, route_table

pytestmark = pytest.mark.unit

AGENT_ID = "aaaaaaaa-0000-0000-0000-000000000001"
WORKFLOW_ID = "cccccccc-0000-0000-0000-000000000001"
VERSION_ID = "dddddddd-0000-0000-0000-000000000001"
ARCHIVED_ID = "dddddddd-0000-0000-0000-000000000002"


class FakeWorkflowStore:
    def __init__(self) -> None:
        self.agent_tenant = {AGENT_ID: TENANT_A}
        self.workflow_agent = {WORKFLOW_ID: AGENT_ID}
        self.version_workflow = {VERSION_ID: WORKFLOW_ID, ARCHIVED_ID: WORKFLOW_ID}
        self.status = {VERSION_ID: "draft", ARCHIVED_ID: "archived"}
        self.created: list[tuple[str, str, str]] = []
        self.cleared_canvases: list[str] = []
        self.invalidated: list[str] = []

    def _tenant_of_workflow(self, workflow_id: str) -> str | None:
        return self.agent_tenant.get(self.workflow_agent.get(workflow_id, ""))

    async def get_agent(self, agent_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
        owner = self.agent_tenant.get(agent_id)
        if owner is None or (tenant_id is not None and owner != tenant_id):
            return None
        return {"id": agent_id, "tenant_id": owner}

    async def workflow_belongs_to(self, workflow_id: str, tenant_id: str) -> bool:
        return self._tenant_of_workflow(workflow_id) == tenant_id

    async def workflow_version_belongs_to(self, version_id: str, tenant_id: str) -> bool:
        return self._tenant_of_workflow(self.version_workflow.get(version_id, "")) == tenant_id

    async def create_workflow(self, agent_id: str, name: str, description: str = "", *, tenant_id: str) -> dict[str, Any]:
        self.created.append((agent_id, name, description))
        return {"workflow_id": "new-wf", "workflow_version_id": "new-wv"}

    async def list_workflows(self, agent_id: str, *, tenant_id: str) -> list[dict[str, Any]]:
        return [{"id": w, "name": "main"} for w, a in self.workflow_agent.items() if a == agent_id]

    async def list_workflow_versions(self, workflow_id: str, *, tenant_id: str) -> list[dict[str, Any]]:
        return [{"id": v, "status": self.status[v]}
                for v, w in self.version_workflow.items() if w == workflow_id]

    async def create_draft_version(self, workflow_id: str, *, tenant_id: str) -> dict[str, Any]:
        if workflow_id not in self.workflow_agent:
            raise ValueError("workflow not found")
        return {"workflow_version_id": "new-draft", "version": 2}

    async def delete_workflow_version(self, version_id: str, tenant_id: str | None = None) -> None:
        wf = self.version_workflow.get(version_id, "")
        if tenant_id is not None and self._tenant_of_workflow(wf) != tenant_id:
            raise ValueError("workflow_version not found")
        self.version_workflow.pop(version_id)

    async def delete_workflow(self, workflow_id: str, tenant_id: str | None = None) -> list[str]:
        if tenant_id is not None and self._tenant_of_workflow(workflow_id) != tenant_id:
            raise ValueError("workflow not found")
        versions = [v for v, w in self.version_workflow.items() if w == workflow_id]
        del self.workflow_agent[workflow_id]
        return versions

    async def save_canvas(self, version_id: str, nodes: list[Any], edges: list[Any]) -> None:
        self.cleared_canvases.append(version_id)

    async def publish_workflow_version(self, version_id: str, changelog: str | None = None, *, tenant_id: str) -> dict[str, Any]:
        if self.status.get(version_id) != "draft":
            raise ValueError("already published")
        self.status[version_id] = "published"
        return {"workflow_version_id": version_id, "status": "published", "changelog": changelog}

    async def republish_workflow_version(self, version_id: str, *, tenant_id: str) -> dict[str, Any]:
        if self.status.get(version_id) != "archived":
            raise ValueError("can only republish archived versions")
        self.status[version_id] = "published"
        return {"workflow_version_id": version_id, "status": "published"}

    async def publish_graph_invalidation(self, redis_url: str, version_id: str) -> bool:
        self.invalidated.append(version_id)
        return True


_PATCHED = (
    "get_agent", "create_workflow", "list_workflows", "list_workflow_versions",
    "create_draft_version", "delete_workflow_version", "delete_workflow", "save_canvas",
    "publish_workflow_version", "republish_workflow_version", "publish_graph_invalidation",
    "workflow_belongs_to", "workflow_version_belongs_to",
)


@pytest.fixture
def store(fb: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> FakeWorkflowStore:
    fake = FakeWorkflowStore()
    patch_router_store(monkeypatch, fb.workflows, fake, _PATCHED)
    return fake


def test_routes_registered(fb: types.SimpleNamespace) -> None:
    expected = {
        ("POST", "/api/agents/{agent_id}/workflows"),
        ("GET", "/api/agents/{agent_id}/workflows"),
        ("GET", "/api/workflows/{workflow_id}/versions"),
        ("POST", "/api/workflows/{workflow_id}/versions"),
        ("DELETE", "/api/workflow-versions/{version_id}"),
        ("DELETE", "/api/workflows/{workflow_id}"),
        ("POST", "/api/workflow-versions/{workflow_version_id}/publish"),
        ("POST", "/api/workflow-versions/{workflow_version_id}/republish"),
    }
    assert route_table(fb.workflows.router) == expected
    assert expected <= route_table(fb.app_module.create_app())


class TestAuthAtInclude:
    @pytest.mark.parametrize(("method", "path"), [
        ("GET", f"/api/agents/{AGENT_ID}/workflows"),
        ("GET", f"/api/workflows/{WORKFLOW_ID}/versions"),
        ("POST", f"/api/workflows/{WORKFLOW_ID}/versions"),
        ("POST", f"/api/workflow-versions/{VERSION_ID}/publish"),
        ("POST", f"/api/workflow-versions/{ARCHIVED_ID}/republish"),
    ])
    def test_route_without_own_dependency_still_401(
        self, client: Any, store: FakeWorkflowStore, method: str, path: str
    ) -> None:
        resp = client.request(method, path, headers={"X-Tenant-Id": TENANT_A})
        assert resp.status_code == 401
        assert store.status == {VERSION_ID: "draft", ARCHIVED_ID: "archived"}
        assert store.invalidated == []

    def test_delete_without_tenant_is_403(self, client: Any, store: FakeWorkflowStore) -> None:
        resp = client.delete(f"/api/workflows/{WORKFLOW_ID}", headers=kong_headers(tenant_id=""))
        assert resp.status_code == 403
        assert WORKFLOW_ID in store.workflow_agent


class TestHappyPath:
    def test_create_workflow(self, client: Any, store: FakeWorkflowStore) -> None:
        resp = client.post(f"/api/agents/{AGENT_ID}/workflows", json={"name": "wf"},
                           headers=kong_headers(TENANT_A))
        assert resp.status_code == 201
        assert resp.json() == {"workflow_id": "new-wf", "workflow_version_id": "new-wv"}
        assert store.created == [(AGENT_ID, "wf", "")]

    def test_publish_broadcasts_invalidation(self, client: Any, store: FakeWorkflowStore) -> None:
        resp = client.post(f"/api/workflow-versions/{VERSION_ID}/publish",
                           json={"changelog": "first"}, headers=kong_headers(TENANT_A))
        assert resp.status_code == 200
        assert resp.json()["changelog"] == "first"
        assert store.status[VERSION_ID] == "published"
        assert store.invalidated == [VERSION_ID]

    def test_publish_body_is_optional(self, client: Any, store: FakeWorkflowStore) -> None:
        resp = client.post(f"/api/workflow-versions/{VERSION_ID}/publish", headers=kong_headers())
        assert resp.status_code == 200
        assert resp.json()["changelog"] is None

    def test_republish_archived(self, client: Any, store: FakeWorkflowStore) -> None:
        resp = client.post(f"/api/workflow-versions/{ARCHIVED_ID}/republish", headers=kong_headers())
        assert resp.status_code == 200
        # Same version id means the same runtime cache key; the broadcast is
        # what forces the rebuild.
        assert store.invalidated == [ARCHIVED_ID]

    def test_delete_workflow_clears_canvases(self, client: Any, store: FakeWorkflowStore) -> None:
        resp = client.delete(f"/api/workflows/{WORKFLOW_ID}", headers=kong_headers(TENANT_A))
        assert resp.status_code == 204
        assert sorted(store.cleared_canvases) == sorted([VERSION_ID, ARCHIVED_ID])


class TestErrors:
    def test_create_workflow_unknown_agent_is_404(self, client: Any, store: FakeWorkflowStore) -> None:
        resp = client.post("/api/agents/unknown/workflows", json={"name": "wf"}, headers=kong_headers())
        assert resp.status_code == 404
        assert store.created == []

    def test_create_workflow_without_name_is_422(self, client: Any, store: FakeWorkflowStore) -> None:
        resp = client.post(f"/api/agents/{AGENT_ID}/workflows", json={}, headers=kong_headers())
        assert resp.status_code == 422

    def test_publish_twice_is_400_without_broadcast(self, client: Any, store: FakeWorkflowStore) -> None:
        store.status[VERSION_ID] = "published"
        resp = client.post(f"/api/workflow-versions/{VERSION_ID}/publish", headers=kong_headers())
        assert resp.status_code == 400
        assert store.invalidated == []

    def test_draft_for_unknown_workflow_is_404(self, client: Any, store: FakeWorkflowStore) -> None:
        resp = client.post("/api/workflows/unknown/versions", headers=kong_headers())
        assert resp.status_code == 404

    def test_delete_other_tenants_workflow_is_400(self, client: Any, store: FakeWorkflowStore) -> None:
        resp = client.delete(f"/api/workflows/{WORKFLOW_ID}", headers=kong_headers(TENANT_B))
        assert resp.status_code == 400
        assert WORKFLOW_ID in store.workflow_agent


# Tenant B reaching tenant A's workflow objects by id. Workflows and versions have
# no tenant column for RLS to filter on, so each route asks the agent's owner and
# answers 404 for unknown and foreign ids alike.
@pytest.mark.parametrize(("method", "path", "body"), [
    ("POST", f"/api/agents/{AGENT_ID}/workflows", {"name": "planted"}),
    ("GET", f"/api/agents/{AGENT_ID}/workflows", None),
    ("GET", f"/api/workflows/{WORKFLOW_ID}/versions", None),
    ("POST", f"/api/workflows/{WORKFLOW_ID}/versions", None),
    ("POST", f"/api/workflow-versions/{VERSION_ID}/publish", None),
    ("POST", f"/api/workflow-versions/{ARCHIVED_ID}/republish", None),
])
def test_other_tenant_cannot_touch_workflows(
    client: Any, store: FakeWorkflowStore, method: str, path: str, body: dict[str, Any] | None
) -> None:
    resp = client.request(method, path, json=body, headers=kong_headers(TENANT_B))
    assert resp.status_code == 404
