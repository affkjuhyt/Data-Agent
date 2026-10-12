"""A1.5 ETag and If-Match, shown on a workspace (GET and PATCH /v1/workspaces/{id}): the kernel
tags every read and refuses a write sent against a version that has since changed."""

from __future__ import annotations

import pytest

from .conftest import Seed
from .test_workspace import Team, _api, _as, _team, _workspace

pytestmark = [pytest.mark.integration]


@pytest.fixture
def team(seed: Seed) -> Team:
    return _team(seed)


async def test_a1_5_a_read_carries_an_etag_that_changes_with_the_resource(team: Team) -> None:
    owner = _as(team, "owner")
    async with _api(team) as api:
        workspace_id = await _workspace(api, team)
        first = await api.get(f"/v1/workspaces/{workspace_id}", headers=owner)
        same = await api.get(f"/v1/workspaces/{workspace_id}", headers=owner)
        await api.patch(f"/v1/workspaces/{workspace_id}", headers=owner, json={"title": "Đổi"})
        after = await api.get(f"/v1/workspaces/{workspace_id}", headers=owner)
    assert first.headers["ETag"] == same.headers["ETag"]
    assert after.headers["ETag"] != first.headers["ETag"]


async def test_a1_5_a_stale_if_match_is_412_and_changes_nothing(team: Team) -> None:
    owner = _as(team, "owner")
    async with _api(team) as api:
        workspace_id = await _workspace(api, team)
        seen = (await api.get(f"/v1/workspaces/{workspace_id}", headers=owner)).headers["ETag"]
        # Someone else's edit lands between this caller's read and write.
        await api.patch(f"/v1/workspaces/{workspace_id}", headers=owner, json={"title": "Của B"})
        stale = await api.patch(
            f"/v1/workspaces/{workspace_id}",
            headers={**owner, "If-Match": seen},
            json={"title": "Của A"},
        )
        current = await api.get(f"/v1/workspaces/{workspace_id}", headers=owner)
        fresh = await api.patch(
            f"/v1/workspaces/{workspace_id}",
            headers={**owner, "If-Match": current.headers["ETag"]},
            json={"title": "Của A"},
        )
    assert stale.status_code == 412 and stale.json()["code"] == "precondition_failed"
    assert current.json()["title"] == "Của B"
    assert fresh.status_code == 200 and fresh.json()["title"] == "Của A"
