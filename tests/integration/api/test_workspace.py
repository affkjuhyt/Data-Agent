"""Phiên làm việc (API-03) against PostgreSQL 16: access levels, tile graph, sharing,
comments, session reports and the outbox rows each change writes."""

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


@dataclass
class Team:
    org: uuid.UUID
    space: uuid.UUID
    other_space: uuid.UUID
    owner: uuid.UUID
    peer: uuid.UUID
    tag: str


class Tokens:
    def __init__(self, org: uuid.UUID) -> None:
        self.org = org

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.org)}


def _team(seed: Seed) -> Team:
    """One org, two Data Spaces, two members (both `ask` and `approve`)."""
    org = seed.org()
    tag = org.hex[:8]
    role = seed.row(
        "org.role", org_id=org, code=f"WS-{tag}", name="Workspace", capabilities=["ask", "approve"]
    )
    users = []
    for name in ("owner", "peer"):
        user = seed.row(
            "org.app_user",
            org_id=org,
            external_id=f"kc-{name}-{tag}",
            username=f"{name}-{tag}",
            display_name=name,
            status="active",
        )
        seed.row("org.user_role", org_id=org, user_id=user, role_id=role, source="manual")
        users.append(user)
    spaces = []
    for code in ("A", "B"):
        space = seed.row(
            "org.data_space",
            org_id=org,
            code=f"DS-{code}-{tag}",
            name=f"Space {code}",
            steward_id=users[0],
        )
        for user in users:
            seed.row(
                "org.data_space_member",
                org_id=org,
                data_space_id=space,
                principal_type="user",
                principal_id=user,
                role_id=role,
            )
        spaces.append(space)
    return Team(
        org=org, space=spaces[0], other_space=spaces[1], owner=users[0], peer=users[1], tag=tag
    )


@pytest.fixture
def team(seed: Seed) -> Team:
    return _team(seed)


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


def _events(seed: Seed, aggregate_id: str) -> list[tuple[str, str, dict[str, Any]]]:
    with seed.conn.cursor() as cur:
        cur.execute(
            "SELECT event_type, aggregate_type, payload FROM workspace.outbox"
            " WHERE aggregate_id = %s ORDER BY created_at, id",
            (aggregate_id,),
        )
        return [(t, a, p if isinstance(p, dict) else json.loads(p)) for t, a, p in cur.fetchall()]


def _sql(seed: Seed, statement: str, *params: object) -> None:
    with seed.conn.cursor() as cur:
        cur.execute(statement, params)


async def _workspace(api: httpx.AsyncClient, team: Team, title: str = "Nợ xấu T9") -> str:
    created = await api.post("/v1/workspaces", headers=_as(team, "owner"), json={"title": title})
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def _tile(
    api: httpx.AsyncClient, team: Team, workspace_id: str, **body: Any
) -> dict[str, Any]:
    body.setdefault("kind", "sql")
    body.setdefault("spec", {"sql": "select 1"})
    created = await api.post(
        f"/v1/workspaces/{workspace_id}/tiles", headers=_as(team, "owner"), json=body
    )
    assert created.status_code == 201, created.text
    return created.json()


@pytest.mark.req("AC-02-01-01")
async def test_create_list_and_read_a_workspace(team: Team) -> None:
    owner = _as(team, "owner")
    async with _api(team) as api:
        created = await api.post(
            "/v1/workspaces",
            headers=owner,
            json={"title": "Nợ xấu T9", "params": {"period": "2026-09"}, "created_from": "blank"},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["owner_id"] == str(team.owner)
        assert body["data_space_id"] == str(team.space)
        assert body["status"] == "active"
        assert "org_id" not in body and "created_by" not in body

        detail = await api.get(f"/v1/workspaces/{body['id']}", headers=owner)
        assert detail.status_code == 200, detail.text
        assert detail.json()["permission"] == "owner"
        assert [(p["principal_id"], p["role"]) for p in detail.json()["participants"]] == [
            (str(team.owner), "owner")
        ]

        mine = await api.get("/v1/workspaces?owner=me&status=active", headers=owner)
        assert mine.status_code == 200, mine.text
        assert [w["id"] for w in mine.json()["items"]] == [body["id"]]
        archived = await api.get("/v1/workspaces?status=archived", headers=owner)
        assert archived.json()["items"] == []

        # The peer neither owns nor was given it.
        peer_list = await api.get("/v1/workspaces", headers=_as(team, "peer"))
        assert peer_list.json()["items"] == []
        peer_read = await api.get(f"/v1/workspaces/{body['id']}", headers=_as(team, "peer"))
        assert peer_read.status_code == 404


async def test_list_pages_with_a_cursor(team: Team) -> None:
    async with _api(team) as api:
        ids = {await _workspace(api, team, f"W{i}") for i in range(3)}
        first = await api.get("/v1/workspaces?page_size=2", headers=_as(team, "owner"))
        assert first.status_code == 200, first.text
        token = first.json()["next_page_token"]
        assert token
        second = await api.get(
            f"/v1/workspaces?page_size=2&page_token={token}", headers=_as(team, "owner")
        )
        assert second.status_code == 200, second.text
        assert second.json()["next_page_token"] == ""
        seen = [w["id"] for w in first.json()["items"] + second.json()["items"]]
        assert set(seen) == ids and len(seen) == 3


@pytest.mark.req("AC-02-06-02")
async def test_tiles_positions_and_dependencies(team: Team, seed: Seed) -> None:
    owner = _as(team, "owner")
    async with _api(team) as api:
        workspace_id = await _workspace(api, team)
        a = await _tile(api, team, workspace_id, title="A")
        b = await _tile(api, team, workspace_id, title="B", depends_on=[a["id"]])
        c = await _tile(api, team, workspace_id, title="C", depends_on=[b["id"]])
        assert (a["position"], b["position"], c["position"]) == (0, 1, 2)
        assert a["stale"] is False and b["latest_snapshot_id"] is None

        # Inserting at 0 moves the others down.
        top = await _tile(api, team, workspace_id, kind="text", spec={"md": "#"}, position=0)
        detail = (await api.get(f"/v1/workspaces/{workspace_id}", headers=owner)).json()
        assert detail["tile_count"] == 4
        assert [t["id"] for t in detail["tiles"]] == [top["id"], a["id"], b["id"], c["id"]]

        # Moving C to the top closes the gap it left.
        moved = await api.patch(f"/v1/tiles/{c['id']}", headers=owner, json={"position": 0})
        assert moved.status_code == 200, moved.text
        detail = (await api.get(f"/v1/workspaces/{workspace_id}", headers=owner)).json()
        assert [t["id"] for t in detail["tiles"]] == [c["id"], top["id"], a["id"], b["id"]]
        assert [t["position"] for t in detail["tiles"]] == [0, 1, 2, 3]

        edited = await api.patch(
            f"/v1/tiles/{a['id']}", headers=owner, json={"spec": {"sql": "select 2"}}
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["stale"] is True
        stale = {
            t["id"]: t["stale"]
            for t in (await api.get(f"/v1/workspaces/{workspace_id}", headers=owner)).json()[
                "tiles"
            ]
        }
        assert stale == {a["id"]: True, b["id"]: True, c["id"]: True, top["id"]: False}

        cycle = await api.patch(
            f"/v1/tiles/{a['id']}", headers=owner, json={"depends_on": [c["id"]]}
        )
        assert cycle.status_code == 422, cycle.text
        assert cycle.json()["code"] == "business_rule_violated"

        stranger = await api.post(
            f"/v1/workspaces/{workspace_id}/tiles",
            headers=owner,
            json={"kind": "sql", "spec": {}, "depends_on": [str(uuid.uuid4())]},
        )
        assert stranger.status_code == 422
        assert stranger.json()["code"] == "validation_failed"

        run = await api.post(f"/v1/tiles/{a['id']}:run", headers=owner)
        assert run.status_code == 202, run.text
        assert run.headers["Location"] == f"/v1/workspaces/{workspace_id}"
        assert run.json()["id"] == a["id"] and run.json()["poll_after_ms"] > 0
        [(event, aggregate, payload)] = _events(seed, a["id"])
        assert (event, aggregate) == ("tile.run_requested", "tile")
        assert payload["tile_ids"] == [a["id"], b["id"], c["id"]]
        assert payload["requested_by"] == str(team.owner)


@pytest.mark.req("AC-02-02-01")
async def test_refresh_runs_in_dependency_order(team: Team, seed: Seed) -> None:
    owner = _as(team, "owner")
    async with _api(team) as api:
        workspace_id = await _workspace(api, team)
        empty = await api.post(f"/v1/workspaces/{workspace_id}:refresh", headers=owner)
        assert empty.status_code == 409
        child = await _tile(api, team, workspace_id, title="child")
        root = await _tile(api, team, workspace_id, title="root")
        linked = await api.patch(
            f"/v1/tiles/{child['id']}", headers=owner, json={"depends_on": [root["id"]]}
        )
        assert linked.status_code == 200, linked.text

        refreshed = await api.post(
            f"/v1/workspaces/{workspace_id}:refresh", headers=owner, json={"as_of": "2026-09-30"}
        )
        assert refreshed.status_code == 202, refreshed.text
        assert refreshed.headers["Location"] == f"/v1/workspaces/{workspace_id}"
        [(event, _, payload)] = _events(seed, workspace_id)
        assert event == "workspace.refresh_requested"
        assert payload["tile_ids"] == [root["id"], child["id"]]
        assert payload["as_of"] == "2026-09-30"


@pytest.mark.req("AC-02-01-07")
async def test_two_hundred_tiles_at_most(team: Team, seed: Seed) -> None:
    async with _api(team) as api:
        workspace_id = await _workspace(api, team)
        _sql(seed, "UPDATE workspace.workspace SET tile_count = 200 WHERE id = %s", workspace_id)
        refused = await api.post(
            f"/v1/workspaces/{workspace_id}/tiles",
            headers=_as(team, "owner"),
            json={"kind": "text", "spec": {}},
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["code"] == "business_rule_violated"


@pytest.mark.req("AC-02-01-05")
async def test_a_workspace_lives_in_one_data_space(team: Team) -> None:
    async with _api(team) as api:
        workspace_id = await _workspace(api, team)
        elsewhere = _as(team, "owner", team.other_space)
        assert (
            await api.get(f"/v1/workspaces/{workspace_id}", headers=elsewhere)
        ).status_code == 404
        added = await api.post(
            f"/v1/workspaces/{workspace_id}/tiles",
            headers=elsewhere,
            json={"kind": "sql", "spec": {}},
        )
        assert added.status_code == 404
        listed = await api.get("/v1/workspaces", headers=elsewhere)
        assert listed.json()["items"] == []


async def test_archived_workspace_is_read_only(team: Team, seed: Seed) -> None:
    owner = _as(team, "owner")
    async with _api(team) as api:
        workspace_id = await _workspace(api, team)
        tile = await _tile(api, team, workspace_id)
        archived = await api.patch(
            f"/v1/workspaces/{workspace_id}", headers=owner, json={"status": "archived"}
        )
        assert archived.status_code == 200, archived.text
        assert archived.json()["status"] == "archived"
        assert (
            await api.patch(f"/v1/workspaces/{workspace_id}", headers=owner, json={"title": "x"})
        ).status_code == 409
        added = await api.post(
            f"/v1/workspaces/{workspace_id}/tiles", headers=owner, json={"kind": "text", "spec": {}}
        )
        assert added.status_code == 409
        assert added.json()["code"] == "invalid_state"
        assert (await api.post(f"/v1/tiles/{tile['id']}:run", headers=owner)).status_code == 409

        back = await api.patch(
            f"/v1/workspaces/{workspace_id}",
            headers=owner,
            json={"status": "active", "params": {"region": "Bắc"}},
        )
        assert back.status_code == 200, back.text
        detail = (await api.get(f"/v1/workspaces/{workspace_id}", headers=owner)).json()
        # New session parameters put every tile up for a rerun (UC-02-01 step 4).
        assert detail["params"] == {"region": "Bắc"} and detail["tiles"][0]["stale"] is True
        assert [(e, p["status"]) for e, _, p in _events(seed, workspace_id)] == [
            ("workspace.status_changed", "archived"),
            ("workspace.status_changed", "active"),
        ]


@pytest.mark.req("AC-02-03-01")
async def test_sharing_grants_levels_and_revocation(team: Team, seed: Seed) -> None:
    owner, peer = _as(team, "owner"), _as(team, "peer")
    async with _api(team) as api:
        workspace_id = await _workspace(api, team)
        tile = await _tile(api, team, workspace_id)
        grant = {"principal_type": "user", "principal_id": str(team.peer), "permission": "view"}
        shared = await api.put(
            f"/v1/workspaces/{workspace_id}/shares",
            headers=owner,
            json={"shares": [{**grant, "expires_at": "2099-01-01T00:00:00Z"}]},
        )
        assert shared.status_code == 200, shared.text
        [share] = shared.json()["items"]
        assert share["permission"] == "view" and share["granted_by"] == str(team.owner)

        seen = await api.get(f"/v1/workspaces/{workspace_id}", headers=peer)
        assert seen.status_code == 200, seen.text
        assert seen.json()["permission"] == "view"
        assert {(p["principal_id"], p["role"]) for p in seen.json()["participants"]} == {
            (str(team.owner), "owner"),
            (str(team.peer), "viewer"),
        }
        assert [
            w["id"] for w in (await api.get("/v1/workspaces", headers=peer)).json()["items"]
        ] == [workspace_id]
        # A viewer refreshes (with its own rights, BR-07) but does not edit or comment.
        assert (
            await api.post(f"/v1/workspaces/{workspace_id}:refresh", headers=peer)
        ).status_code == 202
        edit = await api.post(
            f"/v1/workspaces/{workspace_id}/tiles", headers=peer, json={"kind": "text", "spec": {}}
        )
        assert edit.status_code == 403 and edit.json()["code"] == "forbidden"
        comment = await api.post(
            f"/v1/tiles/{tile['id']}/comments", headers=peer, json={"body": "?"}
        )
        assert comment.status_code == 403

        upgraded = await api.put(
            f"/v1/workspaces/{workspace_id}/shares",
            headers=owner,
            json={"shares": [{**grant, "permission": "comment"}]},
        )
        assert upgraded.status_code == 200, upgraded.text
        comment = await api.post(
            f"/v1/tiles/{tile['id']}/comments", headers=peer, json={"body": "Số này lấy từ đâu?"}
        )
        assert comment.status_code == 201, comment.text

        revoked = await api.delete(
            f"/v1/workspaces/{workspace_id}/shares/{team.peer}", headers=owner
        )
        assert revoked.status_code == 204, revoked.text
        assert (await api.get(f"/v1/workspaces/{workspace_id}", headers=peer)).status_code == 404
        again = await api.delete(f"/v1/workspaces/{workspace_id}/shares/{team.peer}", headers=owner)
        assert again.status_code == 404

    events = [e for e, _, _ in _events(seed, share["id"])]
    assert events == [
        "workspace_share.granted",
        "workspace_share.granted",
        "workspace_share.revoked",
    ]


@pytest.mark.req("AC-02-03-06")
async def test_an_expired_share_no_longer_opens_the_workspace(team: Team, seed: Seed) -> None:
    async with _api(team) as api:
        workspace_id = await _workspace(api, team)
        shared = await api.put(
            f"/v1/workspaces/{workspace_id}/shares",
            headers=_as(team, "owner"),
            json={
                "shares": [
                    {"principal_type": "user", "principal_id": str(team.peer), "permission": "edit"}
                ]
            },
        )
        assert shared.status_code == 200, shared.text
        _sql(
            seed,
            "UPDATE workspace.workspace_share SET expires_at = now() - interval '1 minute' WHERE workspace_id = %s",
            workspace_id,
        )
        peer = _as(team, "peer")
        assert (await api.get(f"/v1/workspaces/{workspace_id}", headers=peer)).status_code == 404
        assert (await api.get("/v1/workspaces", headers=peer)).json()["items"] == []


async def test_only_the_owner_decides_who_gets_in(team: Team, seed: Seed) -> None:
    # UC-02-03: an editor who could rewrite the share list could lift their own expiry (BR-89),
    # remove other people or seat new editors.
    owner, peer = _as(team, "owner"), _as(team, "peer")
    async with _api(team) as api:
        workspace_id = await _workspace(api, team)
        grant = {
            "principal_type": "user",
            "principal_id": str(team.peer),
            "permission": "edit",
            "expires_at": "2099-01-01T00:00:00Z",
        }
        shared = await api.put(
            f"/v1/workspaces/{workspace_id}/shares", headers=owner, json={"shares": [grant]}
        )
        assert shared.status_code == 200, shared.text
        lifted = await api.put(
            f"/v1/workspaces/{workspace_id}/shares",
            headers=peer,
            json={"shares": [{**grant, "expires_at": None}]},
        )
        assert lifted.status_code == 403, lifted.text
        removed = await api.delete(
            f"/v1/workspaces/{workspace_id}/shares/{team.peer}", headers=peer
        )
        assert removed.status_code == 403, removed.text
        seated = await api.post(
            f"/v1/workspaces/{workspace_id}/participants",
            headers=peer,
            json={"principal_type": "user", "principal_id": str(uuid.uuid4()), "role": "editor"},
        )
        assert seated.status_code == 403, seated.text
    with seed.conn.cursor() as cur:
        cur.execute(
            "SELECT expires_at FROM workspace.workspace_share WHERE principal_id = %s",
            (str(team.peer),),
        )
        [(expires,)] = cur.fetchall()
    assert expires is not None


async def test_participants(team: Team, seed: Seed) -> None:
    owner = _as(team, "owner")
    agent = str(uuid.uuid4())
    async with _api(team) as api:
        workspace_id = await _workspace(api, team)
        body = {"principal_type": "agent", "principal_id": agent, "role": "agent_helper"}
        added = await api.post(
            f"/v1/workspaces/{workspace_id}/participants", headers=owner, json=body
        )
        assert added.status_code == 201, added.text
        assert added.json()["role"] == "agent_helper"
        twice = await api.post(
            f"/v1/workspaces/{workspace_id}/participants", headers=owner, json=body
        )
        assert twice.status_code == 409

        # An invited editor edits without a share.
        editor = await api.post(
            f"/v1/workspaces/{workspace_id}/participants",
            headers=owner,
            json={"principal_type": "user", "principal_id": str(team.peer), "role": "editor"},
        )
        assert editor.status_code == 201, editor.text
        tile = await api.post(
            f"/v1/workspaces/{workspace_id}/tiles",
            headers=_as(team, "peer"),
            json={"kind": "text", "spec": {}},
        )
        assert tile.status_code == 201, tile.text

    [(event, aggregate, payload)] = _events(seed, added.json()["id"])
    assert (event, aggregate) == ("ws_participant.added", "ws_participant")
    assert payload["principal_id"] == agent and payload["role"] == "agent_helper"


@pytest.mark.req("AC-02-05-01", "AC-02-05-02", "AC-02-05-03")
async def test_comments_mentions_and_assigned_work(team: Team, seed: Seed) -> None:
    owner, peer = _as(team, "owner"), _as(team, "peer")
    agent = str(uuid.uuid4())
    async with _api(team) as api:
        workspace_id = await _workspace(api, team)
        tile = await _tile(api, team, workspace_id)
        await api.post(
            f"/v1/workspaces/{workspace_id}/participants",
            headers=owner,
            json={"principal_type": "agent", "principal_id": agent, "role": "agent_helper"},
        )
        await api.put(
            f"/v1/workspaces/{workspace_id}/shares",
            headers=owner,
            json={
                "shares": [
                    {
                        "principal_type": "user",
                        "principal_id": str(team.peer),
                        "permission": "comment",
                    }
                ]
            },
        )
        created = await api.post(
            f"/v1/tiles/{tile['id']}/comments",
            headers=owner,
            json={
                "body": "Kiểm tra lại số tháng 9",
                "mentions": [str(team.peer), agent],
                "assignee_id": str(team.peer),
                "due_at": "2026-10-15T17:00:00+07:00",
            },
        )
        assert created.status_code == 201, created.text
        comment = created.json()
        assert comment["status"] == "open" and comment["assignee_id"] == str(team.peer)

        reply = await api.post(
            f"/v1/tiles/{tile['id']}/comments",
            headers=peer,
            json={"body": "Đã xem", "parent_id": comment["id"]},
        )
        assert reply.status_code == 201, reply.text
        bad_parent = await api.post(
            f"/v1/tiles/{tile['id']}/comments",
            headers=peer,
            json={"body": "x", "parent_id": str(uuid.uuid4())},
        )
        assert bad_parent.status_code == 422

        # The assignee closes the task but does not rewrite the author's text.
        rewrite = await api.patch(
            f"/v1/comments/{comment['id']}", headers=peer, json={"body": "khác"}
        )
        assert rewrite.status_code == 403 and rewrite.json()["code"] == "forbidden"
        done = await api.patch(
            f"/v1/comments/{comment['id']}", headers=peer, json={"status": "done"}
        )
        assert done.status_code == 200, done.text
        assert done.json()["status"] == "done" and done.json()["resolved_at"] is not None
        # The owner neither wrote the reply nor is assigned to it.
        foreign = await api.patch(
            f"/v1/comments/{reply.json()['id']}", headers=owner, json={"status": "done"}
        )
        assert foreign.status_code == 403

    events = _events(seed, comment["id"])
    assert [e for e, _, _ in events] == ["ws_comment.created", "ws_comment.status_changed"]
    created_payload = events[0][2]
    assert created_payload["agent_mentions"] == [agent]
    assert created_payload["assignee_id"] == str(team.peer)
    assert events[1][2]["status"] == "done"


@pytest.mark.req("AC-02-07-06")
async def test_session_report_checks_and_publication(team: Team, seed: Seed) -> None:
    owner, peer = _as(team, "owner"), _as(team, "peer")
    async with _api(team) as api:
        workspace_id = await _workspace(api, team)
        tile = await _tile(api, team, workspace_id)
        bad = await api.post(
            f"/v1/workspaces/{workspace_id}/reports",
            headers=owner,
            json={"title": "T9", "tile_ids": [str(uuid.uuid4())]},
        )
        assert bad.status_code == 422
        created = await api.post(
            f"/v1/workspaces/{workspace_id}/reports",
            headers=owner,
            json={"title": "Báo cáo T9", "tile_ids": [tile["id"]]},
        )
        assert created.status_code == 201, created.text
        report = created.json()
        assert (report["status"], report["version"]) == ("draft", 1)
        second = await api.post(
            f"/v1/workspaces/{workspace_id}/reports", headers=owner, json={"title": "Bản 2"}
        )
        assert second.json()["version"] == 2

        early = await api.post(f"/v1/session-reports/{report['id']}:publish", headers=peer, json={})
        assert early.status_code == 409 and early.json()["code"] == "invalid_state"

        checking = await api.post(f"/v1/session-reports/{report['id']}:check", headers=owner)
        assert checking.status_code == 202, checking.text
        assert checking.headers["Location"] == f"/v1/session-reports/{report['id']}"
        assert checking.json()["status"] == "checking"
        assert (
            await api.post(f"/v1/session-reports/{report['id']}:check", headers=owner)
        ).status_code == 409

        # The checks worker found a number without grounding.
        _sql(
            seed,
            """UPDATE workspace.session_report SET status = 'ready',
               checks = '{"grounding": {"status": "fail"}, "filed": "pass", "access": "pass", "personal_data": "pass"}'
               WHERE id = %s""",
            report["id"],
        )
        ungrounded = await api.post(
            f"/v1/session-reports/{report['id']}:publish", headers=peer, json={}
        )
        assert (
            ungrounded.status_code == 422 and ungrounded.json()["code"] == "business_rule_violated"
        )

        _sql(
            seed,
            """UPDATE workspace.session_report SET
               checks = '{"grounding": "pass", "filed": "pass", "access": "pass", "personal_data": "pass"}'
               WHERE id = %s""",
            report["id"],
        )
        recipients = {
            "recipients": [{"principal_type": "group", "principal_id": str(uuid.uuid4())}]
        }
        # The author's own :publish only asks for approval.
        asked = await api.post(
            f"/v1/session-reports/{report['id']}:publish", headers=owner, json=recipients
        )
        assert asked.status_code == 200, asked.text
        assert asked.json()["status"] == "pending_approval" and asked.json()["published_at"] is None
        assert (
            await api.post(f"/v1/session-reports/{report['id']}:publish", headers=owner, json={})
        ).status_code == 409

        published = await api.post(
            f"/v1/session-reports/{report['id']}:publish", headers=peer, json=recipients
        )
        assert published.status_code == 200, published.text
        assert published.json()["status"] == "published"
        assert published.json()["approved_by"] == str(team.peer)

    events = _events(seed, report["id"])
    assert [(e, p.get("status")) for e, _, p in events] == [
        ("session_report.created", None),
        ("session_report.status_changed", "checking"),
        ("session_report.status_changed", "pending_approval"),
        ("session_report.status_changed", "published"),
    ]
    assert events[0][2]["tile_ids"] == [tile["id"]]
    assert events[3][2]["recipients"][0]["principal_type"] == "group"


async def test_workspace_isolation_between_orgs(team: Team, seed: Seed) -> None:
    other = _team(seed)
    async with _api(team) as api:
        workspace_id = await _workspace(api, team, "Riêng tư")
    async with _api(other) as other_api:
        response = await other_api.get(
            f"/v1/workspaces/{workspace_id}", headers=_as(other, "owner")
        )
    assert response.status_code == 404, response.text
