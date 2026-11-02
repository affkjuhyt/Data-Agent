"""The organisation's self-approval switch (GET and PATCH /v1/admin/organization) against
PostgreSQL: only an admin changes it, each change is audited with what it was and what it became,
and the next request already decides by the new value.

The organisation, its people and roles are test_org_admin's world.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from services.api.app import create_app
from services.api.config import Settings

from . import test_org_admin as org_admin
from .test_org_admin import Tokens, World, client

pytestmark = [pytest.mark.integration, pytest.mark.contract]

world = org_admin.world

URL = "/v1/admin/organization"


def _flag(world: World) -> bool:
    return bool(
        world.one("SELECT allow_self_approval FROM org.organization WHERE id = %s", world.org)
    )


def _audits(audit_log: Any, world: World) -> list[dict[str, Any]]:
    return audit_log(world.org, "organization.updated")


class _DelegatedTokens(Tokens):
    """A token "<user>#agent" is an agent acting for that user through token exchange."""

    def claims(self, token: str) -> dict[str, Any]:
        user, _, actor = token.partition("#")
        out = super().claims(user)
        if actor:
            out["act"] = {"sub": actor}
        return out


def _delegated_client(world: World) -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=_DelegatedTokens(world))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


async def test_only_an_admin_turns_it_on(world: World, audit_log: Any) -> None:
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
    assert _audits(audit_log, world) == []


async def test_an_agent_acting_for_an_admin_does_not_turn_it(world: World, audit_log: Any) -> None:
    # The switch decides who may approve; a client acting for the admin may not widen that.
    async with _delegated_client(world) as api:
        delegated = await api.patch(
            URL, headers=world.headers("admin#agent"), json={"allow_self_approval": True}
        )
        read = await api.get(URL, headers=world.headers("admin#agent"))
    assert delegated.status_code == 403, delegated.text
    assert read.status_code == 200, read.text
    assert _flag(world) is False
    assert _audits(audit_log, world) == []


async def test_an_admin_turns_it_on_and_off_and_each_change_is_audited(
    world: World, audit_log: Any
) -> None:
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
    # Setting the value it already has changes nothing, so it is not a change to audit. The rows
    # are in the audit log itself, not only requested through an outbox nobody reads.
    audits = _audits(audit_log, world)
    assert [(a["before"], a["after"]) for a in audits] == [
        ({"allow_self_approval": False}, {"allow_self_approval": True}),
        ({"allow_self_approval": True}, {"allow_self_approval": False}),
    ]
    assert {a["action"] for a in audits} == {"organization.updated"}
    assert {a["actor_id"] for a in audits} == {str(world.users["admin"])}
    assert {a["object_kind"] for a in audits} == {"organization"}
    assert {a["object_id"] for a in audits} == {str(world.org)}
    assert {a["actor_kind"] for a in audits} == {"user"}


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
