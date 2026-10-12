"""API-04 "Cần bạn và đánh giá" against PostgreSQL: HITL, proposals, evaluation, Error Book.

Each test builds its own organisation with three users in one Data Space: a reviewer, a second
reviewer holding `compliance` (AC06), and a proposer. Acceptance criteria are named in comments
(tests/design_checklist.py reads them there; the req marker does not take AC ids yet).
"""

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

CAPS = ["ask", "approve", "steward"]


@dataclass
class Team:
    org: uuid.UUID
    space: uuid.UUID
    other_space: uuid.UUID
    users: dict[str, uuid.UUID]
    seed: Seed

    def headers(self, who: str, space: uuid.UUID | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer kc-{who}", "X-Data-Space": str(space or self.space)}

    def events(self, schema: str, aggregate_id: object) -> list[tuple[str, dict[str, Any]]]:
        with self.seed.conn.cursor() as cur:
            cur.execute(
                f"SELECT event_type, payload FROM {schema}.outbox WHERE aggregate_id = %s ORDER BY created_at",
                (str(aggregate_id),),
            )
            return list(cur.fetchall())


def _user(
    seed: Seed, org: uuid.UUID, name: str, role: uuid.UUID, spaces: list[uuid.UUID]
) -> uuid.UUID:
    user = seed.row(
        "org.app_user",
        org_id=org,
        external_id=f"kc-{name}",
        username=name,
        display_name=name,
        status="active",
    )
    seed.row("org.user_role", org_id=org, user_id=user, role_id=role, source="manual")
    for space in spaces:
        seed.row(
            "org.data_space_member",
            org_id=org,
            data_space_id=space,
            principal_type="user",
            principal_id=user,
            role_id=role,
        )
    return user


@pytest.fixture
def team(seed: Seed) -> Team:
    org = seed.org()
    role = seed.row("org.role", org_id=org, code="REVIEW", name="Review", capabilities=CAPS)
    compliance = seed.row(
        "org.role", org_id=org, code="AC06", name="Control", capabilities=[*CAPS, "compliance"]
    )
    steward = seed.row(
        "org.app_user",
        org_id=org,
        external_id=f"kc-owner-{org.hex[:6]}",
        username="owner",
        display_name="Owner",
    )
    space = seed.row(
        "org.data_space", org_id=org, code="DS-API04", name="API-04", steward_id=steward
    )
    other = seed.row(
        "org.data_space", org_id=org, code="DS-API04-B", name="API-04 B", steward_id=steward
    )
    users = {
        "reviewer": _user(seed, org, "reviewer", role, [space, other]),
        "controller": _user(seed, org, "controller", compliance, [space]),
        "proposer": _user(seed, org, "proposer", role, [space]),
    }
    users["steward"] = steward
    return Team(org=org, space=space, other_space=other, users=users, seed=seed)


class Tokens:
    def __init__(self, team: Team) -> None:
        self.team = team

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.team.org)}


def client(team: Team) -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=Tokens(team))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


def _hitl(team: Team, **values: Any) -> uuid.UUID:
    values.setdefault("kind", "clarification")
    values.setdefault("title", "Cần làm rõ phạm vi")
    values.setdefault("source_service", "agent")
    values.setdefault("data_space_id", team.space)
    if "options" in values:
        values["options"] = Json(values["options"])
    return team.seed.row("inbox.hitl_request", org_id=team.org, **values)


def _proposal(team: Team, **values: Any) -> uuid.UUID:
    values.setdefault("kind", "synonym")
    values.setdefault("target_service", "context")
    values.setdefault("title", "Thêm từ đồng nghĩa")
    values.setdefault("proposed_by", team.users["proposer"])
    values.setdefault("status", "pending_review")
    return team.seed.row("inbox.proposal", org_id=team.org, data_space_id=team.space, **values)


async def test_inbox_lists_what_waits_for_the_caller(team: Team) -> None:
    mine = _hitl(team, assignee_id=team.users["reviewer"], kind="checkpoint")
    group = team.seed.row(
        "org.user_group", org_id=team.org, external_id="g-1", name="GRP-REVIEW", source="local"
    )
    team.seed.row(
        "org.user_group_member", org_id=team.org, group_id=group, user_id=team.users["reviewer"]
    )
    via_group = _hitl(team, assignee_group="GRP-REVIEW")
    not_mine = _hitl(team, assignee_id=team.users["proposer"])
    done = _hitl(
        team, assignee_id=team.users["reviewer"], status="answered", resolved_at="2026-10-01"
    )
    elsewhere = _hitl(team, assignee_id=team.users["reviewer"], data_space_id=team.other_space)
    async with client(team) as api:
        listed = await api.get("/v1/inbox", headers=team.headers("reviewer"))
        assert listed.status_code == 200, listed.text
        ids = {item["id"] for item in listed.json()["items"]}
        assert ids == {str(mine), str(via_group)}
        assert not ids & {str(not_mine), str(done), str(elsewhere)}

        checkpoints = await api.get("/v1/inbox?kind=checkpoint", headers=team.headers("reviewer"))
        assert [i["id"] for i in checkpoints.json()["items"]] == [str(mine)]
        answered = await api.get("/v1/inbox?status=answered", headers=team.headers("reviewer"))
        assert [i["id"] for i in answered.json()["items"]] == [str(done)]
        theirs = await api.get(
            f"/v1/inbox?assignee={team.users['proposer']}", headers=team.headers("reviewer")
        )
        assert [i["id"] for i in theirs.json()["items"]] == [str(not_mine)]

        paged = await api.get("/v1/inbox?page_size=1", headers=team.headers("reviewer"))
        token = paged.json()["next_page_token"]
        assert token
        rest = await api.get(
            f"/v1/inbox?page_size=1&page_token={token}", headers=team.headers("reviewer")
        )
        assert len(paged.json()["items"]) == len(rest.json()["items"]) == 1
        assert paged.json()["items"][0]["id"] != rest.json()["items"][0]["id"]

        # Another Data Space's request is not found from this one.
        hidden = await api.get(f"/v1/hitl-requests/{elsewhere}", headers=team.headers("reviewer"))
        assert hidden.status_code == 404, hidden.text


async def test_resolve_records_the_decision_once(team: Team) -> None:
    # AC-07-07-01 (API part): the chosen option is recorded and hitl.resolved resumes the run.
    # AC-07-07-06: a second answer finds the request resolved.
    request = _hitl(
        team,
        assignee_id=team.users["reviewer"],
        kind="checkpoint",
        options=[{"id": "core_banking"}, {"id": "ledger"}],
        source_ref=uuid.uuid4(),
    )
    async with client(team) as api:
        wrong = await api.post(
            f"/v1/hitl-requests/{request}:resolve",
            headers=team.headers("reviewer"),
            json={"decision": "answer", "option": "made_up"},
        )
        assert wrong.status_code == 422, wrong.text

        resolved = await api.post(
            f"/v1/hitl-requests/{request}:resolve",
            headers=team.headers("reviewer"),
            json={
                "decision": "approve",
                "option": "core_banking",
                "note": "Dùng Core",
                "remember_as_rule": True,
            },
        )
        assert resolved.status_code == 200, resolved.text
        body = resolved.json()
        assert body["status"] == "approved"
        assert body["resolved_by"] == str(team.users["reviewer"])
        assert body["response"] == {
            "decision": "approve",
            "option": "core_banking",
            "note": "Dùng Core",
        }
        assert body["remember_as_rule"] is True

        again = await api.post(
            f"/v1/hitl-requests/{request}:resolve",
            headers=team.headers("reviewer"),
            json={"decision": "reject"},
        )
        assert again.status_code == 409, again.text
        assert again.json()["code"] == "invalid_state"

    [(event_type, payload)] = team.events("inbox", request)
    assert event_type == "hitl.resolved"
    assert payload["status"] == "approved"
    assert payload["remember_as_rule"] is True
    assert "note" not in payload


async def test_only_the_assignee_resolves(team: Team) -> None:
    request = _hitl(team, assignee_id=team.users["proposer"])
    async with client(team) as api:
        refused = await api.post(
            f"/v1/hitl-requests/{request}:resolve",
            headers=team.headers("reviewer"),
            json={"decision": "answer"},
        )
    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == "forbidden"


async def test_a_cancelled_or_expired_request_cannot_be_resolved(team: Team) -> None:
    # AC-07-07-07: the run was cancelled while the card waited.
    cancelled = _hitl(
        team, assignee_id=team.users["reviewer"], status="cancelled", resolved_at="2026-10-01"
    )
    expired = _hitl(
        team, assignee_id=team.users["reviewer"], status="expired", resolved_at="2026-10-01"
    )
    async with client(team) as api:
        for request in (cancelled, expired):
            response = await api.post(
                f"/v1/hitl-requests/{request}:resolve",
                headers=team.headers("reviewer"),
                json={"decision": "answer"},
            )
            assert response.status_code == 409, response.text
            assert response.json()["code"] == "invalid_state"


async def test_reassign_hands_the_request_over(team: Team) -> None:
    request = _hitl(team, assignee_id=team.users["reviewer"])
    async with client(team) as api:
        moved = await api.post(
            f"/v1/hitl-requests/{request}:resolve",
            headers=team.headers("reviewer"),
            json={"decision": "reassign", "reassign_to": str(team.users["controller"])},
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["status"] == "reassigned"
        assert moved.json()["assignee_id"] == str(team.users["controller"])

        # The former assignee no longer decides it; the new one does.
        former = await api.post(
            f"/v1/hitl-requests/{request}:resolve",
            headers=team.headers("reviewer"),
            json={"decision": "answer"},
        )
        assert former.status_code == 403, former.text
        answered = await api.post(
            f"/v1/hitl-requests/{request}:resolve",
            headers=team.headers("controller"),
            json={"decision": "answer"},
        )
        assert answered.status_code == 200, answered.text
        assert answered.json()["status"] == "answered"
    assert [e for e, _ in team.events("inbox", request)] == ["hitl.reassigned", "hitl.resolved"]


async def test_approval_chain_needs_two_people(team: Team) -> None:
    # AC-07-07-04: step 2 opens only once step 1 is decided; BR-132: by another person.
    request = _hitl(team, kind="period_exception", risk="high", assignee_id=team.users["reviewer"])
    for step, role in ((1, "steward"), (2, "compliance")):
        team.seed.row(
            "inbox.hitl_approval_step",
            org_id=team.org,
            hitl_request_id=request,
            step=step,
            approver_role=role,
        )
    async with client(team) as api:
        first = await api.post(
            f"/v1/hitl-requests/{request}:resolve",
            headers=team.headers("reviewer"),
            json={"decision": "approve"},
        )
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "open"
        assert [s["decision"] for s in first.json()["approval_steps"]] == ["approve", None]

        same = await api.post(
            f"/v1/hitl-requests/{request}:resolve",
            headers=team.headers("reviewer"),
            json={"decision": "approve"},
        )
        assert same.status_code == 422, same.text
        assert same.json()["code"] == "business_rule_violated"

        # The reviewer hands step 2 to the controller, who approves it.
        await api.post(
            f"/v1/hitl-requests/{request}:resolve",
            headers=team.headers("reviewer"),
            json={"decision": "reassign", "reassign_to": str(team.users["controller"])},
        )
        second = await api.post(
            f"/v1/hitl-requests/{request}:resolve",
            headers=team.headers("controller"),
            json={"decision": "approve"},
        )
        assert second.status_code == 200, second.text
        assert second.json()["status"] == "approved"
        assert [s["approver_id"] for s in second.json()["approval_steps"]] == [
            str(team.users["reviewer"]),
            str(team.users["controller"]),
        ]
    assert [e for e, _ in team.events("inbox", request)] == [
        "hitl.step_decided",
        "hitl.reassigned",
        "hitl.resolved",
    ]


async def test_the_proposer_cannot_decide(team: Team) -> None:
    # AC-07-03-08
    proposal = _proposal(team)
    async with client(team) as api:
        response = await api.post(
            f"/v1/proposals/{proposal}:decide",
            headers=team.headers("proposer"),
            json={"decision": "accept"},
        )
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "forbidden"


async def test_reject_needs_a_reason(team: Team) -> None:
    # AC-07-03-02
    proposal = _proposal(team)
    async with client(team) as api:
        bare = await api.post(
            f"/v1/proposals/{proposal}:decide",
            headers=team.headers("reviewer"),
            json={"decision": "reject", "note": " "},
        )
        assert bare.status_code == 422, bare.text
        assert bare.json()["code"] == "business_rule_violated"
        rejected = await api.post(
            f"/v1/proposals/{proposal}:decide",
            headers=team.headers("reviewer"),
            json={"decision": "reject", "note": "Trùng từ đã có"},
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["status"] == "rejected"
        assert rejected.json()["reviews"][0]["note"] == "Trùng từ đã có"
        closed = await api.post(
            f"/v1/proposals/{proposal}:decide",
            headers=team.headers("reviewer"),
            json={"decision": "accept"},
        )
        assert closed.status_code == 409, closed.text


async def test_accepting_a_regression_needs_each_item_reviewed(team: Team) -> None:
    # AC-07-03-06 (BR-28) and AC-07-03-01 (API part: proposal_review and proposal.decided).
    eval_set = team.seed.row(
        "eval.eval_set",
        org_id=team.org,
        data_space_id=team.space,
        subject_type="context",
        name="Bộ chuẩn",
    )
    item = team.seed.row(
        "eval.eval_item",
        org_id=team.org,
        eval_set_id=eval_set,
        input=Json({"q": "a"}),
        expected=Json({"v": 1}),
    )
    run = team.seed.row(
        "eval.eval_run",
        org_id=team.org,
        eval_set_id=eval_set,
        subject_version="context@draft",
        trigger="proposal",
        item_count=92,
        passed=90,
        failed=2,
        regressions=2,
        status="completed",
    )
    team.seed.row(
        "eval.eval_result",
        org_id=team.org,
        eval_run_id=run,
        eval_item_id=item,
        passed=False,
        changed_from_baseline=True,
    )
    proposal = _proposal(team, eval_run_id=run)
    async with client(team) as api:
        detail = await api.get(f"/v1/proposals/{proposal}", headers=team.headers("reviewer"))
        assert detail.status_code == 200, detail.text
        assert detail.json()["eval_run"]["regressions"] == 2
        assert [r["eval_item_id"] for r in detail.json()["changed_results"]] == [str(item)]

        blocked = await api.post(
            f"/v1/proposals/{proposal}:decide",
            headers=team.headers("reviewer"),
            json={"decision": "accept"},
        )
        assert blocked.status_code == 422, blocked.text
        assert blocked.json()["code"] == "business_rule_violated"

        accepted = await api.post(
            f"/v1/proposals/{proposal}:decide",
            headers=team.headers("reviewer"),
            json={
                "decision": "accept_with_edit",
                "reviewed_regressions": True,
                "edited_diff": {"add": ["xăng"]},
            },
        )
        assert accepted.status_code == 200, accepted.text
        # An edit is content nobody else has reviewed: it waits for another person (BR-62).
        assert accepted.json()["status"] == "pending_review"
        assert accepted.json()["diff"] == {"add": ["xăng"]}
        assert accepted.json()["reviews"][0]["reviewed_regressions"] is True

        own_edit = await api.post(
            f"/v1/proposals/{proposal}:decide",
            headers=team.headers("reviewer"),
            json={"decision": "accept", "reviewed_regressions": True},
        )
        assert own_edit.status_code == 403, own_edit.text

        final = await api.post(
            f"/v1/proposals/{proposal}:decide",
            headers=team.headers("controller"),
            json={"decision": "accept", "reviewed_regressions": True},
        )
        assert final.status_code == 200, final.text
        assert final.json()["status"] == "accepted"
    [(edited, _), (event_type, payload)] = team.events("inbox", proposal)
    assert edited == "proposal.step_decided"
    assert event_type == "proposal.decided"
    assert payload["status"] == "accepted"
    assert payload["target_service"] == "context"


async def test_a_change_to_a_filed_report_waits_for_the_controller(team: Team) -> None:
    # AC-07-03-03 (BR-119): the first acceptance does not publish; AC06, another person, does.
    proposal = _proposal(team, kind="metric_change", requires_compliance=True)
    async with client(team) as api:
        first = await api.post(
            f"/v1/proposals/{proposal}:decide",
            headers=team.headers("reviewer"),
            json={"decision": "accept"},
        )
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "pending_review"

        without_compliance = await api.post(
            f"/v1/proposals/{proposal}:decide",
            headers=team.headers("reviewer"),
            json={"decision": "accept"},
        )
        assert without_compliance.status_code == 403, without_compliance.text

        second = await api.post(
            f"/v1/proposals/{proposal}:decide",
            headers=team.headers("controller"),
            json={"decision": "accept"},
        )
        assert second.status_code == 200, second.text
        assert second.json()["status"] == "accepted"
    assert [e for e, _ in team.events("inbox", proposal)] == [
        "proposal.step_decided",
        "proposal.decided",
    ]


async def test_eval_items_and_runs(team: Team) -> None:
    eval_set = team.seed.row(
        "eval.eval_set",
        org_id=team.org,
        data_space_id=team.space,
        subject_type="context",
        name="Bộ chuẩn",
    )
    archived = team.seed.row(
        "eval.eval_set",
        org_id=team.org,
        data_space_id=team.space,
        subject_type="context",
        name="Bộ cũ",
        status="archived",
    )
    async with client(team) as api:
        item = await api.post(
            f"/v1/eval-sets/{eval_set}/items",
            headers=team.headers("reviewer"),
            json={
                "input": {"q": "Giá RON95?"},
                "expected": {"value": 24000},
                "tolerance": 0.5,
                "as_of": "2026-07-02",
            },
        )
        assert item.status_code == 201, item.text
        assert item.json()["origin"] == "manual"
        assert item.json()["as_of"] == "2026-07-02"

        run = await api.post(f"/v1/eval-sets/{eval_set}/runs", headers=team.headers("reviewer"))
        assert run.status_code == 202, run.text
        run_id = run.json()["id"]
        assert run.headers["location"] == f"/v1/eval-runs/{run_id}"
        assert run.json()["status"] == "running"
        assert run.json()["poll_after_ms"] > 0

        named = await api.post(
            f"/v1/eval-sets/{eval_set}/runs",
            headers=team.headers("reviewer"),
            json={"subject_version": "context@v7"},
        )
        assert named.status_code == 202, named.text

        fetched = await api.get(f"/v1/eval-runs/{run_id}", headers=team.headers("reviewer"))
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["item_count"] == 1
        assert fetched.json()["subject_version"] == "current"
        assert fetched.json()["results"] == []
        other = await api.get(
            f"/v1/eval-runs/{run_id}", headers=team.headers("reviewer", team.other_space)
        )
        assert other.status_code == 404, other.text

        refused = await api.post(f"/v1/eval-sets/{archived}/runs", headers=team.headers("reviewer"))
        assert refused.status_code == 409, refused.text
        assert refused.json()["code"] == "invalid_state"
    [(event_type, payload)] = team.events("eval", uuid.UUID(run_id))
    assert event_type == "eval_run.requested"
    assert payload["eval_set_id"] == str(eval_set)


async def test_error_book_lifecycle(team: Team) -> None:
    # AC-07-14-01 (API part), AC-07-14-06, AC-07-14-07, AC-07-14-04 (dismiss), AC-07-14-08.
    eval_set = team.seed.row(
        "eval.eval_set",
        org_id=team.org,
        data_space_id=team.space,
        subject_type="context",
        name="Bộ chuẩn",
    )
    feedback = uuid.uuid4()
    report = {
        "source_kind": "user_report",
        "data_space_id": str(team.space),
        "feedback_id": str(feedback),
        "answer_id": str(uuid.uuid4()),
        "title": "Mức giảm E5RON92 kỳ 02/07/2026 ghi 1.155 đ/lít",
        "detail": {
            "metric_code": "muc_thay_doi_gia",
            "input": {"question": "Kỳ 02/07/2026 E5RON92 giảm bao nhiêu?"},
            "expected": {"value": 1055, "unit": "đ/lít"},
            "as_of": "2026-07-02",
        },
    }
    h = team.headers("reviewer")
    async with client(team) as api:
        created = await api.post("/v1/error-book-entries", headers=h, json=report)
        assert created.status_code == 201, created.text
        entry = created.json()
        assert entry["entry_no"] == "EB-0001"
        assert entry["status"] == "open"
        # Default assignee: the Data Space's steward (no data product given).
        assert entry["assignee_id"] == str(team.users["steward"])

        again = await api.post("/v1/error-book-entries", headers=h, json=report)
        assert again.status_code == 200, again.text
        assert again.json()["id"] == entry["id"]

        listed = await api.get(
            "/v1/error-book-entries?metric_code=muc_thay_doi_gia&status=open", headers=h
        )
        assert [e["id"] for e in listed.json()["items"]] == [entry["id"]]
        none = await api.get("/v1/error-book-entries?source_kind=forecast_miss", headers=h)
        assert none.json()["items"] == []

        early = await api.post(
            f"/v1/error-book-entries/{entry['id']}:mark-fixed",
            headers=h,
            json={"fix_kind": "code", "fix_ref": "abc123"},
        )
        assert early.status_code == 422, early.text
        assert early.json()["code"] == "business_rule_violated"

        not_yet = await api.post(
            f"/v1/error-book-entries/{entry['id']}:propose-to-pack",
            headers=h,
            json={"pack_id": str(uuid.uuid4()), "summary": "x"},
        )
        assert not_yet.status_code == 409, not_yet.text

        patched = await api.patch(
            f"/v1/error-book-entries/{entry['id']}",
            headers=h,
            json={
                "error_kind": "formula",
                "root_cause": "Kỳ liền trước tính theo tuần lịch",
                "severity": "high",
            },
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["severity"] == "high"

        fixed = await api.post(
            f"/v1/error-book-entries/{entry['id']}:mark-fixed",
            headers=h,
            json={
                "fix_kind": "metric_version",
                "fix_proposal_id": str(uuid.uuid4()),
                "note": "Đề xuất đã duyệt",
            },
        )
        assert fixed.status_code == 200, fixed.text
        assert fixed.json()["status"] == "fixed"
        assert fixed.json()["fixed_by"] == str(team.users["reviewer"])
        assert fixed.json()["eval_item_id"] is not None

        detail = await api.get(f"/v1/error-book-entries/{entry['id']}", headers=h)
        assert detail.status_code == 200, detail.text
        case = detail.json()["eval_item"]
        assert case["origin"] == "error_book"
        assert case["origin_ref"] == entry["id"]
        assert case["eval_set_id"] == str(eval_set)
        assert case["expected"] == {"value": 1055, "unit": "đ/lít"}
        assert case["as_of"] == "2026-07-02"

        twice = await api.post(f"/v1/error-book-entries/{entry['id']}:add-regression", headers=h)
        assert twice.status_code == 409, twice.text

        proposed = await api.post(
            f"/v1/error-book-entries/{entry['id']}:propose-to-pack",
            headers=h,
            json={"pack_id": str(uuid.uuid4()), "summary": "Kỳ liền trước theo lịch điều hành"},
        )
        assert proposed.status_code == 200, proposed.text
        assert proposed.json()["pack_contribution"] == "proposed"
        assert proposed.json()["pack_proposal_ref"]

        dismissed_late = await api.post(
            f"/v1/error-book-entries/{entry['id']}:dismiss",
            headers=h,
            json={"reason": "Không phải lỗi"},
        )
        assert dismissed_late.status_code == 409, dismissed_late.text

    events = team.events("eval", uuid.UUID(entry["id"]))
    assert [e for e, _ in events] == [
        "eval.error_book.opened",
        "error_book_entry.fixed",
        "error_book_entry.proposed_to_pack",
    ]
    pack_payload = events[2][1]
    # UC-09-05: the customer's question, numbers and title do not leave with the proposal.
    assert not {"title", "detail", "root_cause"} & set(pack_payload)


async def test_add_regression_and_dismiss(team: Team) -> None:
    team.seed.row(
        "eval.eval_set",
        org_id=team.org,
        data_space_id=team.space,
        subject_type="context",
        name="Bộ chuẩn",
    )
    h = team.headers("reviewer")
    base = {"data_space_id": str(team.space), "title": "Bỏ câu chứa số không truy được"}
    async with client(team) as api:
        first = (
            await api.post(
                "/v1/error-book-entries",
                headers=h,
                json={**base, "source_kind": "number_check", "answer_id": str(uuid.uuid4())},
            )
        ).json()
        second = (
            await api.post(
                "/v1/error-book-entries",
                headers=h,
                json={**base, "source_kind": "number_check", "answer_id": str(uuid.uuid4())},
            )
        ).json()
        assert second["entry_no"] == "EB-0002"

        open_case = await api.post(
            f"/v1/error-book-entries/{first['id']}:add-regression", headers=h
        )
        assert open_case.status_code == 409, open_case.text

        await api.patch(
            f"/v1/error-book-entries/{first['id']}",
            headers=h,
            json={"error_kind": "model", "root_cause": "Mô hình viết sai số"},
        )
        fixed = await api.post(
            f"/v1/error-book-entries/{first['id']}:mark-fixed",
            headers=h,
            json={"fix_kind": "prompt", "fix_ref": "prompt v4"},
        )
        # The entry recorded no case: the steward supplies it.
        assert fixed.json()["eval_item_id"] is None
        missing = await api.post(f"/v1/error-book-entries/{first['id']}:add-regression", headers=h)
        assert missing.status_code == 422, missing.text
        added = await api.post(
            f"/v1/error-book-entries/{first['id']}:add-regression",
            headers=h,
            json={"input": {"question": "Tổng dư nợ?"}, "expected": {"behavior": "refuse"}},
        )
        assert added.status_code == 200, added.text
        assert added.json()["eval_item_id"] is not None
        assert added.json()["status"] == "fixed"

        duplicate = await api.post(
            f"/v1/error-book-entries/{second['id']}:dismiss",
            headers=h,
            json={"duplicate_of_id": first["id"]},
        )
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["status"] == "dismissed"
        assert duplicate.json()["duplicate_of_id"] == first["id"]

        frozen = await api.patch(
            f"/v1/error-book-entries/{second['id']}", headers=h, json={"severity": "low"}
        )
        assert frozen.status_code == 409, frozen.text
        assert frozen.json()["code"] == "conflict"
    assert [e for e, _ in team.events("eval", uuid.UUID(second["id"]))] == [
        "eval.error_book.opened",
        "error_book_entry.dismissed",
    ]


async def test_a_chain_routed_by_capability_moves_to_the_next_holders(team: Team) -> None:
    # Decided 12/10/2026: a request may go to a capability, and each step names the capability
    # its approver must hold (steward, then compliance), decided by different people (BR-132).
    request = _hitl(team, kind="period_exception", risk="high", assignee_capability="steward")
    for step, role in ((1, "steward"), (2, "compliance")):
        team.seed.row(
            "inbox.hitl_approval_step",
            org_id=team.org,
            hitl_request_id=request,
            step=step,
            approver_role=role,
        )
    async with client(team) as api:
        listed = await api.get("/v1/inbox", headers=team.headers("reviewer"))
        assert str(request) in [i["id"] for i in listed.json()["items"]]
        first = await api.post(
            f"/v1/hitl-requests/{request}:resolve",
            headers=team.headers("reviewer"),
            json={"decision": "approve"},
        )
        assert first.status_code == 200, first.text
        assert first.json()["assignee_capability"] == "compliance"
        # The reviewer holds no compliance: step 2 is not theirs, and no longer in their inbox.
        again = await api.post(
            f"/v1/hitl-requests/{request}:resolve",
            headers=team.headers("reviewer"),
            json={"decision": "approve"},
        )
        assert again.status_code == 403
        assert str(request) not in [
            i["id"]
            for i in (await api.get("/v1/inbox", headers=team.headers("reviewer"))).json()["items"]
        ]
        second = await api.post(
            f"/v1/hitl-requests/{request}:resolve",
            headers=team.headers("controller"),
            json={"decision": "approve"},
        )
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "approved"


async def test_an_org_wide_request_is_decided_on_org_level_capabilities(team: Team) -> None:
    # An audit export over 100 000 rows belongs to no Data Space (data_space_id null).
    request = _hitl(
        team, kind="export", risk="high", data_space_id=None, assignee_capability="compliance"
    )
    async with client(team) as api:
        seen = await api.get("/v1/inbox", headers=team.headers("controller"))
        hidden = await api.get("/v1/inbox", headers=team.headers("reviewer"))
        refused = await api.post(
            f"/v1/hitl-requests/{request}:resolve",
            headers=team.headers("reviewer"),
            json={"decision": "approve"},
        )
        done = await api.post(
            f"/v1/hitl-requests/{request}:resolve",
            headers=team.headers("controller"),
            json={"decision": "approve"},
        )
    assert str(request) in [i["id"] for i in seen.json()["items"]]
    assert seen.json()["items"][0]["data_space_id"] is None
    assert str(request) not in [i["id"] for i in hidden.json()["items"]]
    assert refused.status_code == 404  # not theirs to see, let alone decide
    assert done.status_code == 200 and done.json()["status"] == "approved"


async def test_an_org_wide_request_is_hidden_from_those_it_does_not_concern(team: Team) -> None:
    # An audit export carries its filters in context: no one outside the request may read it.
    request = _hitl(
        team,
        kind="export",
        risk="high",
        data_space_id=None,
        assignee_capability="compliance",
        created_by=team.users["proposer"],
    )
    async with client(team) as api:
        outsider = await api.get(f"/v1/hitl-requests/{request}", headers=team.headers("reviewer"))
        by_id = await api.get(
            "/v1/inbox",
            params={"assignee": str(team.users["controller"])},
            headers=team.headers("reviewer"),
        )
        requester = await api.get(f"/v1/hitl-requests/{request}", headers=team.headers("proposer"))
        approver = await api.get(f"/v1/hitl-requests/{request}", headers=team.headers("controller"))
    assert outsider.status_code == 404
    assert str(request) not in [i["id"] for i in by_id.json()["items"]]
    assert requester.status_code == approver.status_code == 200
