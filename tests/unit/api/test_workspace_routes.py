"""Phiên làm việc: request validation and error mapping that need no database, and the pure
rules (access levels, tile dependency graph, report checks) the routes apply."""

from __future__ import annotations

import datetime
import uuid

import pytest
from fastapi.testclient import TestClient

from services.api.modules.workspace import rules

from .conftest import bearer

pytestmark = pytest.mark.unit

SPACE_HEADERS = {**bearer("asker"), "X-Data-Space": "DS-OIL"}
SOME_ID = "01920000-0000-7000-8000-0000000000aa"
NOW = datetime.datetime(2026, 10, 11, 9, 0, tzinfo=datetime.UTC)


def _fields(response) -> set[str]:
    return {e["field"] for e in response.json().get("errors", [])}


# --- request validation --------------------------------------------------------------------------


def test_workspace_routes_need_a_data_space(client: TestClient) -> None:
    response = client.post("/v1/workspaces", headers=bearer("asker"), json={"title": "T9"})
    assert response.status_code == 400
    assert response.json()["code"] == "data_space_required"


def test_create_workspace_requires_title_and_refuses_unknown_fields(client: TestClient) -> None:
    missing = client.post("/v1/workspaces", headers=SPACE_HEADERS, json={})
    assert missing.status_code == 422
    assert missing.json()["code"] == "validation_failed"
    assert "title" in _fields(missing)

    unknown = client.post(
        "/v1/workspaces", headers=SPACE_HEADERS, json={"title": "T9", "owner_id": SOME_ID}
    )
    assert unknown.status_code == 422
    assert "owner_id" in _fields(unknown)


def test_create_tile_requires_kind_and_spec(client: TestClient) -> None:
    response = client.post(
        f"/v1/workspaces/{SOME_ID}/tiles", headers=SPACE_HEADERS, json={"title": "x"}
    )
    assert response.status_code == 422
    assert {"kind", "spec"} <= _fields(response)

    bad_kind = client.post(
        f"/v1/workspaces/{SOME_ID}/tiles", headers=SPACE_HEADERS, json={"kind": "video", "spec": {}}
    )
    assert bad_kind.status_code == 422
    assert "kind" in _fields(bad_kind)


def test_a_person_cannot_mark_a_tile_suggested(client: TestClient) -> None:
    response = client.patch(
        f"/v1/tiles/{SOME_ID}", headers=SPACE_HEADERS, json={"agent_suggestion_state": "suggested"}
    )
    assert response.status_code == 422
    assert "agent_suggestion_state" in _fields(response)


def test_patch_workspace_refuses_null_title(client: TestClient) -> None:
    response = client.patch(
        f"/v1/workspaces/{SOME_ID}", headers=SPACE_HEADERS, json={"title": None}
    )
    assert response.status_code == 422
    assert _fields(response) == {"title"}


def test_shares_put_takes_the_whole_set(client: TestClient) -> None:
    missing = client.put(f"/v1/workspaces/{SOME_ID}/shares", headers=SPACE_HEADERS, json={})
    assert missing.status_code == 422
    assert "shares" in _fields(missing)

    grant = {"principal_type": "user", "principal_id": SOME_ID, "permission": "view"}
    twice = client.put(
        f"/v1/workspaces/{SOME_ID}/shares", headers=SPACE_HEADERS, json={"shares": [grant, grant]}
    )
    assert twice.status_code == 422

    past = client.put(
        f"/v1/workspaces/{SOME_ID}/shares",
        headers=SPACE_HEADERS,
        json={"shares": [{**grant, "expires_at": "2020-01-01T00:00:00Z"}]},
    )
    assert past.status_code == 422
    assert _fields(past) == {"shares.0.expires_at"}


@pytest.mark.req("AC-02-03-05")
def test_shares_reach_people_and_groups_only(client: TestClient) -> None:
    response = client.put(
        f"/v1/workspaces/{SOME_ID}/shares",
        headers=SPACE_HEADERS,
        json={
            "shares": [{"principal_type": "email", "principal_id": SOME_ID, "permission": "view"}]
        },
    )
    assert response.status_code == 422


def test_participant_roles(client: TestClient) -> None:
    path = f"/v1/workspaces/{SOME_ID}/participants"
    owner = client.post(
        path,
        headers=SPACE_HEADERS,
        json={"principal_type": "user", "principal_id": SOME_ID, "role": "owner"},
    )
    assert owner.status_code == 422
    assert owner.json()["code"] == "business_rule_violated"

    agent_as_editor = client.post(
        path,
        headers=SPACE_HEADERS,
        json={"principal_type": "agent", "principal_id": SOME_ID, "role": "editor"},
    )
    assert agent_as_editor.status_code == 422
    assert agent_as_editor.json()["code"] == "validation_failed"

    missing = client.post(path, headers=SPACE_HEADERS, json={"principal_type": "user"})
    assert missing.status_code == 422
    assert {"principal_id", "role"} <= _fields(missing)


def test_comment_body_is_required_and_status_not_null(client: TestClient) -> None:
    created = client.post(
        f"/v1/tiles/{SOME_ID}/comments", headers=SPACE_HEADERS, json={"mentions": []}
    )
    assert created.status_code == 422
    assert "body" in _fields(created)

    patched = client.patch(f"/v1/comments/{SOME_ID}", headers=SPACE_HEADERS, json={"status": None})
    assert patched.status_code == 422
    assert _fields(patched) == {"status"}


def test_report_requires_title(client: TestClient) -> None:
    response = client.post(
        f"/v1/workspaces/{SOME_ID}/reports", headers=SPACE_HEADERS, json={"tile_ids": []}
    )
    assert response.status_code == 422
    assert "title" in _fields(response)


def test_publishing_a_report_needs_approve(client: TestClient) -> None:
    response = client.post(f"/v1/session-reports/{SOME_ID}:publish", headers=SPACE_HEADERS, json={})
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_list_rejects_an_unknown_status(client: TestClient) -> None:
    response = client.get("/v1/workspaces?status=deleted", headers=SPACE_HEADERS)
    assert response.status_code == 422
    assert "status" in _fields(response)


# --- rules ---------------------------------------------------------------------------------------


def _level(**kwargs) -> str | None:
    base = {
        "is_owner": False,
        "share_permission": None,
        "share_expires_at": None,
        "participant_role": None,
        "now": NOW,
    }
    return rules.permission_of(**{**base, **kwargs})


def test_owner_outranks_everything() -> None:
    assert _level(is_owner=True, share_permission="view") == "owner"


@pytest.mark.req("AC-02-03-06")
def test_an_expired_share_grants_nothing_even_with_a_seat() -> None:
    expired = NOW - datetime.timedelta(seconds=1)
    assert (
        _level(share_permission="edit", share_expires_at=expired, participant_role="editor") is None
    )
    later = NOW + datetime.timedelta(days=3)
    assert _level(share_permission="comment", share_expires_at=later) == "comment"


def test_a_seat_without_a_share_grants_its_role() -> None:
    assert _level(participant_role="editor") == "edit"
    assert _level(participant_role="viewer") == "view"
    assert _level(participant_role="agent_helper") is None
    assert _level() is None


def test_levels_are_ordered() -> None:
    assert rules.at_least("owner", "edit")
    assert rules.at_least("comment", "view")
    assert not rules.at_least("view", "comment")


def _ids(n: int) -> list[uuid.UUID]:
    return [uuid.UUID(int=i + 1) for i in range(n)]


@pytest.mark.req("AC-02-02-01")
def test_dependencies_run_first_and_ties_keep_notebook_order() -> None:
    a, b, c, d = _ids(4)
    # b depends on a but sits above it in the notebook; once a has run, b comes before c
    # because it is higher up; d waits for both.
    tiles = [(b, 0, [a]), (a, 1, []), (c, 2, []), (d, 3, [b, c])]
    assert rules.topological_order(tiles) == [a, b, c, d]


def test_a_cycle_is_refused_and_deleted_dependencies_ignored() -> None:
    a, b, gone = _ids(3)
    with pytest.raises(rules.CycleError):
        rules.topological_order([(a, 0, [b]), (b, 1, [a])])
    assert rules.topological_order([(a, 0, [gone])]) == [a]


@pytest.mark.req("AC-02-06-02")
def test_dependents_are_transitive() -> None:
    a, b, c, d = _ids(4)
    graph = {a: [], b: [a], c: [b], d: []}
    assert rules.dependents(graph, a) == {b, c}
    assert rules.dependents(graph, d) == set()


def test_creates_cycle() -> None:
    a, b, c = _ids(3)
    graph = {a: [], b: [a], c: [b]}
    assert rules.creates_cycle(graph, a, [c])
    assert rules.creates_cycle(graph, a, [a])
    assert not rules.creates_cycle(graph, c, [a])


@pytest.mark.req("AC-02-07-02")
def test_failed_checks() -> None:
    assert rules.failed_checks({}) == []
    assert (
        rules.failed_checks({"grounding": "pass", "access": {"status": "pass", "items": 3}}) == []
    )
    assert rules.failed_checks(
        {"grounding": {"status": "fail"}, "personal_data": "pass", "filed": "x"}
    ) == [
        "filed",
        "grounding",
    ]
