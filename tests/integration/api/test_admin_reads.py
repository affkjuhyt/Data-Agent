"""Quản trị reads added on 12/10/2026, against PostgreSQL: the principal picker (UC-02-03,
UC-11-08), roles with their capabilities (UC-11-01) and a Data Space's members (UC-11-08)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from psycopg2.extras import Json

from services.api.app import create_app
from services.api.config import Settings

from .conftest import Seed

pytestmark = [pytest.mark.integration, pytest.mark.contract]


@dataclass
class World:
    org: uuid.UUID
    space: uuid.UUID
    other_space: uuid.UUID
    foreign_space: uuid.UUID
    users: dict[str, uuid.UUID]
    roles: dict[str, uuid.UUID]
    group: uuid.UUID
    suffix: str

    def headers(self, who: str, space: uuid.UUID | None = None) -> dict[str, str]:
        out = {"Authorization": f"Bearer kc-{who}-{self.suffix}"}
        if space is not None:
            out["X-Data-Space"] = str(space)
        return out


def _user(seed: Seed, org: uuid.UUID, name: str, suffix: str, **extra: Any) -> uuid.UUID:
    return seed.row(
        "org.app_user",
        org_id=org,
        external_id=f"kc-{name}-{suffix}",
        username=f"{name}-{suffix}",
        display_name=extra.pop("display_name", name.title()),
        email=f"{name}@bank.example",
        attributes=Json({"branch_code": "HN01"}),
        status=extra.pop("status", "active"),
        **extra,
    )


@pytest.fixture
def world(seed: Seed) -> World:
    suffix = uuid.uuid4().hex[:8]
    org = seed.org()

    def role(code: str, *caps: str) -> uuid.UUID:
        return seed.row("org.role", org_id=org, code=code, name=code, capabilities=list(caps))

    roles = {
        "admin": role("AC05", "ask", "admin"),
        "steward": role("AC03", "ask", "steward"),
        "analyst": role("AC01", "ask"),
    }
    users = {
        name: _user(seed, org, name, suffix) for name in ("admin", "steward", "analyst", "nobody")
    }
    users["linh"] = _user(seed, org, "linh", suffix, display_name="Nguyen Linh")
    users["linh_left"] = _user(
        seed, org, "linhleft", suffix, display_name="Tran Linh", status="left"
    )
    users["linh_bot"] = _user(
        seed,
        org,
        "linhbot",
        suffix,
        display_name="Linh Bot",
        kind="agent",
        owner_user_id=users["linh"],
    )
    users["linh_service"] = _user(
        seed, org, "linhsvc", suffix, display_name="Linh Service", kind="service"
    )
    for name, r in (("admin", "admin"), ("analyst", "analyst"), ("steward", "analyst")):
        seed.row(
            "org.user_role", org_id=org, user_id=users[name], role_id=roles[r], source="manual"
        )
    group = seed.row(
        "org.user_group",
        org_id=org,
        external_id=f"g-{suffix}",
        name="GRP-LINH-ANALYSTS",
        source="ad",
    )
    space = seed.row(
        "org.data_space", org_id=org, code=f"DS-{suffix}", name="Risk", steward_id=users["steward"]
    )
    other_space = seed.row(
        "org.data_space",
        org_id=org,
        code=f"DS2-{suffix}",
        name="Other",
        steward_id=users["steward"],
    )
    for s in (space, other_space):
        seed.row(
            "org.data_space_member",
            org_id=org,
            data_space_id=s,
            principal_type="user",
            principal_id=users["steward"],
            role_id=roles["steward"],
        )
    seed.row(
        "org.data_space_member",
        org_id=org,
        data_space_id=space,
        principal_type="group",
        principal_id=group,
        role_id=roles["analyst"],
    )
    seed.row(
        "org.data_space_member",
        org_id=org,
        data_space_id=space,
        principal_type="agent",
        principal_id=users["linh_bot"],
        role_id=roles["analyst"],
    )
    seed.row(
        "org.data_space_member",
        org_id=org,
        data_space_id=other_space,
        principal_type="user",
        principal_id=users["analyst"],
        role_id=roles["analyst"],
    )

    # Another organisation with look-alike names: never visible.
    foreign = seed.org()
    stranger = seed.row(
        "org.app_user",
        org_id=foreign,
        external_id=f"kc-stranger-{suffix}",
        username=f"stranger-{suffix}",
        display_name="Linh Foreign",
        status="active",
    )
    seed.row(
        "org.user_group",
        org_id=foreign,
        external_id=f"fg-{suffix}",
        name="GRP-LINH-FOREIGN",
        source="ad",
    )
    foreign_space = seed.row(
        "org.data_space", org_id=foreign, code=f"DSF-{suffix}", name="Foreign", steward_id=stranger
    )
    return World(
        org=org,
        space=space,
        other_space=other_space,
        foreign_space=foreign_space,
        users=users,
        roles=roles,
        group=group,
        suffix=suffix,
    )


class Tokens:
    def __init__(self, world: World) -> None:
        self.world = world

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.world.org)}


def client(world: World) -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=Tokens(world))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


async def test_principals_are_ids_kinds_and_names_of_active_principals(world: World) -> None:
    async with client(world) as api:
        found = await api.get("/v1/principals?q=linh", headers=world.headers("analyst"))
        assert found.status_code == 200, found.text
        items = found.json()["items"]
        assert {(i["id"], i["kind"], i["display_name"]) for i in items} == {
            (str(world.users["linh"]), "user", "Nguyen Linh"),
            (str(world.users["linh_bot"]), "agent", "Linh Bot"),
            (str(world.group), "group", "GRP-LINH-ANALYSTS"),
        }
        # Nothing personal: no email, username, department or attributes.
        assert all(set(i) == {"id", "kind", "display_name"} for i in items)
        assert "bank.example" not in found.text

        groups = await api.get("/v1/principals?q=LINH&kind=group", headers=world.headers("analyst"))
        assert [i["id"] for i in groups.json()["items"]] == [str(world.group)]
        agents = await api.get("/v1/principals?q=linh&kind=agent", headers=world.headers("analyst"))
        assert [i["id"] for i in agents.json()["items"]] == [str(world.users["linh_bot"])]

        # The text is matched literally and only on the display name.
        assert (await api.get("/v1/principals?q=%25", headers=world.headers("analyst"))).json()[
            "items"
        ] == []
        by_email = await api.get("/v1/principals?q=bank.example", headers=world.headers("analyst"))
        assert by_email.json()["items"] == []

        missing = await api.get("/v1/principals", headers=world.headers("analyst"))
        assert missing.status_code == 422, missing.text
        bad_kind = await api.get(
            "/v1/principals?q=a&kind=service", headers=world.headers("analyst")
        )
        assert bad_kind.status_code == 422, bad_kind.text

        # No org-wide `ask`: 403.
        denied = await api.get("/v1/principals?q=linh", headers=world.headers("nobody"))
        assert denied.status_code == 403, denied.text


async def test_roles_list_their_capabilities_for_admins_only(world: World) -> None:
    async with client(world) as api:
        roles = await api.get("/v1/admin/roles", headers=world.headers("admin"))
        assert roles.status_code == 200, roles.text
        by_code = {r["code"]: r for r in roles.json()["items"]}
        assert set(by_code) == {"AC01", "AC03", "AC05"}
        assert by_code["AC05"]["capabilities"] == ["admin", "ask"]
        assert by_code["AC05"]["org_wide"] is True
        assert by_code["AC03"]["org_wide"] is False
        assert by_code["AC03"]["id"] == str(world.roles["steward"])

        denied = await api.get("/v1/admin/roles", headers=world.headers("analyst"))
        assert denied.status_code == 403, denied.text


async def test_steward_lists_the_members_of_their_space(world: World) -> None:
    async with client(world) as api:
        members = await api.get(
            f"/v1/data-spaces/{world.space}/members?page_size=2",
            headers=world.headers("steward", world.space),
        )
        assert members.status_code == 200, members.text
        first = members.json()
        assert len(first["items"]) == 2
        assert first["next_page_token"]
        rest = await api.get(
            f"/v1/data-spaces/{world.space}/members?page_size=2&cursor={first['next_page_token']}",
            headers=world.headers("steward", world.space),
        )
        assert rest.status_code == 200, rest.text
        rows = first["items"] + rest.json()["items"]
        assert rest.json()["next_page_token"] == ""
        assert {(r["principal_type"], r["principal_id"], r["principal_name"]) for r in rows} == {
            ("user", str(world.users["steward"]), "Steward"),
            ("group", str(world.group), "GRP-LINH-ANALYSTS"),
            ("agent", str(world.users["linh_bot"]), "Linh Bot"),
        }
        assert all(r["data_space_id"] == str(world.space) for r in rows)
        steward_row = next(r for r in rows if r["principal_type"] == "user")
        assert (steward_row["role_code"], steward_row["role_id"]) == (
            "AC03",
            str(world.roles["steward"]),
        )

        # Another space of the organisation than the one in X-Data-Space: 404.
        elsewhere = await api.get(
            f"/v1/data-spaces/{world.other_space}/members",
            headers=world.headers("steward", world.space),
        )
        assert elsewhere.status_code == 404, elsewhere.text
        # Another organisation's space: 404 as if it did not exist.
        foreign = await api.get(
            f"/v1/data-spaces/{world.foreign_space}/members",
            headers=world.headers("steward", world.foreign_space),
        )
        assert foreign.status_code == 404, foreign.text
        # A member without `steward` in the space: 403.
        denied = await api.get(
            f"/v1/data-spaces/{world.other_space}/members",
            headers=world.headers("analyst", world.other_space),
        )
        assert denied.status_code == 403, denied.text
