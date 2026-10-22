"""Identity and capabilities come from org.* under RLS (tab 3b D4.1, A1.2, ADR-31)."""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from services.api.app import create_app
from services.api.config import Settings
from services.api.kernel.auth import DataSpaceNotFound
from services.api.modules.org import OrgIdentityResolver

from .conftest import Seed

pytestmark = [pytest.mark.integration, pytest.mark.security]

NOW = datetime.datetime.now(datetime.UTC)


@dataclass
class World:
    org: uuid.UUID
    alice: uuid.UUID
    bob: uuid.UUID
    oil: uuid.UUID
    gold: uuid.UUID
    steward_role: uuid.UUID


def build(seed: Seed) -> World:
    o = seed.org()

    def user(name: str, status: str = "active") -> uuid.UUID:
        return seed.row(
            "org.app_user",
            org_id=o,
            external_id=f"kc-{name}",
            username=name,
            display_name=name.title(),
            status=status,
        )

    def role(code: str, *caps: str) -> uuid.UUID:
        return seed.row(
            "org.role", org_id=o, code=code, name=f"Vai trò {code}", capabilities=list(caps)
        )

    alice, bob = user("alice"), user("bob")
    user("carol", status="disabled")
    ask, admin = role("AC01", "ask"), role("AC10", "admin")
    expired, disabled = role("AC20", "approve"), role("AC21", "compliance")
    steward = role("AC05", "steward", "admin_data")

    seed.row("org.user_role", org_id=o, user_id=alice, role_id=ask, source="manual")
    # Emergency access that has run out (BR-44) grants nothing.
    seed.row(
        "org.user_role",
        org_id=o,
        user_id=alice,
        role_id=expired,
        source="emergency",
        valid_until=NOW - datetime.timedelta(hours=1),
        approved_by=bob,
    )
    group = seed.row("org.user_group", org_id=o, external_id="cn=analysts", name="Analysts")
    seed.row("org.user_group_member", org_id=o, group_id=group, user_id=alice)
    seed.row("org.group_mapping", org_id=o, group_id=group, role_id=admin)
    seed.row("org.group_mapping", org_id=o, group_id=group, role_id=disabled, status="disabled")

    def space(code: str, **extra: Any) -> uuid.UUID:
        return seed.row("org.data_space", org_id=o, code=code, name=code, steward_id=alice, **extra)

    oil, gold = space("DS-OIL"), space("DS-GOLD")
    gone = space("DS-OLD", deleted_at=NOW)
    # Alice is in DS-OIL through her group, in DS-OLD directly (deleted space), not in DS-GOLD.
    seed.row(
        "org.data_space_member",
        org_id=o,
        data_space_id=oil,
        principal_type="group",
        principal_id=group,
        role_id=steward,
    )
    seed.row(
        "org.data_space_member",
        org_id=o,
        data_space_id=gone,
        principal_type="user",
        principal_id=alice,
        role_id=steward,
    )
    # Bob's membership in DS-GOLD has expired (BR-142).
    seed.row(
        "org.data_space_member",
        org_id=o,
        data_space_id=gold,
        principal_type="user",
        principal_id=bob,
        role_id=steward,
        source="access_request",
        valid_until=NOW - datetime.timedelta(days=1),
    )
    return World(org=o, alice=alice, bob=bob, oil=oil, gold=gold, steward_role=steward)


@pytest.fixture
def world(seed: Seed) -> World:
    return build(seed)


async def test_org_wide_capabilities(world: World) -> None:
    identity = await OrgIdentityResolver().resolve(
        org_id=world.org, external_id="kc-alice", data_space=None
    )
    assert identity is not None
    assert identity.user_id == world.alice
    # ask (direct) + admin (group); not approve (expired), not compliance (mapping disabled),
    # not steward (only inside DS-OIL).
    assert identity.capabilities == {"ask", "admin"}
    assert identity.data_space_id is None


@pytest.mark.parametrize("ref", ["DS-OIL", "id"])
async def test_data_space_roles_join_by_code_or_id(world: World, ref: str) -> None:
    header = str(world.oil) if ref == "id" else ref
    identity = await OrgIdentityResolver().resolve(
        org_id=world.org, external_id="kc-alice", data_space=header
    )
    assert identity is not None
    assert identity.capabilities == {"ask", "admin", "steward", "admin_data"}
    assert identity.data_space_id == world.oil


@pytest.mark.parametrize(
    ("user", "space"),
    [
        ("kc-alice", "DS-GOLD"),  # exists, not a member: an org-wide admin role does not open it
        ("kc-alice", "DS-OLD"),  # member, but the space is deleted
        ("kc-alice", "DS-NONE"),
        ("kc-bob", "DS-GOLD"),  # membership expired
    ],
)
async def test_naming_a_space_without_current_membership_is_refused(
    world: World, user: str, space: str
) -> None:
    with pytest.raises(DataSpaceNotFound):
        await OrgIdentityResolver().resolve(org_id=world.org, external_id=user, data_space=space)


async def test_user_without_roles_has_no_capabilities(world: World) -> None:
    identity = await OrgIdentityResolver().resolve(
        org_id=world.org, external_id="kc-bob", data_space=None
    )
    assert identity is not None and identity.capabilities == frozenset()


@pytest.mark.parametrize("external_id", ["kc-carol", "kc-nobody"])
async def test_disabled_or_unknown_user_is_not_resolved(world: World, external_id: str) -> None:
    assert (
        await OrgIdentityResolver().resolve(
            org_id=world.org, external_id=external_id, data_space=None
        )
        is None
    )


async def test_another_org_sees_none_of_it(world: World, seed: Seed) -> None:
    # Same Keycloak sub in another organisation: RLS keeps each org to its own rows.
    other = build(seed)
    mine = await OrgIdentityResolver().resolve(
        org_id=world.org, external_id="kc-alice", data_space=None
    )
    theirs = await OrgIdentityResolver().resolve(
        org_id=other.org, external_id="kc-alice", data_space=None
    )
    assert mine is not None and theirs is not None
    assert mine.user_id == world.alice and theirs.user_id == other.alice
    with pytest.raises(DataSpaceNotFound):
        await OrgIdentityResolver().resolve(
            org_id=world.org, external_id="kc-alice", data_space=str(other.oil)
        )


def _role(seed: Seed, world: World, code: str, *caps: str) -> uuid.UUID:
    return seed.row("org.role", org_id=world.org, code=code, name=code, capabilities=list(caps))


async def _caps(world: World, user: str, space: str | None = None) -> frozenset[str]:
    identity = await OrgIdentityResolver().resolve(
        org_id=world.org, external_id=user, data_space=space
    )
    assert identity is not None
    return identity.capabilities


async def test_space_role_cannot_grant_org_wide_capabilities(world: World, seed: Seed) -> None:
    # A steward of DS-GOLD gives Bob a role there that carries admin and compliance.
    # Naming DS-GOLD must add steward only: /v1/admin/* and audit checks stay closed.
    risky = _role(seed, world, "DS-ADMIN", "admin", "compliance", "audit_read", "steward")
    seed.row(
        "org.data_space_member",
        org_id=world.org,
        data_space_id=world.gold,
        principal_type="user",
        principal_id=world.bob,
        role_id=risky,
    )
    assert await _caps(world, "kc-bob", "DS-GOLD") == {"steward"}
    async with _client(world) as client:
        r = await client.get("/v1/data-spaces", headers={"Authorization": "Bearer kc-alice"})
    assert all("admin" not in s["my_capabilities"] for s in r.json()["items"])


async def test_role_from_a_disabled_group_mapping_is_revoked_at_once(
    world: World, seed: Seed
) -> None:
    # The sync has not yet removed the user_role rows materialised from the mappings.
    group = seed.row("org.user_group", org_id=world.org, external_id="cn=ops", name="Ops")
    live_role, dead_role = (
        _role(seed, world, "OPS", "run_agent"),
        _role(seed, world, "OLD", "approve"),
    )
    live = seed.row("org.group_mapping", org_id=world.org, group_id=group, role_id=live_role)
    dead = seed.row(
        "org.group_mapping", org_id=world.org, group_id=group, role_id=dead_role, status="disabled"
    )
    for role, mapping in ((live_role, live), (dead_role, dead)):
        seed.row(
            "org.user_role",
            org_id=world.org,
            user_id=world.bob,
            role_id=role,
            source="group_mapping",
            source_ref=mapping,
        )
    assert await _caps(world, "kc-bob") == {"run_agent"}
    async with _client(world) as client:
        r = await client.get("/v1/me", headers={"Authorization": "Bearer kc-bob"})
    assert [g["role"]["code"] for g in r.json()["roles"]] == ["OPS"]


async def test_suspended_organisation_resolves_nobody(world: World, seed: Seed) -> None:
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE org.organization SET status = 'suspended' WHERE id = %s", (str(world.org),)
        )
    resolved = await OrgIdentityResolver().resolve(
        org_id=world.org, external_id="kc-alice", data_space=None
    )
    assert resolved is None


async def test_archived_space_cannot_be_named(world: World, seed: Seed) -> None:
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE org.data_space SET status = 'archived' WHERE id = %s", (str(world.oil),)
        )
    with pytest.raises(DataSpaceNotFound):
        await OrgIdentityResolver().resolve(
            org_id=world.org, external_id="kc-alice", data_space="DS-OIL"
        )


class _Tokens:
    def __init__(self, org_id: uuid.UUID) -> None:
        self.org_id = org_id

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.org_id)}


def _client(world: World) -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=_Tokens(world.org))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


async def test_list_my_data_spaces(world: World) -> None:
    async with _client(world) as client:
        r = await client.get("/v1/data-spaces", headers={"Authorization": "Bearer kc-alice"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [s["code"] for s in body["items"]] == ["DS-OIL"]
    item = body["items"][0]
    assert item["my_roles"] == [
        {"id": str(world.steward_role), "code": "AC05", "name": "Vai trò AC05"}
    ]
    assert item["my_capabilities"] == ["admin_data", "steward"]
    assert body["next_page_token"] == ""


async def test_any_signed_in_user_lists_only_current_memberships(world: World) -> None:
    # A login is enough since d89c5000 (the Data Space picker comes before any capability); what a
    # user sees is still only where they are a member now: Bob's DS-GOLD membership has expired.
    async with _client(world) as client:
        r = await client.get("/v1/data-spaces", headers={"Authorization": "Bearer kc-bob"})
    assert r.status_code == 200
    assert r.json()["items"] == []


async def test_list_pages_by_cursor(world: World, seed: Seed) -> None:
    for code in ("DS-A", "DS-B", "DS-C"):
        space = seed.row(
            "org.data_space", org_id=world.org, code=code, name=code, steward_id=world.alice
        )
        seed.row(
            "org.data_space_member",
            org_id=world.org,
            data_space_id=space,
            principal_type="user",
            principal_id=world.alice,
            role_id=world.steward_role,
        )
    seen: list[str] = []
    token = ""
    async with _client(world) as client:
        while True:
            r = await client.get(
                "/v1/data-spaces",
                params={"page_size": 2, "page_token": token},
                headers={"Authorization": "Bearer kc-alice"},
            )
            body = r.json()
            seen += [s["code"] for s in body["items"]]
            token = body["next_page_token"]
            if not token:
                break
    assert seen == ["DS-A", "DS-B", "DS-C", "DS-OIL"]


async def test_me_lists_where_each_role_comes_from(world: World) -> None:
    async with _client(world) as client:
        r = await client.get("/v1/me", headers={"Authorization": "Bearer kc-alice"})
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["id"] == str(world.alice)
    assert (me["username"], me["locale"], me["time_zone"]) == ("alice", "vi-VN", "Asia/Ho_Chi_Minh")
    # The expired emergency role and the disabled mapping are not listed.
    assert [(g["role"]["code"], g["source"], g["group_name"]) for g in me["roles"]] == [
        ("AC01", "manual", None),
        ("AC10", "group_mapping", "Analysts"),
    ]
    assert me["capabilities"] == ["admin", "ask"]


async def test_me_needs_no_capability(world: World) -> None:
    # A2 "Quyền" is "—": any signed-in user of the organisation, even one with no role.
    async with _client(world) as client:
        r = await client.get("/v1/me", headers={"Authorization": "Bearer kc-bob"})
    assert r.status_code == 200
    assert r.json()["roles"] == [] and r.json()["capabilities"] == []
