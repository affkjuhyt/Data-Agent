"""The organisation's self-approval switch (GET and PATCH /v1/admin/organization) against
PostgreSQL: only an admin changes it, each change is audited with what it was and what it became,
and the next request already decides by the new value.

The organisation, its people and roles are test_org_admin's world.
"""

from __future__ import annotations

from typing import Any

import pytest

from . import test_org_admin as org_admin
from .test_org_admin import World, client

pytestmark = [pytest.mark.integration, pytest.mark.contract]

world = org_admin.world

URL = "/v1/admin/organization"


def _flag(world: World) -> bool:
    return bool(
        world.one("SELECT allow_self_approval FROM org.organization WHERE id = %s", world.org)
    )


def _audits(world: World) -> list[dict[str, Any]]:
    return [p for e, p in world.events(world.org) if e == "audit.requested"]


async def test_only_an_admin_turns_it_on(world: World) -> None:
    async with client(world) as api:
        read = await api.get(URL, headers=world.headers("steward"))
        turned = await api.patch(
            URL, headers=world.headers("steward"), json={"allow_self_approval": True}
        )
        # A Data Space role that names admin grants nothing organisation-wide.
        rogue = await api.patch(
            URL, headers=world.headers("rogue", world.space), json={"allow_self_approval": True}
        )
    # Anyone in the organisation may read it: a creator needs to know whether they can approve.
    assert read.status_code == 200, read.text
    assert read.json()["allow_self_approval"] is False
    assert turned.status_code == 403, turned.text
    assert rogue.status_code == 403, rogue.text
    assert _flag(world) is False
    assert _audits(world) == []


async def test_an_admin_turns_it_on_and_off_and_each_change_is_audited(world: World) -> None:
    async with client(world) as api:
        before = await api.get(URL, headers=world.headers("admin"))
        on = await api.patch(
            URL, headers=world.headers("admin"), json={"allow_self_approval": True}
        )
        again = await api.patch(
            URL, headers=world.headers("admin"), json={"allow_self_approval": True}
        )
        off = await api.patch(
            URL, headers=world.headers("admin"), json={"allow_self_approval": False}
        )
        missing = await api.patch(URL, headers=world.headers("admin"), json={})
    assert before.status_code == 200, before.text
    body = before.json()
    assert body["id"] == str(world.org) and body["allow_self_approval"] is False
    assert set(body) == {"id", "code", "name", "allow_self_approval"}
    assert on.status_code == 200, on.text
    assert on.json()["allow_self_approval"] is True
    assert again.status_code == 200 and again.json()["allow_self_approval"] is True
    assert off.status_code == 200, off.text
    assert off.json()["allow_self_approval"] is False
    assert missing.status_code == 422
    assert _flag(world) is False
    # Setting the value it already has changes nothing, so it is not a change to audit.
    audits = _audits(world)
    assert [(a["before"], a["after"]) for a in audits] == [
        ({"allow_self_approval": False}, {"allow_self_approval": True}),
        ({"allow_self_approval": True}, {"allow_self_approval": False}),
    ]
    assert {a["action"] for a in audits} == {"organization.updated"}
    assert {a["actor_id"] for a in audits} == {str(world.users["admin"])}
    assert {a["object_kind"] for a in audits} == {"organization"}


async def test_the_next_request_sees_the_new_value(world: World, self_approvals: Any) -> None:
    async with client(world) as api:
        sent = (await org_admin._request(api, world, "ask", who="steward")).json()
        refused = await org_admin._decide(api, world, sent["id"], decision="approve")
        on = await api.patch(
            URL, headers=world.headers("admin"), json={"allow_self_approval": True}
        )
        own = await org_admin._decide(api, world, sent["id"], decision="approve")
    assert refused.status_code == 403, refused.text
    assert on.status_code == 200, on.text
    assert own.status_code == 200, own.text
    assert own.json()["status"] == "approved"
    assert self_approvals(world.org) == ["access_request.decide"]
