"""Core API-03 workspace operations against PostgreSQL 16."""

from __future__ import annotations

import pytest

from .test_qa_query import World, build, client

pytestmark = [pytest.mark.integration, pytest.mark.contract]


@pytest.fixture
def workspace_world(seed) -> World:
    return build(seed, "-workspace")


async def test_workspace_and_tile_lifecycle(workspace_world: World) -> None:
    headers = {
        "Authorization": "Bearer kc-api-workspace",
        "X-Data-Space": str(workspace_world.space),
    }
    async with client(workspace_world) as api:
        created = await api.post(
            "/v1/workspaces", headers=headers, json={"title": "Báo cáo tháng"}
        )
        assert created.status_code == 201, created.text
        workspace_id = created.json()["id"]
        assert created.json()["tiles"] == []

        tile = await api.post(
            f"/v1/workspaces/{workspace_id}/tiles",
            headers=headers,
            json={"position": 0, "kind": "text", "title": "Ghi chú"},
        )
        assert tile.status_code == 201, tile.text
        tile_id = tile.json()["id"]

        detail = await api.get(f"/v1/workspaces/{workspace_id}", headers=headers)
        assert detail.status_code == 200, detail.text
        assert detail.json()["tile_count"] == 1
        assert detail.json()["tiles"][0]["id"] == tile_id

        patched = await api.patch(
            f"/v1/tiles/{tile_id}", headers=headers, json={"title": "Đã sửa"}
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["title"] == "Đã sửa"

        listed = await api.get("/v1/workspaces", headers=headers)
        assert listed.status_code == 200, listed.text
        assert listed.json()["items"][0]["id"] == workspace_id


async def test_workspace_isolation(workspace_world: World, seed) -> None:
    other = build(seed, "-workspace-other")
    headers = {
        "Authorization": "Bearer kc-api-workspace",
        "X-Data-Space": str(workspace_world.space),
    }
    async with client(workspace_world) as api:
        created = await api.post(
            "/v1/workspaces", headers=headers, json={"title": "Riêng tư"}
        )
        workspace_id = created.json()["id"]

    async with client(other) as other_api:
        response = await other_api.get(
            f"/v1/workspaces/{workspace_id}",
            headers={
                "Authorization": "Bearer kc-api-workspace-other",
                "X-Data-Space": str(other.space),
            },
        )
    assert response.status_code == 404, response.text
