"""Phiên làm việc, endpoints added 12/10/2026, against PostgreSQL and the V3 grants:
GET /v1/session-reports, GET /v1/session-reports/{id}, GET /v1/workspaces/{id}/shares,
POST /v1/workspaces/{id}/shares:preview and GET /v1/tiles/{id}/comments."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from services.api.app import create_app
from services.api.config import Settings

from .conftest import Seed

pytestmark = [pytest.mark.integration, pytest.mark.contract]

PEOPLE = {
    # who: (capabilities, Data Spaces it is a member of: "A", "B")
    "owner": (["ask"], "AB"),
    "viewer": (["ask"], "A"),
    "approver": (["ask", "approve"], "A"),
    "both": (["ask"], "AB"),
    "outsider": (["ask"], "B"),
    "noask": (["upload"], "A"),
    "scoped": (["ask"], "A"),
    "left": (["ask"], "A"),
}


@dataclass
class Team:
    org: uuid.UUID
    space: uuid.UUID
    other: uuid.UUID
    users: dict[str, uuid.UUID]
    groups: dict[str, uuid.UUID]
    tag: str
    seed: Seed


class Tokens:
    def __init__(self, org: uuid.UUID) -> None:
        self.org = org

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.org)}


@pytest.fixture
def team(seed: Seed) -> Team:
    org = seed.org()
    tag = org.hex[:8]
    users: dict[str, uuid.UUID] = {}
    roles: dict[str, uuid.UUID] = {}
    for who, (caps, _) in PEOPLE.items():
        users[who] = seed.row(
            "org.app_user",
            org_id=org,
            external_id=f"kc-{who}-{tag}",
            username=f"{who}-{tag}",
            display_name=who,
            status="active",
        )
        roles[who] = seed.row(
            "org.role", org_id=org, code=f"WS-{who}-{tag}", name=who, capabilities=caps
        )
        seed.row(
            "org.user_role", org_id=org, user_id=users[who], role_id=roles[who], source="manual"
        )
    spaces = {
        code: seed.row(
            "org.data_space",
            org_id=org,
            code=f"DS-{code}-{tag}",
            name=code,
            steward_id=users["owner"],
        )
        for code in "AB"
    }
    for who, (_, codes) in PEOPLE.items():
        for code in codes:
            seed.row(
                "org.data_space_member",
                org_id=org,
                data_space_id=spaces[code],
                principal_type="user",
                principal_id=users[who],
                role_id=roles[who],
                row_scope=json.dumps({"branch_code": "HN01"}) if who == "scoped" else None,
            )
    with seed.conn.cursor() as cur:
        cur.execute("UPDATE org.app_user SET status = 'left' WHERE id = %s", (str(users["left"]),))
    # A directory group that is a member of A with an `ask` role, and one that is not a member.
    groups = {
        name: seed.row("org.user_group", org_id=org, external_id=f"cn={name}-{tag}", name=name)
        for name in ("risk", "retail")
    }
    seed.row(
        "org.data_space_member",
        org_id=org,
        data_space_id=spaces["A"],
        principal_type="group",
        principal_id=groups["risk"],
        role_id=roles["viewer"],
    )
    return Team(
        org=org,
        space=spaces["A"],
        other=spaces["B"],
        users=users,
        groups=groups,
        tag=tag,
        seed=seed,
    )


@asynccontextmanager
async def _api(team: Team) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(), token_verifier=Tokens(team.org))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api"
    ) as api:
        yield api


def _as(team: Team, who: str, space: uuid.UUID | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer kc-{who}-{team.tag}",
        "X-Data-Space": str(space or team.space),
    }


def _count(team: Team, table: str) -> int:
    with team.seed.conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table} WHERE org_id = %s", (str(team.org),))
        return int(cur.fetchone()[0])


async def _workspace(api: httpx.AsyncClient, team: Team, title: str = "Nợ xấu T9") -> str:
    created = await api.post("/v1/workspaces", headers=_as(team, "owner"), json={"title": title})
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


async def _tile(api: httpx.AsyncClient, team: Team, workspace_id: str, title: str) -> str:
    created = await api.post(
        f"/v1/workspaces/{workspace_id}/tiles",
        headers=_as(team, "owner"),
        json={"kind": "sql", "title": title, "spec": {"sql": "select 1"}},
    )
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


async def _share(api: httpx.AsyncClient, team: Team, workspace_id: str, **who: str) -> None:
    shared = await api.put(
        f"/v1/workspaces/{workspace_id}/shares",
        headers=_as(team, "owner"),
        json={
            "shares": [
                {"principal_type": "user", "principal_id": str(team.users[name]), "permission": p}
                for name, p in who.items()
            ]
        },
    )
    assert shared.status_code == 200, shared.text


# ---------------------------------------------------------------- session reports


async def test_session_reports_list_and_read(team: Team) -> None:
    async with _api(team) as api:
        workspace_id = await _workspace(api, team)
        other_id = await _workspace(api, team, "Huy động")
        reports = []
        for title in ("Bản 1", "Bản 2"):
            made = await api.post(
                f"/v1/workspaces/{workspace_id}/reports",
                headers=_as(team, "owner"),
                json={"title": title},
            )
            assert made.status_code == 201, made.text
            reports.append(made.json())
        made = await api.post(
            f"/v1/workspaces/{other_id}/reports", headers=_as(team, "owner"), json={"title": "X"}
        )
        assert made.status_code == 201, made.text

        listed = await api.get(
            f"/v1/session-reports?workspace_id={workspace_id}", headers=_as(team, "owner")
        )
        assert listed.status_code == 200, listed.text
        assert [r["id"] for r in listed.json()["items"]] == [reports[1]["id"], reports[0]["id"]]
        everything = await api.get("/v1/session-reports", headers=_as(team, "owner"))
        assert len(everything.json()["items"]) == 3

        page = await api.get(
            f"/v1/session-reports?workspace_id={workspace_id}&page_size=1",
            headers=_as(team, "owner"),
        )
        token = page.json()["next_page_token"]
        assert token
        rest = await api.get(
            f"/v1/session-reports?workspace_id={workspace_id}&page_size=1&cursor={token}",
            headers=_as(team, "owner"),
        )
        assert [r["id"] for r in rest.json()["items"]] == [reports[0]["id"]]
        assert rest.json()["next_page_token"] == ""

        checked = await api.post(
            f"/v1/session-reports/{reports[0]['id']}:check", headers=_as(team, "owner")
        )
        assert checked.status_code == 202, checked.text
        # :check's Location is the report itself.
        read = await api.get(checked.headers["Location"], headers=_as(team, "owner"))
        assert read.status_code == 200, read.text
        body = read.json()
        assert body["id"] == reports[0]["id"] and body["status"] == "checking"
        assert {"checks", "body_ref", "version", "title"} <= set(body)
        assert "org_id" not in body and "created_by" not in body

        checking = await api.get("/v1/session-reports?status=checking", headers=_as(team, "owner"))
        assert [r["id"] for r in checking.json()["items"]] == [reports[0]["id"]]
        bad = await api.get("/v1/session-reports?status=nope", headers=_as(team, "owner"))
        assert bad.status_code == 422


async def test_session_reports_follow_the_workspace_rights(team: Team) -> None:
    async with _api(team) as api:
        workspace_id = await _workspace(api, team)
        made = await api.post(
            f"/v1/workspaces/{workspace_id}/reports",
            headers=_as(team, "owner"),
            json={"title": "R"},
        )
        report_id = made.json()["id"]
        path = f"/v1/session-reports/{report_id}"

        # Neither shared nor approving: as if it did not exist.
        assert (await api.get(path, headers=_as(team, "both"))).status_code == 404
        listed = await api.get("/v1/session-reports", headers=_as(team, "both"))
        assert listed.json()["items"] == []
        # Another Data Space: not found either.
        assert (await api.get(path, headers=_as(team, "owner", team.other))).status_code == 404
        assert (
            await api.get(f"/v1/session-reports/{uuid.uuid4()}", headers=_as(team, "owner"))
        ).status_code == 404
        # The A2 gate: `ask`.
        assert (await api.get(path, headers=_as(team, "noask"))).status_code == 403
        assert (await api.get("/v1/session-reports", headers=_as(team, "noask"))).status_code == 403

        # A share opens it.
        await _share(api, team, workspace_id, viewer="view")
        assert (await api.get(path, headers=_as(team, "viewer"))).status_code == 200

        # The approver reads it once it waits for approval, without a share (publish_report).
        assert (await api.get(path, headers=_as(team, "approver"))).status_code == 404
        with team.seed.conn.cursor() as cur:
            cur.execute(
                "UPDATE workspace.session_report SET status = 'pending_approval' WHERE id = %s",
                (report_id,),
            )
        assert (await api.get(path, headers=_as(team, "approver"))).status_code == 200
        waiting = await api.get(
            "/v1/session-reports?status=pending_approval", headers=_as(team, "approver")
        )
        assert [r["id"] for r in waiting.json()["items"]] == [report_id]


# ---------------------------------------------------------------- shares


async def test_list_shares(team: Team) -> None:
    async with _api(team) as api:
        workspace_id = await _workspace(api, team)
        empty = await api.get(f"/v1/workspaces/{workspace_id}/shares", headers=_as(team, "owner"))
        assert empty.status_code == 200, empty.text
        assert empty.json()["items"] == []
        await _share(api, team, workspace_id, viewer="view", both="edit")

        listed = await api.get(f"/v1/workspaces/{workspace_id}/shares", headers=_as(team, "viewer"))
        assert listed.status_code == 200, listed.text
        got = {(s["principal_id"], s["permission"]) for s in listed.json()["items"]}
        assert got == {(str(team.users["viewer"]), "view"), (str(team.users["both"]), "edit")}
        assert all(s["granted_by"] == str(team.users["owner"]) for s in listed.json()["items"])

        assert (
            await api.get(f"/v1/workspaces/{workspace_id}/shares", headers=_as(team, "approver"))
        ).status_code == 404
        assert (
            await api.get(
                f"/v1/workspaces/{workspace_id}/shares", headers=_as(team, "owner", team.other)
            )
        ).status_code == 404
        assert (
            await api.get(f"/v1/workspaces/{workspace_id}/shares", headers=_as(team, "noask"))
        ).status_code == 403


def _grant(kind: str, principal: uuid.UUID, permission: str = "view") -> dict[str, str]:
    return {"principal_type": kind, "principal_id": str(principal), "permission": permission}


async def test_preview_says_what_each_recipient_would_see(team: Team) -> None:
    seed = team.seed
    async with _api(team) as api:
        workspace_id = await _workspace(api, team)
        local = await _tile(api, team, workspace_id, "Dư nợ")
        cross = await _tile(api, team, workspace_id, "Huy động")
        # The cross tile's last result read Data Space B (what a tile run writes).
        run = seed.row(
            "query.query_run",
            org_id=team.org,
            data_space_id=team.other,
            caller_kind="tile",
            caller_id=uuid.UUID(cross),
            principal_id=team.users["owner"],
            route="live",
            sql_generated="select 1",
            sql_executed="select 1",
            status="succeeded",
        )
        snapshot = str(uuid.uuid4())
        with seed.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO workspace.tile_snapshot (id, org_id, tile_id, query_run_ids, status)"
                " VALUES (%s, %s, %s, ARRAY[%s]::uuid[], 'ok')",
                (snapshot, str(team.org), cross, str(run)),
            )
            cur.execute(
                "UPDATE workspace.tile SET latest_snapshot_id = %s WHERE id = %s", (snapshot, cross)
            )
        shares_before = _count(team, "workspace.workspace_share")
        events_before = _count(team, "workspace.outbox")

        u = team.users
        previewed = await api.post(
            f"/v1/workspaces/{workspace_id}/shares:preview",
            headers=_as(team, "owner"),
            json={
                "shares": [
                    _grant("user", u["viewer"]),
                    _grant("user", u["both"], "edit"),
                    _grant("user", u["outsider"]),
                    _grant("user", u["noask"]),
                    _grant("user", u["scoped"]),
                    _grant("user", u["left"]),
                    _grant("user", uuid.uuid4()),
                    _grant("group", team.groups["risk"]),
                    _grant("group", team.groups["retail"]),
                ]
            },
        )
        assert previewed.status_code == 200, previewed.text
        items = previewed.json()["items"]
        by = {i["principal_id"]: i for i in items}
        assert len(items) == 9

        viewer = by[str(u["viewer"])]
        assert viewer["can_open"] is True and viewer["reason"] is None
        assert viewer["visible_tile_ids"] == [local]
        assert viewer["hidden_tiles"] == [{"tile_id": cross, "reason": "other_data_space"}]
        assert viewer["row_scope_limited"] is False

        both = by[str(u["both"])]
        assert both["permission"] == "edit"
        assert both["visible_tile_ids"] == [local, cross] and both["hidden_tiles"] == []

        def closed(who: str | uuid.UUID, reason: str) -> None:
            item = by[str(u[who] if isinstance(who, str) else who)]
            assert item["can_open"] is False and item["reason"] == reason, item
            assert item["visible_tile_ids"] == []
            assert {h["reason"] for h in item["hidden_tiles"]} == {reason}

        closed("outsider", "not_data_space_member")
        closed("noask", "missing_capability")
        closed("left", "not_in_organisation")
        unknown = next(
            i
            for i in items
            if i["principal_id"]
            not in {str(v) for v in u.values()} | {str(g) for g in team.groups.values()}
        )
        assert unknown["reason"] == "not_in_organisation"

        scoped = by[str(u["scoped"])]
        assert scoped["can_open"] is True and scoped["row_scope_limited"] is True

        risk = by[str(team.groups["risk"])]
        assert risk["can_open"] is True and risk["visible_tile_ids"] == [local]
        retail = by[str(team.groups["retail"])]
        assert retail["reason"] == "not_data_space_member"

        # A preview writes nothing (BR-89).
        assert _count(team, "workspace.workspace_share") == shares_before
        assert _count(team, "workspace.outbox") == events_before


async def test_preview_is_the_owners_and_checks_the_body(team: Team) -> None:
    async with _api(team) as api:
        workspace_id = await _workspace(api, team)
        path = f"/v1/workspaces/{workspace_id}/shares:preview"
        body = {"shares": [_grant("user", team.users["viewer"])]}
        await _share(api, team, workspace_id, both="edit")
        # An editor does not share, so it does not preview either.
        assert (await api.post(path, headers=_as(team, "both"), json=body)).status_code == 403
        assert (await api.post(path, headers=_as(team, "approver"), json=body)).status_code == 404
        assert (
            await api.post(path, headers=_as(team, "owner", team.other), json=body)
        ).status_code == 404
        assert (await api.post(path, headers=_as(team, "noask"), json=body)).status_code == 403
        missing = await api.post(path, headers=_as(team, "owner"), json={})
        assert missing.status_code == 422
        twice = await api.post(
            path,
            headers=_as(team, "owner"),
            json={"shares": [_grant("user", team.users["viewer"])] * 2},
        )
        assert twice.status_code == 422
        unknown = await api.post(
            f"/v1/workspaces/{uuid.uuid4()}/shares:preview", headers=_as(team, "owner"), json=body
        )
        assert unknown.status_code == 404


# ---------------------------------------------------------------- comments


async def test_tile_comments_in_order_with_tasks_and_replies(team: Team) -> None:
    async with _api(team) as api:
        workspace_id = await _workspace(api, team)
        tile = await _tile(api, team, workspace_id, "Dư nợ")
        other_tile = await _tile(api, team, workspace_id, "Khác")
        owner = _as(team, "owner")
        first = await api.post(f"/v1/tiles/{tile}/comments", headers=owner, json={"body": "Số lạ"})
        assert first.status_code == 201, first.text
        reply = await api.post(
            f"/v1/tiles/{tile}/comments",
            headers=owner,
            json={"body": "Kiểm lại", "parent_id": first.json()["id"]},
        )
        task = await api.post(
            f"/v1/tiles/{tile}/comments",
            headers=owner,
            json={
                "body": "Đối chiếu sổ cái",
                "assignee_id": str(team.users["viewer"]),
                "due_at": "2026-10-20T10:00:00Z",
            },
        )
        await api.post(f"/v1/tiles/{other_tile}/comments", headers=owner, json={"body": "Khác"})

        listed = await api.get(f"/v1/tiles/{tile}/comments", headers=owner)
        assert listed.status_code == 200, listed.text
        items = listed.json()["items"]
        assert [c["id"] for c in items] == [
            first.json()["id"],
            reply.json()["id"],
            task.json()["id"],
        ]
        assert items[1]["parent_id"] == first.json()["id"]
        assert items[2]["assignee_id"] == str(team.users["viewer"]) and items[2]["status"] == "open"

        page = await api.get(f"/v1/tiles/{tile}/comments?page_size=2", headers=owner)
        token = page.json()["next_page_token"]
        rest = await api.get(f"/v1/tiles/{tile}/comments?page_size=2&cursor={token}", headers=owner)
        assert [c["id"] for c in rest.json()["items"]] == [task.json()["id"]]

        # A deleted comment leaves the thread.
        with team.seed.conn.cursor() as cur:
            cur.execute(
                "UPDATE workspace.ws_comment SET deleted_at = now() WHERE id = %s",
                (reply.json()["id"],),
            )
        after = await api.get(f"/v1/tiles/{tile}/comments", headers=owner)
        assert len(after.json()["items"]) == 2


async def test_tile_comments_follow_the_workspace_rights(team: Team) -> None:
    async with _api(team) as api:
        workspace_id = await _workspace(api, team)
        tile = await _tile(api, team, workspace_id, "Dư nợ")
        path = f"/v1/tiles/{tile}/comments"
        assert (await api.get(path, headers=_as(team, "viewer"))).status_code == 404
        await _share(api, team, workspace_id, viewer="view")
        assert (await api.get(path, headers=_as(team, "viewer"))).status_code == 200
        assert (await api.get(path, headers=_as(team, "owner", team.other))).status_code == 404
        assert (
            await api.get(f"/v1/tiles/{uuid.uuid4()}/comments", headers=_as(team, "owner"))
        ).status_code == 404
        assert (await api.get(path, headers=_as(team, "noask"))).status_code == 403
