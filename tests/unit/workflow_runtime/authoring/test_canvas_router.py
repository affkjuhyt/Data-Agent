"""workflow_runtime routers/canvas.py: canvas load/save guarded by version ownership.

The canvas lives in Mongo, which has no row-level security, so the router's own
ownership check (`workflow_version_belongs_to`) is the only thing keeping one
tenant from reading or overwriting another's canvas. The fakes model Mongo as a
dict and the ownership lookup as a version -> tenant map.
"""
from __future__ import annotations

import types
from typing import Any

import pytest

from .conftest import TENANT_A, TENANT_B, kong_headers, route_table

pytestmark = pytest.mark.unit

VERSION_ID = "bbbbbbbb-0000-0000-0000-000000000001"


class FakeCanvasStore:
    def __init__(self) -> None:
        self.owner: dict[str, str] = {}
        self.canvases: dict[str, dict[str, list[Any]]] = {}

    async def workflow_version_belongs_to(self, version_id: str, tenant_id: str) -> bool:
        return self.owner.get(version_id) == tenant_id

    async def save_canvas(self, version_id: str, nodes: list[Any], edges: list[Any]) -> None:
        self.canvases[version_id] = {"nodes": nodes, "edges": edges}

    async def load_canvas(self, version_id: str) -> dict[str, list[Any]]:
        return self.canvases.get(version_id, {"nodes": [], "edges": []})


@pytest.fixture
def store(fb: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> FakeCanvasStore:
    fake = FakeCanvasStore()
    fake.owner[VERSION_ID] = TENANT_A
    for name in ("workflow_version_belongs_to", "save_canvas", "load_canvas"):
        monkeypatch.setattr(fb.canvas, name, getattr(fake, name))
    return fake


CANVAS = {"nodes": [{"id": "n1", "type": "llm"}], "edges": [{"source": "n1", "target": "n2"}]}
URL = f"/api/workflow-versions/{VERSION_ID}/canvas"


def test_routes_registered(fb: types.SimpleNamespace) -> None:
    expected = {
        ("POST", "/api/workflow-versions/{workflow_version_id}/canvas"),
        ("GET", "/api/workflow-versions/{workflow_version_id}/canvas"),
    }
    assert route_table(fb.canvas.router) == expected
    assert expected <= route_table(fb.app_module.create_app())


def test_unauthenticated_load_is_401(client: Any, store: FakeCanvasStore) -> None:
    store.canvases[VERSION_ID] = CANVAS
    resp = client.get(URL, headers={"X-Tenant-Id": TENANT_A, "X-Kong-Verified": "true"})
    assert resp.status_code == 401


def test_caller_without_tenant_is_403(client: Any, store: FakeCanvasStore) -> None:
    resp = client.get(URL, headers=kong_headers(tenant_id=""))
    assert resp.status_code == 403


def test_owner_saves_then_loads(client: Any, store: FakeCanvasStore) -> None:
    saved = client.post(URL, json=CANVAS, headers=kong_headers(TENANT_A))
    assert saved.status_code == 200
    assert saved.json() == {"saved": True, "workflow_version_id": VERSION_ID}

    loaded = client.get(URL, headers=kong_headers(TENANT_A))
    assert loaded.status_code == 200
    assert loaded.json() == CANVAS


def test_other_tenant_cannot_read(client: Any, store: FakeCanvasStore) -> None:
    store.canvases[VERSION_ID] = CANVAS
    resp = client.get(URL, headers=kong_headers(TENANT_B))
    # 404 rather than 403: a 403 would confirm the id exists.
    assert resp.status_code == 404
    assert "nodes" not in resp.text


def test_other_tenant_cannot_overwrite(client: Any, store: FakeCanvasStore) -> None:
    store.canvases[VERSION_ID] = CANVAS
    resp = client.post(URL, json={"nodes": [], "edges": []}, headers=kong_headers(TENANT_B))
    assert resp.status_code == 404
    assert store.canvases[VERSION_ID] == CANVAS


def test_unknown_version_is_404(client: Any, store: FakeCanvasStore) -> None:
    resp = client.get("/api/workflow-versions/does-not-exist/canvas", headers=kong_headers(TENANT_A))
    assert resp.status_code == 404


def test_payload_without_edges_is_422(client: Any, store: FakeCanvasStore) -> None:
    resp = client.post(URL, json={"nodes": []}, headers=kong_headers(TENANT_A))
    assert resp.status_code == 422
    assert VERSION_ID not in store.canvases


def test_store_failure_is_500(
    client: Any, store: FakeCanvasStore, fb: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(version_id: str) -> dict[str, Any]:
        raise RuntimeError("mongo unreachable")

    monkeypatch.setattr(fb.canvas, "load_canvas", broken)
    resp = client.get(URL, headers=kong_headers(TENANT_A))
    assert resp.status_code == 500
