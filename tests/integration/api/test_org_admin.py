"""Quản trị over org.* against PostgreSQL: users and where each role comes from, group mappings,
emergency access (UC-11-01), Data Spaces, members and sources (UC-11-08, UC-04-04), access
requests (UC-11-07) and work calendars (UC-02-04).

Each test builds its own organisation: an admin (AC05), a compliance officer (AC06), a steward
(AC03) and an analyst in one active Data Space, a plain colleague, and a second organisation for
isolation.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from services.api.app import create_app
from services.api.config import Settings

from .conftest import Seed

pytestmark = [pytest.mark.integration, pytest.mark.contract]

NOW = datetime.datetime.now(datetime.UTC)
TODAY = NOW.date()


@dataclass
class World:
    org: uuid.UUID
    other_org: uuid.UUID
    space: uuid.UUID
    other_space: uuid.UUID
    foreign_space: uuid.UUID
    users: dict[str, uuid.UUID]
    roles: dict[str, uuid.UUID]
    group: uuid.UUID
    calendar: uuid.UUID
    foreign_calendar: uuid.UUID
    seed: Seed

    def headers(self, who: str, space: uuid.UUID | None = None) -> dict[str, str]:
        out = {"Authorization": f"Bearer kc-{who}"}
        if space is not None:
            out["X-Data-Space"] = str(space)
        return out

    def events(self, aggregate_id: object) -> list[tuple[str, dict[str, Any]]]:
        with self.seed.conn.cursor() as cur:
            cur.execute(
                "SELECT event_type, payload FROM org.outbox WHERE aggregate_id = %s"
                " ORDER BY created_at, id",
                (str(aggregate_id),),
            )
            return list(cur.fetchall())

    def event_types(self, aggregate_id: object) -> list[str]:
        return [e for e, _ in self.events(aggregate_id)]

    def one(self, sql: str, *params: object) -> Any:
        with self.seed.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])
            row = cur.fetchone()
            return row[0] if row else None

    def all(self, sql: str, *params: object) -> list[tuple[Any, ...]]:
        with self.seed.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])
            return list(cur.fetchall())


def _user(seed: Seed, org: uuid.UUID, name: str, **extra: Any) -> uuid.UUID:
    return seed.row(
        "org.app_user",
        org_id=org,
        external_id=f"kc-{name}",
        username=name,
        display_name=name.title(),
        status="active",
        **extra,
    )


def _member(seed: Seed, org: uuid.UUID, space: uuid.UUID, user: uuid.UUID, role: uuid.UUID) -> None:
    seed.row(
        "org.data_space_member",
        org_id=org,
        data_space_id=space,
        principal_type="user",
        principal_id=user,
        role_id=role,
    )


@pytest.fixture
def world(seed: Seed) -> World:
    org = seed.org()

    def role(code: str, *caps: str) -> uuid.UUID:
        return seed.row("org.role", org_id=org, code=code, name=code, capabilities=list(caps))

    roles = {
        "admin": role("AC05", "ask", "admin"),
        "compliance": role("AC06", "ask", "compliance"),
        "steward": role("AC03", "ask", "steward", "approve", "admin_data"),
        "analyst": role("AC01", "ask"),
        "detail": role("DETAIL", "view_detail"),
        "agent": role("RUNNER", "run_agent"),
        # A Data Space role carrying an organisation-wide capability: it must grant nothing.
        "space_admin": role("SPACE-ADMIN", "ask", "admin", "compliance", "steward"),
    }
    users = {name: _user(seed, org, name) for name in ("admin", "officer", "steward")}
    users |= {name: _user(seed, org, name) for name in ("analyst", "helper", "rogue", "colleague")}
    for name in ("admin", "officer"):
        seed.row(
            "org.user_role",
            org_id=org,
            user_id=users[name],
            role_id=roles["admin" if name == "admin" else "compliance"],
            source="manual",
        )
    calendar = seed.row(
        "org.work_calendar", org_id=org, code="VN", name="Lịch Việt Nam", is_default=True
    )
    seed.row(
        "org.work_calendar_day",
        org_id=org,
        calendar_id=calendar,
        day=datetime.date(2026, 9, 2),
        kind="holiday",
        name="Quốc khánh",
    )
    space = seed.row(
        "org.data_space",
        org_id=org,
        code="DS-RISK",
        name="Rủi ro tín dụng",
        steward_id=users["steward"],
        backup_steward_id=users["helper"],
        status="active",
    )
    other_space = seed.row(
        "org.data_space",
        org_id=org,
        code="DS-MKT",
        name="Thị trường",
        steward_id=users["admin"],
        status="active",
    )
    _member(seed, org, space, users["steward"], roles["steward"])
    _member(seed, org, space, users["analyst"], roles["analyst"])
    _member(seed, org, space, users["rogue"], roles["space_admin"])
    _member(seed, org, other_space, users["steward"], roles["steward"])
    group = seed.row("org.user_group", org_id=org, external_id="cn=GG-RRTD", name="GG-RRTD-Analyst")
    for name in ("colleague", "helper"):
        seed.row("org.user_group_member", org_id=org, group_id=group, user_id=users[name])

    other_org = seed.org()
    stranger = _user(seed, other_org, "stranger")
    users["stranger"] = stranger
    foreign_space = seed.row(
        "org.data_space",
        org_id=other_org,
        code="DS-RISK",
        name="Rủi ro khác",
        steward_id=stranger,
        status="active",
    )
    foreign_calendar = seed.row(
        "org.work_calendar", org_id=other_org, code="VN", name="Khác", is_default=True
    )
    return World(
        org,
        other_org,
        space,
        other_space,
        foreign_space,
        users,
        roles,
        group,
        calendar,
        foreign_calendar,
        seed,
    )


class Tokens:
    def __init__(self, world: World) -> None:
        self.world = world

    def claims(self, token: str) -> dict[str, Any]:
        org = self.world.other_org if token == "kc-stranger" else self.world.org
        return {"sub": token, "org_id": str(org)}


def client(world: World) -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=Tokens(world))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


# ── users ──


async def test_users_list_effective_roles_per_space(world: World) -> None:
    async with client(world) as api:
        listed = await api.get("/v1/admin/users", headers=world.headers("admin"))
        assert listed.status_code == 200, listed.text
        by_name = {u["username"]: u for u in listed.json()["items"]}
        assert "stranger" not in by_name
        assert [r["code"] for r in by_name["admin"]["org_roles"]] == ["AC05"]
        steward = by_name["steward"]
        assert steward["org_roles"] == []
        assert {s["data_space_code"] for s in steward["data_space_roles"]} == {"DS-RISK", "DS-MKT"}

        searched = await api.get("/v1/admin/users?q=stew", headers=world.headers("admin"))
        assert [u["username"] for u in searched.json()["items"]] == ["steward"]
        in_group = await api.get(
            "/v1/admin/users?group=GG-RRTD-Analyst", headers=world.headers("admin")
        )
        assert {u["username"] for u in in_group.json()["items"]} == {"colleague", "helper"}
        first = await api.get("/v1/admin/users?page_size=2", headers=world.headers("admin"))
        token = first.json()["next_page_token"]
        assert token and len(first.json()["items"]) == 2
        second = await api.get(
            f"/v1/admin/users?page_size=2&page_token={token}", headers=world.headers("admin")
        )
        assert not {u["id"] for u in first.json()["items"]} & {
            u["id"] for u in second.json()["items"]
        }

        denied = await api.get("/v1/admin/users", headers=world.headers("steward"))
        assert denied.status_code == 403
        # A space role naming `admin` does not open the admin screens.
        rogue = await api.get("/v1/admin/users", headers=world.headers("rogue", world.space))
        assert rogue.status_code == 403


async def test_user_detail_names_the_origin_of_each_role(world: World) -> None:
    async with client(world) as api:
        detail = await api.get(
            f"/v1/admin/users/{world.users['steward']}", headers=world.headers("admin")
        )
        assert detail.status_code == 200, detail.text
        grants = detail.json()["grants"]
        assert {(g["role"]["code"], g["data_space_id"], g["source"]) for g in grants} == {
            ("AC03", str(world.space), "manual"),
            ("AC03", str(world.other_space), "manual"),
        }
        helper = await api.get(
            f"/v1/admin/users/{world.users['helper']}", headers=world.headers("admin")
        )
        assert [g["name"] for g in helper.json()["groups"]] == ["GG-RRTD-Analyst"]

        foreign = await api.get(
            f"/v1/admin/users/{world.users['stranger']}", headers=world.headers("admin")
        )
        assert foreign.status_code == 404
        denied = await api.get(
            f"/v1/admin/users/{world.users['steward']}", headers=world.headers("officer")
        )
        assert denied.status_code == 403


# ── group mappings ──


# AC-11-01-01
@pytest.mark.req("AC-11-01-01")
async def test_group_mapping_is_previewed_then_applied(world: World) -> None:
    mapping = {
        "group_id": str(world.group),
        "role_id": str(world.roles["analyst"]),
        "data_space_id": str(world.space),
        "row_scope": {"region": "north"},
    }
    org_wide = {"group_id": str(world.group), "role_id": str(world.roles["analyst"])}
    async with client(world) as api:
        preview = await api.put(
            "/v1/admin/group-mappings",
            json={"mappings": [mapping, org_wide], "preview": True},
            headers=world.headers("admin"),
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["applied"] is False
        assert set(preview.json()["users_added"]) == {
            str(world.users["colleague"]),
            str(world.users["helper"]),
        }
        assert preview.json()["users_removed"] == []
        assert world.one("SELECT count(*) FROM org.group_mapping WHERE org_id = %s", world.org) == 0

        applied = await api.put(
            "/v1/admin/group-mappings",
            json={"mappings": [mapping, org_wide]},
            headers=world.headers("admin"),
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["applied"] is True
        items = applied.json()["items"]
        assert {(i["data_space_id"], i["status"]) for i in items} == {
            (str(world.space), "active"),
            (None, "active"),
        }
        in_space = next(i for i in items if i["data_space_id"])
        assert sorted(world.event_types(in_space["id"])) == [
            "audit.requested",
            "group_mapping.changed",
        ]
        payload = dict(world.events(in_space["id"]))["group_mapping.changed"]
        assert payload["group_id"] == str(world.group) and payload["status"] == "active"
        # One user_role row per member for the org-wide mapping, one group row in the space.
        assert (
            world.one(
                "SELECT count(*) FROM org.user_role WHERE org_id = %s AND source = 'group_mapping'",
                world.org,
            )
            == 2
        )
        assert world.all(
            "SELECT principal_type, row_scope->>'region' FROM org.data_space_member"
            " WHERE data_space_id = %s AND source = 'group_mapping'",
            world.space,
        ) == [("group", "north")]

        helper = await api.get(
            f"/v1/admin/users/{world.users['helper']}", headers=world.headers("admin")
        )
        from_group = [g for g in helper.json()["grants"] if g["source"] == "group_mapping"]
        assert {g["group"]["name"] for g in from_group} == {"GG-RRTD-Analyst"}
        assert {g["data_space_id"] for g in from_group} == {str(world.space), None}

        # Leaving a mapping out of the set disables it and takes its grants away.
        cleared = await api.put(
            "/v1/admin/group-mappings", json={"mappings": []}, headers=world.headers("admin")
        )
        assert cleared.status_code == 200, cleared.text
        assert {i["status"] for i in cleared.json()["items"]} == {"disabled"}
        assert set(cleared.json()["users_removed"]) == {
            str(world.users["colleague"]),
            str(world.users["helper"]),
        }
        assert (
            world.one(
                "SELECT count(*) FROM org.user_role WHERE org_id = %s AND source = 'group_mapping'",
                world.org,
            )
            == 0
        )
        assert (
            world.one(
                "SELECT count(*) FROM org.data_space_member WHERE data_space_id = %s"
                " AND source = 'group_mapping'",
                world.space,
            )
            == 0
        )


# AC-11-01-06
@pytest.mark.req("AC-11-01-06")
async def test_a_mapping_making_an_approver_a_controller_is_refused(world: World) -> None:
    world.seed.row(
        "org.user_group_member",
        org_id=world.org,
        group_id=world.group,
        user_id=world.users["steward"],
    )
    async with client(world) as api:
        refused = await api.put(
            "/v1/admin/group-mappings",
            json={
                "mappings": [
                    {"group_id": str(world.group), "role_id": str(world.roles["compliance"])}
                ]
            },
            headers=world.headers("admin"),
        )
        assert refused.status_code == 422
        assert refused.json()["code"] == "business_rule_violated"
        assert "steward in DS-RISK" in refused.json()["detail"]
        assert world.one("SELECT count(*) FROM org.group_mapping WHERE org_id = %s", world.org) == 0


async def test_group_mappings_refuse_bad_references(world: World) -> None:
    async def put(who: str, *mappings: dict[str, Any]) -> httpx.Response:
        return await api.put(
            "/v1/admin/group-mappings",
            json={"mappings": list(mappings)},
            headers=world.headers(who),
        )

    base = {"group_id": str(world.group), "role_id": str(world.roles["analyst"])}
    async with client(world) as api:
        # An organisation-wide role is never handed out inside a space.
        org_only = await put(
            "admin",
            {**base, "role_id": str(world.roles["admin"]), "data_space_id": str(world.space)},
        )
        assert org_only.status_code == 422
        assert org_only.json()["code"] == "business_rule_violated"
        foreign = await put("admin", {**base, "data_space_id": str(world.foreign_space)})
        assert foreign.status_code == 422
        twice = await put("admin", base, base)
        assert twice.status_code == 422
        unknown_role = await put("admin", {**base, "role_id": str(uuid.uuid4())})
        assert unknown_role.status_code == 422
        unknown_group = await put("admin", {**base, "group_id": str(uuid.uuid4())})
        assert unknown_group.status_code == 422
        assert (await put("steward", base)).status_code == 403


# ── emergency access ──


# AC-11-01-02
@pytest.mark.req("AC-11-01-02")
async def test_emergency_access_lasts_a_day_and_needs_a_second_person(world: World) -> None:
    # Measured when the test runs: a module-level instant drifts by the minutes the suite took.
    now = datetime.datetime.now(datetime.UTC)
    body = {
        "user_id": str(world.users["analyst"]),
        "role_id": str(world.roles["admin"]),
        "reason": "Sự cố đối soát cuối kỳ",
        "second_approver_id": str(world.users["officer"]),
    }
    async with client(world) as api:
        granted = await api.post(
            "/v1/admin/emergency-access", json=body, headers=world.headers("admin")
        )
        assert granted.status_code == 201, granted.text
        row = granted.json()
        assert row["source"] == "emergency" and row["approved_by"] == str(world.users["officer"])
        until = datetime.datetime.fromisoformat(row["valid_until"])
        assert (
            datetime.timedelta(hours=23, minutes=58)
            < until - now
            <= datetime.timedelta(hours=24, minutes=5)
        )
        assert world.one("SELECT source FROM org.user_role WHERE id = %s", row["id"]) == "emergency"
        events = dict(world.events(row["id"]))
        assert events["user_role.granted"]["user_id"] == str(world.users["analyst"])
        assert events["audit.requested"]["after"]["reason"] == "Sự cố đối soát cuối kỳ"
        assert events["audit.requested"]["actor_id"] == str(world.users["admin"])

        too_long = await api.post(
            "/v1/admin/emergency-access",
            json={**body, "valid_until": (now + datetime.timedelta(hours=25)).isoformat()},
            headers=world.headers("admin"),
        )
        assert too_long.status_code == 422
        assert too_long.json()["code"] == "business_rule_violated"
        past = await api.post(
            "/v1/admin/emergency-access",
            json={**body, "valid_until": (now - datetime.timedelta(hours=1)).isoformat()},
            headers=world.headers("admin"),
        )
        assert past.status_code == 422
        self_approved = await api.post(
            "/v1/admin/emergency-access",
            json={**body, "second_approver_id": str(world.users["admin"])},
            headers=world.headers("admin"),
        )
        assert self_approved.status_code == 422
        grantee_approves = await api.post(
            "/v1/admin/emergency-access",
            json={**body, "second_approver_id": str(world.users["analyst"])},
            headers=world.headers("admin"),
        )
        assert grantee_approves.status_code == 422
        foreign_approver = await api.post(
            "/v1/admin/emergency-access",
            json={**body, "second_approver_id": str(world.users["stranger"])},
            headers=world.headers("admin"),
        )
        assert foreign_approver.status_code == 422
        foreign_user = await api.post(
            "/v1/admin/emergency-access",
            json={**body, "user_id": str(world.users["stranger"])},
            headers=world.headers("admin"),
        )
        assert foreign_user.status_code == 422
        denied = await api.post(
            "/v1/admin/emergency-access", json=body, headers=world.headers("officer")
        )
        assert denied.status_code == 403


# ── Data Spaces ──


# AC-11-08-01
@pytest.mark.req("AC-11-08-01")
async def test_admin_creates_a_data_space_with_its_stewards(world: World) -> None:
    body = {
        "code": "DS-NEW",
        "name": "Rủi ro thị trường",
        "steward_id": str(world.users["steward"]),
        "backup_steward_id": str(world.users["helper"]),
        "default_policy": {"mask_pii": True},
        "work_calendar_id": str(world.calendar),
    }
    async with client(world) as api:
        made = await api.post("/v1/data-spaces", json=body, headers=world.headers("admin"))
        assert made.status_code == 201, made.text
        space = made.json()
        assert space["status"] == "draft" and space["default_policy"] == {"mask_pii": True}
        assert space["backup_steward_id"] == str(world.users["helper"])
        assert world.one("SELECT code FROM org.data_space WHERE id = %s", space["id"]) == "DS-NEW"
        events = dict(world.events(space["id"]))
        assert events["data_space.created"]["steward_id"] == str(world.users["steward"])
        assert events["audit.requested"]["action"] == "data_space.created"
        # Both stewards act in the space through a membership with the steward role.
        seated = world.all(
            "SELECT principal_id FROM org.data_space_member WHERE data_space_id = %s"
            " AND role_id = %s",
            space["id"],
            world.roles["steward"],
        )
        assert {str(r[0]) for r in seated} == {
            str(world.users["steward"]),
            str(world.users["helper"]),
        }

        again = await api.post("/v1/data-spaces", json=body, headers=world.headers("admin"))
        assert again.status_code == 409
        same_person = await api.post(
            "/v1/data-spaces",
            json={**body, "code": "DS-X", "backup_steward_id": body["steward_id"]},
            headers=world.headers("admin"),
        )
        assert same_person.status_code == 422
        foreign_steward = await api.post(
            "/v1/data-spaces",
            json={**body, "code": "DS-Y", "steward_id": str(world.users["stranger"])},
            headers=world.headers("admin"),
        )
        assert foreign_steward.status_code == 422
        foreign_calendar = await api.post(
            "/v1/data-spaces",
            json={**body, "code": "DS-Z", "work_calendar_id": str(world.foreign_calendar)},
            headers=world.headers("admin"),
        )
        assert foreign_calendar.status_code == 422


# AC-11-08-06
@pytest.mark.req("AC-11-08-06")
async def test_only_an_org_wide_admin_creates_a_data_space(world: World) -> None:
    body = {"code": "DS-DENIED", "name": "x", "steward_id": str(world.users["steward"])}
    async with client(world) as api:
        steward = await api.post(
            "/v1/data-spaces", json=body, headers=world.headers("steward", world.space)
        )
        assert steward.status_code == 403
        # A Data Space role that names `admin` grants nothing organisation-wide.
        rogue = await api.post(
            "/v1/data-spaces", json=body, headers=world.headers("rogue", world.space)
        )
        assert rogue.status_code == 403
    assert world.one("SELECT count(*) FROM org.data_space WHERE code = 'DS-DENIED'") == 0


async def test_patch_moves_a_data_space_through_its_states(world: World) -> None:
    draft = world.seed.row(
        "org.data_space",
        org_id=world.org,
        code="DS-DRAFT",
        name="Nháp",
        steward_id=world.users["steward"],
    )
    async with client(world) as api:

        async def patch(body: dict[str, Any], who: str = "admin", space: object = draft) -> Any:
            return await api.patch(
                f"/v1/data-spaces/{space}", json=body, headers=world.headers(who)
            )

        # BR-143: no backup steward, no opening.
        early = await patch({"status": "active"})
        assert early.status_code == 422 and early.json()["code"] == "business_rule_violated"
        opened = await patch(
            {
                "status": "active",
                "backup_steward_id": str(world.users["helper"]),
                "default_policy": {"allow_external_channels": False},
                "name": "Đã mở",
            }
        )
        assert opened.status_code == 200, opened.text
        assert opened.json()["status"] == "active" and opened.json()["opened_at"]
        assert opened.json()["default_policy"] == {"allow_external_channels": False}
        payload = dict(world.events(draft))["data_space.updated"]
        assert payload["status"] == "active"
        assert "audit.requested" in world.event_types(draft)

        back = await patch({"status": "draft"})
        assert back.status_code == 409 and back.json()["code"] == "invalid_state"
        dropped = await patch({"backup_steward_id": None})
        assert dropped.status_code == 422
        null_name = await patch({"name": None})
        assert null_name.status_code == 422
        same = await patch({"backup_steward_id": str(world.users["steward"])})
        assert same.status_code == 422
        archived = await patch({"status": "archived"})
        assert archived.status_code == 200
        reopened = await patch({"status": "active"})
        assert reopened.status_code == 409

        foreign = await patch({"name": "x"}, space=world.foreign_space)
        assert foreign.status_code == 404
        denied = await patch({"name": "x"}, who="steward")
        assert denied.status_code == 403


# AC-11-08-02
@pytest.mark.req("AC-11-08-02")
async def test_steward_sets_members_of_their_own_space(world: World) -> None:
    members = [
        {
            "principal_type": "user",
            "principal_id": str(world.users["steward"]),
            "role_id": str(world.roles["steward"]),
        },
        {
            "principal_type": "group",
            "principal_id": str(world.group),
            "role_id": str(world.roles["analyst"]),
            "row_scope": {"region": "north"},
            "valid_until": "2030-12-31T23:59:00+07:00",
        },
    ]
    async with client(world) as api:
        put = await api.put(
            f"/v1/data-spaces/{world.space}/members",
            json={"members": members},
            headers=world.headers("steward", world.space),
        )
        assert put.status_code == 200, put.text
        rows = {(m["principal_type"], m["principal_id"]): m for m in put.json()["items"]}
        group = rows[("group", str(world.group))]
        assert group["row_scope"] == {"region": "north"} and group["source"] == "manual"
        # Members left out are removed: the analyst and the rogue role.
        assert ("user", str(world.users["analyst"])) not in rows
        assert world.one(
            "SELECT count(*) FROM org.data_space_member WHERE data_space_id = %s", world.space
        ) == len(rows)
        created = dict(world.events(group["id"]))["data_space_member.created"]
        assert created["data_space_id"] == str(world.space)
        assert created["principal_id"] == str(world.group)
        audit = dict(world.events(world.space))["audit.requested"]
        assert audit["action"] == "data_space.members_changed"
        assert group["id"] in audit["after"]["created"]

        # Another space than the one named in X-Data-Space answers as if it did not exist.
        elsewhere = await api.put(
            f"/v1/data-spaces/{world.other_space}/members",
            json={"members": members},
            headers=world.headers("steward", world.space),
        )
        assert elsewhere.status_code == 404
        no_header = await api.put(
            f"/v1/data-spaces/{world.space}/members",
            json={"members": members},
            headers=world.headers("steward"),
        )
        assert no_header.status_code in (400, 403)
        analyst = await api.put(
            f"/v1/data-spaces/{world.other_space}/members",
            json={"members": members},
            headers=world.headers("analyst", world.space),
        )
        assert analyst.status_code in (403, 404)
        not_member = await api.put(
            f"/v1/data-spaces/{world.space}/members",
            json={"members": members},
            headers=world.headers("admin", world.space),
        )
        assert not_member.status_code == 404


async def test_members_never_carry_org_wide_roles_or_conflicts(world: World) -> None:
    steward = {
        "principal_type": "user",
        "principal_id": str(world.users["steward"]),
        "role_id": str(world.roles["steward"]),
    }

    async def put(*members: dict[str, Any]) -> httpx.Response:
        return await api.put(
            f"/v1/data-spaces/{world.space}/members",
            json={"members": list(members)},
            headers=world.headers("steward", world.space),
        )

    async with client(world) as api:
        org_only = await put(
            steward,
            {
                "principal_type": "user",
                "principal_id": str(world.users["analyst"]),
                "role_id": str(world.roles["admin"]),
            },
        )
        assert org_only.status_code == 403 and org_only.json()["code"] == "policy_denied"
        # BR-136: the org-wide compliance officer becomes an approver in the space.
        conflict = await put(
            steward,
            {
                "principal_type": "user",
                "principal_id": str(world.users["officer"]),
                "role_id": str(world.roles["steward"]),
            },
        )
        assert conflict.status_code == 403 and conflict.json()["code"] == "policy_denied"
        assert "officer in DS-RISK" in conflict.json()["detail"]
        foreign = await put(
            steward,
            {
                "principal_type": "user",
                "principal_id": str(world.users["stranger"]),
                "role_id": str(world.roles["analyst"]),
            },
        )
        assert foreign.status_code == 422
        expired = await put(
            {**steward, "valid_until": (NOW - datetime.timedelta(days=1)).isoformat()}
        )
        assert expired.status_code == 422
        assert (
            world.one(
                "SELECT count(*) FROM org.data_space_member WHERE data_space_id = %s",
                world.space,
            )
            == 3
        )


# AC-11-08-04
@pytest.mark.req("AC-11-08-04")
async def test_the_last_steward_cannot_be_removed(world: World) -> None:
    async with client(world) as api:
        refused = await api.put(
            f"/v1/data-spaces/{world.space}/members",
            json={
                "members": [
                    {
                        "principal_type": "user",
                        "principal_id": str(world.users["analyst"]),
                        "role_id": str(world.roles["analyst"]),
                    }
                ]
            },
            headers=world.headers("steward", world.space),
        )
    assert refused.status_code == 422
    assert refused.json()["code"] == "business_rule_violated"
    assert world.one(
        "SELECT count(*) FROM org.data_space_member WHERE data_space_id = %s AND role_id = %s",
        world.space,
        world.roles["steward"],
    )


async def test_admin_data_assigns_sources_through_the_catalog(world: World) -> None:
    sources = [str(uuid.uuid4()), str(uuid.uuid4())]
    async with client(world) as api:
        put = await api.put(
            f"/v1/data-spaces/{world.space}/sources",
            json={"source_ids": sources},
            headers=world.headers("steward", world.space),
        )
        assert put.status_code == 200, put.text
        assert put.json()["id"] == str(world.space)
        assigned = dict(world.events(world.space))["data_space.sources_assigned"]
        assert assigned["source_ids"] == sorted(sources)

        twice = await api.put(
            f"/v1/data-spaces/{world.space}/sources",
            json={"source_ids": [sources[0], sources[0]]},
            headers=world.headers("steward", world.space),
        )
        assert twice.status_code == 422
        elsewhere = await api.put(
            f"/v1/data-spaces/{world.other_space}/sources",
            json={"source_ids": sources},
            headers=world.headers("steward", world.space),
        )
        assert elsewhere.status_code == 404
        denied = await api.put(
            f"/v1/data-spaces/{world.space}/sources",
            json={"source_ids": sources},
            headers=world.headers("analyst", world.space),
        )
        assert denied.status_code == 403


# ── access requests ──


async def _request(
    api: httpx.AsyncClient,
    world: World,
    capability: str,
    *,
    days: int = 30,
    space: uuid.UUID | None = None,
    who: str = "colleague",
) -> httpx.Response:
    return await api.post(
        "/v1/access-requests",
        json={
            "data_space_id": str(space or world.space),
            "capability": capability,
            "reason": "Lập báo cáo nợ xấu quý 3",
            "requested_until": (TODAY + datetime.timedelta(days=days)).isoformat(),
        },
        headers=world.headers(who),
    )


async def _decide(
    api: httpx.AsyncClient, world: World, request_id: str, who: str = "steward", **body: Any
) -> httpx.Response:
    return await api.post(
        f"/v1/access-requests/{request_id}:decide",
        json=body,
        headers=world.headers(who, world.space),
    )


async def test_access_requests_can_be_listed_by_requester_and_steward(world: World) -> None:
    async with client(world) as api:
        sent = (await _request(api, world, "ask")).json()
        own = await api.get("/v1/access-requests", headers=world.headers("colleague"))
        assert own.status_code == 200, own.text
        assert any(item["id"] == sent["id"] for item in own.json()["items"])

        detail = await api.get(
            f"/v1/access-requests/{sent['id']}",
            headers=world.headers("colleague"),
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["id"] == sent["id"]

        steward = await api.get(
            "/v1/access-requests?status=pending",
            headers=world.headers("steward", world.space),
        )
        assert steward.status_code == 200, steward.text
        assert any(item["id"] == sent["id"] for item in steward.json()["items"])


# AC-11-07-01
@pytest.mark.req("AC-11-07-01")
async def test_an_approved_request_adds_a_member_for_the_requested_time(world: World) -> None:
    async with client(world) as api:
        sent = await _request(api, world, "run_agent")
        assert sent.status_code == 201, sent.text
        request = sent.json()
        assert request["status"] == "pending" and request["needs_compliance"] is False
        created = dict(world.events(request["id"]))["access_request.created"]
        assert created["steward_id"] == str(world.users["steward"])
        assert created["capability"] == "run_agent"

        decided = await _decide(api, world, request["id"], decision="approve", note="Đồng ý")
        assert decided.status_code == 200, decided.text
        body = decided.json()
        assert body["status"] == "approved" and body["approver_id"] == str(world.users["steward"])
        member = body["member_id"]
        source, role, until = world.all(
            "SELECT source, role_id, valid_until FROM org.data_space_member WHERE id = %s", member
        )[0]
        assert source == "access_request" and str(role) == str(world.roles["agent"])
        # The member holds the right for the 30 days asked, counted from the approval.
        decided_at = datetime.datetime.fromisoformat(body["decided_at"])
        assert until.date() == (decided_at + datetime.timedelta(days=30)).date()
        assert set(world.event_types(request["id"])) == {
            "access_request.created",
            "access_request.approved",
            "audit.requested",
        }
        assert "data_space_member.created" in world.event_types(member)

        again = await _decide(api, world, request["id"], decision="reject")
        assert again.status_code == 409 and again.json()["code"] == "invalid_state"


# AC-11-07-02
@pytest.mark.req("AC-11-07-02")
async def test_narrowing_grants_less_never_more(world: World) -> None:
    async with client(world) as api:
        sent = (await _request(api, world, "run_agent")).json()
        missing = await _decide(api, world, sent["id"], decision="narrow")
        assert missing.status_code == 422
        narrowed = await _decide(
            api,
            world,
            sent["id"],
            decision="narrow",
            granted_scope={"capability": "view_aggregate", "row_scope": {"branch": "HN"}},
        )
        assert narrowed.status_code == 200, narrowed.text
        assert narrowed.json()["status"] == "narrowed"
        assert narrowed.json()["granted_scope"]["capability"] == "view_aggregate"
        role, scope = world.all(
            "SELECT role_id, row_scope FROM org.data_space_member WHERE id = %s",
            narrowed.json()["member_id"],
        )[0]
        assert str(role) == str(world.roles["analyst"]) and scope == {"branch": "HN"}

        # "Narrowing" to more than was asked would skip the AC06 approval detail data needs.
        asked = (await _request(api, world, "view_aggregate", who="helper")).json()
        widened = await _decide(
            api, world, asked["id"], decision="narrow", granted_scope={"capability": "view_detail"}
        )
        assert widened.status_code == 422
        assert world.one("SELECT status FROM org.access_request WHERE id = %s", asked["id"]) == (
            "pending"
        )


# AC-11-07-03
@pytest.mark.req("AC-11-07-03")
async def test_a_rejected_request_adds_no_member(world: World) -> None:
    async with client(world) as api:
        sent = (await _request(api, world, "ask")).json()
        scoped = await _decide(
            api, world, sent["id"], decision="reject", granted_scope={"capability": "ask"}
        )
        assert scoped.status_code == 422
        rejected = await _decide(api, world, sent["id"], decision="reject", note="Không cần")
        assert rejected.status_code == 200, rejected.text
        body = rejected.json()
        assert body["status"] == "rejected" and body["approver_id"] and body["decided_at"]
        assert body["member_id"] is None
        assert (
            world.one(
                "SELECT count(*) FROM org.data_space_member WHERE principal_id = %s",
                world.users["colleague"],
            )
            == 0
        )
        assert "access_request.rejected" in world.event_types(sent["id"])


# AC-11-07-05
@pytest.mark.req("AC-11-07-05")
async def test_detail_data_waits_for_compliance(world: World) -> None:
    async with client(world) as api:
        sent = (await _request(api, world, "view_detail")).json()
        assert sent["needs_compliance"] is True
        refused = await _decide(api, world, sent["id"], decision="approve")
        assert refused.status_code == 403
        assert world.one("SELECT status FROM org.access_request WHERE id = %s", sent["id"]) == (
            "pending"
        )
        assert (
            world.one(
                "SELECT count(*) FROM org.data_space_member WHERE principal_id = %s",
                world.users["colleague"],
            )
            == 0
        )
        # The steward may still turn it down without AC06.
        rejected = await _decide(api, world, sent["id"], decision="reject")
        assert rejected.status_code == 200


# AC-11-07-06
@pytest.mark.req("AC-11-07-06")
async def test_a_request_lasts_at_most_ninety_days(world: World) -> None:
    async with client(world) as api:
        long = await _request(api, world, "ask", days=120)
        assert long.status_code == 422 and long.json()["code"] == "business_rule_violated"
        past = await _request(api, world, "ask", days=0)
        assert past.status_code == 422
        assert (await _request(api, world, "ask", days=90)).status_code == 201


async def test_access_requests_stay_in_their_org_and_space(world: World) -> None:
    async with client(world) as api:
        foreign = await _request(api, world, "ask", space=world.foreign_space)
        assert foreign.status_code == 404
        unknown = await _request(api, world, "ask", space=uuid.uuid4())
        assert unknown.status_code == 404
        sent = (await _request(api, world, "ask")).json()
        duplicate = await _request(api, world, "ask")
        assert duplicate.status_code == 409

        own = (await _request(api, world, "run_agent", who="steward")).json()
        self_decided = await _decide(api, world, own["id"], decision="approve")
        assert self_decided.status_code == 403

        # A request of DS-MKT is not decided under the X-Data-Space of DS-RISK.
        other = (await _request(api, world, "ask", space=world.other_space, who="helper")).json()
        wrong_space = await _decide(api, world, other["id"], decision="approve")
        assert wrong_space.status_code == 404
        analyst = await _decide(api, world, sent["id"], who="analyst", decision="approve")
        assert analyst.status_code == 403
        rogue = await _decide(api, world, sent["id"], who="rogue", decision="approve")
        # The rogue role names steward, a Data Space capability, so it passes the gate, but
        # never compliance: AC06 approval still comes from an org-wide role only.
        assert rogue.status_code == 200
        detail = (await _request(api, world, "view_detail", who="helper")).json()
        by_rogue = await _decide(api, world, detail["id"], who="rogue", decision="approve")
        assert by_rogue.status_code == 403


# ── work calendars ──


async def test_calendars_are_listed_for_askers_and_edited_by_admins(world: World) -> None:
    second = world.seed.row("org.work_calendar", org_id=world.org, code="VN-BANK", name="Ngân hàng")
    async with client(world) as api:
        listed = await api.get("/v1/calendars", headers=world.headers("analyst", world.space))
        assert listed.status_code == 200, listed.text
        items = {c["code"]: c for c in listed.json()["items"]}
        assert set(items) == {"VN", "VN-BANK"}
        assert items["VN"]["days"] == [
            {"day": "2026-09-02", "kind": "holiday", "name": "Quốc khánh"}
        ]
        assert items["VN"]["weekend_days"] == [6, 7]
        nobody = await api.get("/v1/calendars", headers=world.headers("colleague"))
        assert nobody.status_code == 403

        changed = await api.put(
            f"/v1/calendars/{second}",
            json={
                "time_zone": "Asia/Ho_Chi_Minh",
                "weekend_days": [7],
                "is_default": True,
                "days": [
                    {"day": "2026-04-30", "kind": "holiday", "name": "Giải phóng"},
                    {"day": "2026-05-09", "kind": "working_override"},
                ],
            },
            headers=world.headers("admin"),
        )
        assert changed.status_code == 200, changed.text
        body = changed.json()
        assert body["is_default"] is True and body["weekend_days"] == [7]
        assert [d["day"] for d in body["days"]] == ["2026-04-30", "2026-05-09"]
        # One default calendar per organisation: the old one gave way.
        assert (
            world.one("SELECT is_default FROM org.work_calendar WHERE id = %s", world.calendar)
            is False
        )
        assert (
            world.one(
                "SELECT is_default FROM org.work_calendar WHERE id = %s", world.foreign_calendar
            )
            is True
        )
        assert dict(world.events(second))["work_calendar.updated"]["is_default"] is True

        undefault = await api.put(
            f"/v1/calendars/{second}", json={"is_default": False}, headers=world.headers("admin")
        )
        assert undefault.status_code == 422
        bad_zone = await api.put(
            f"/v1/calendars/{second}",
            json={"time_zone": "Mars/Base"},
            headers=world.headers("admin"),
        )
        assert bad_zone.status_code == 422
        bad_days = await api.put(
            f"/v1/calendars/{second}", json={"weekend_days": [0, 8]}, headers=world.headers("admin")
        )
        assert bad_days.status_code == 422
        foreign = await api.put(
            f"/v1/calendars/{world.foreign_calendar}",
            json={"name": "x"},
            headers=world.headers("admin"),
        )
        assert foreign.status_code == 404
        denied = await api.put(
            f"/v1/calendars/{second}", json={"name": "x"}, headers=world.headers("steward")
        )
        assert denied.status_code == 403
