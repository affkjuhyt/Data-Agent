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


@pytest.mark.req("AC-07-06-04")
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
        # The run, not the diff, says how the evaluation went.
        assert detail.json()["summary"]["checks_note"] == (
            "Bộ đánh giá 92 câu: 90 câu đạt với thay đổi này; 2 câu đang đạt sẽ không đạt nữa."
            " Xem từng câu trước khi chấp nhận."
        )

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
        # :decide answers with the summary of the diff as it now stands (no synonym shape left).
        assert accepted.json()["summary"]["headline"] == "Thêm từ đồng nghĩa"
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


@pytest.mark.req("AC-07-06-03")
async def test_eval_answer_change_requires_reason_and_writes_audit_event(team: Team) -> None:
    eval_set = team.seed.row(
        "eval.eval_set",
        org_id=team.org,
        data_space_id=team.space,
        subject_type="context",
        name="Answer history",
    )
    async with client(team) as api:
        item = await api.post(
            f"/v1/eval-sets/{eval_set}/items",
            headers=team.headers("reviewer"),
            json={
                "input": {"question": "Giá xăng kỳ cũ?"},
                "as_of": "2026-07-02",
                "expected": {"value": 24000},
            },
        )
        assert item.status_code == 201, item.text
        item_id = item.json()["id"]
        no_reason = await api.patch(
            f"/v1/eval-items/{item_id}",
            headers=team.headers("reviewer"),
            json={"expected": {"value": 24500}},
        )
        assert no_reason.status_code == 422, no_reason.text
        # A new question under the old answer changes the contract as much as a new answer.
        new_question = await api.patch(
            f"/v1/eval-items/{item_id}",
            headers=team.headers("reviewer"),
            json={"input": {"question": "Dư nợ tháng 10/2026?"}},
        )
        assert new_question.status_code == 422, new_question.text
        changed = await api.patch(
            f"/v1/eval-items/{item_id}",
            headers=team.headers("reviewer"),
            json={
                "expected": {"value": 24500},
                "reason": "Số đã nộp được điều chỉnh theo kỳ chốt mới.",
            },
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["expected"] == {"value": 24500}
    [(event_type, payload)] = team.events("eval", uuid.UUID(item_id))
    assert event_type == "eval_item.answer_changed"
    assert payload["reason"] == "Số đã nộp được điều chỉnh theo kỳ chốt mới."


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
    ]


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


async def test_an_excluded_approver_cannot_even_reassign(team: Team) -> None:
    request = _hitl(
        team,
        kind="export",
        data_space_id=None,
        assignee_capability="compliance",
        context=Json({"excluded_approver_ids": [str(team.users["controller"])]}),
    )
    async with client(team) as api:
        r = await api.post(
            f"/v1/hitl-requests/{request}:resolve",
            headers=team.headers("controller"),
            json={"decision": "reassign", "reassign_to": str(team.users["reviewer"])},
        )
    assert r.status_code == 403


async def test_a_proposal_reaches_cần_bạn_and_leaves_it_once_decided(team: Team) -> None:
    # The worker opens an approve_proposal request beside each proposal (proposals._open): it is
    # what Cần bạn lists. Before it, no reviewer ever saw a proposal there.
    accepted, rejected = _proposal(team), _proposal(team)
    requests = {
        p: _hitl(
            team,
            kind="approve_proposal",
            source_service="inbox",
            source_ref=p,
            assignee_capability="approve",
            created_by=team.users["proposer"],
        )
        for p in (accepted, rejected)
    }
    async with client(team) as api:
        listed = await api.get("/v1/inbox", headers=team.headers("reviewer"))
        assert {i["id"] for i in listed.json()["items"]} >= {str(r) for r in requests.values()}
        for proposal, decision in ((accepted, "accept"), (rejected, "reject")):
            decided = await api.post(
                f"/v1/proposals/{proposal}:decide",
                headers=team.headers("reviewer"),
                json={"decision": decision, "note": "Đã xem", "reviewed_regressions": True},
            )
            assert decided.status_code == 200, decided.text
        still = await api.get("/v1/inbox", headers=team.headers("reviewer"))
    assert not {i["id"] for i in still.json()["items"]} & {str(r) for r in requests.values()}
    with team.seed.conn.cursor() as cur:
        cur.execute(
            "SELECT source_ref, status, resolved_by FROM inbox.hitl_request WHERE kind = 'approve_proposal'"
            " AND org_id = %s",
            (str(team.org),),
        )
        closed = {str(ref): (status, str(by)) for ref, status, by in cur.fetchall()}
    reviewer = str(team.users["reviewer"])
    assert closed == {str(accepted): ("approved", reviewer), str(rejected): ("rejected", reviewer)}


async def test_a_proposals_request_follows_its_chain_and_is_closed_by_the_decision_only(
    team: Team,
) -> None:
    # Resolving the approve_proposal request itself would hide an undecided proposal; and after the
    # first acceptance of a BR-119 change the request is the controller's to see.
    proposal = _proposal(team, kind="metric_change", requires_compliance=True)
    request = _hitl(
        team,
        kind="approve_proposal",
        source_service="inbox",
        source_ref=proposal,
        assignee_capability="approve",
        created_by=team.users["proposer"],
    )
    async with client(team) as api:
        resolved = await api.post(
            f"/v1/hitl-requests/{request}:resolve",
            headers=team.headers("reviewer"),
            json={"decision": "approve"},
        )
        assert resolved.status_code == 409, resolved.text
        assert f"/v1/proposals/{proposal}:decide" in resolved.json()["detail"]
        first = await api.post(
            f"/v1/proposals/{proposal}:decide",
            headers=team.headers("reviewer"),
            json={"decision": "accept"},
        )
        assert first.status_code == 200, first.text
    with team.seed.conn.cursor() as cur:
        cur.execute(
            "SELECT status, assignee_capability FROM inbox.hitl_request WHERE id = %s",
            (str(request),),
        )
        assert cur.fetchone() == ("open", "compliance")


# --- self-approval: the org switch lets the creator take the second half of a step ---------------


async def test_the_requester_cannot_approve_their_own_request(team: Team) -> None:
    request = _hitl(team, assignee_id=team.users["reviewer"], created_by=team.users["reviewer"])
    async with client(team) as api:
        r = await api.post(
            f"/v1/hitl-requests/{request}:resolve",
            headers=team.headers("reviewer"),
            json={"decision": "approve"},
        )
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "The requester cannot approve their own request."


async def test_hitl_approve_self_approved_when_the_org_allows_it(
    team: Team, self_approvals: Any
) -> None:
    team.seed.allow_self_approval(team.org)
    request = _hitl(team, assignee_id=team.users["reviewer"], created_by=team.users["reviewer"])
    async with client(team) as api:
        r = await api.post(
            f"/v1/hitl-requests/{request}:resolve",
            headers=team.headers("reviewer"),
            json={"decision": "approve"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    assert self_approvals(team.org) == ["hitl.approve"]


async def test_hitl_chain_step_self_approved_when_the_org_allows_it(
    team: Team, self_approvals: Any
) -> None:
    team.seed.allow_self_approval(team.org)
    request = _hitl(team, kind="period_exception", risk="high", assignee_id=team.users["reviewer"])
    for step in (1, 2):
        team.seed.row(
            "inbox.hitl_approval_step",
            org_id=team.org,
            hitl_request_id=request,
            step=step,
            approver_role="steward",
        )
    async with client(team) as api:
        first = await api.post(
            f"/v1/hitl-requests/{request}:resolve",
            headers=team.headers("reviewer"),
            json={"decision": "approve"},
        )
        assert first.status_code == 200, first.text
        assert self_approvals(team.org) == []
        second = await api.post(
            f"/v1/hitl-requests/{request}:resolve",
            headers=team.headers("reviewer"),
            json={"decision": "approve"},
        )
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "approved"
    assert self_approvals(team.org) == ["hitl.chain_step"]


async def test_hitl_excluded_approver_self_approved_when_the_org_allows_it(
    team: Team, self_approvals: Any
) -> None:
    team.seed.allow_self_approval(team.org)
    request = _hitl(
        team,
        kind="export",
        data_space_id=None,
        assignee_capability="compliance",
        context=Json({"excluded_approver_ids": [str(team.users["controller"])]}),
    )
    async with client(team) as api:
        r = await api.post(
            f"/v1/hitl-requests/{request}:resolve",
            headers=team.headers("controller"),
            json={"decision": "approve"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    assert self_approvals(team.org) == ["hitl.excluded_approver"]


async def test_hitl_chain_step_reject_is_logged_when_the_org_allows_it(
    team: Team, self_approvals: Any
) -> None:
    team.seed.allow_self_approval(team.org)
    request = _hitl(team, kind="period_exception", risk="high", assignee_id=team.users["reviewer"])
    for step in (1, 2):
        team.seed.row(
            "inbox.hitl_approval_step",
            org_id=team.org,
            hitl_request_id=request,
            step=step,
            approver_role="steward",
        )
    async with client(team) as api:
        for decision, expected in (("approve", []), ("reject", ["hitl.chain_step"])):
            r = await api.post(
                f"/v1/hitl-requests/{request}:resolve",
                headers=team.headers("reviewer"),
                json={"decision": decision},
            )
            assert r.status_code == 200, r.text
            assert self_approvals(team.org) == expected
    assert r.json()["status"] == "rejected"


async def test_hitl_chain_step_refused_for_the_role_leaves_no_row(
    team: Team, self_approvals: Any
) -> None:
    # Step 2 wants compliance, which the reviewer does not hold: refused, and nothing is logged.
    team.seed.allow_self_approval(team.org)
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
        again = await api.post(
            f"/v1/hitl-requests/{request}:resolve",
            headers=team.headers("reviewer"),
            json={"decision": "approve"},
        )
    assert again.status_code == 403, again.text
    assert self_approvals(team.org) == []


async def test_hitl_excluded_approver_reject_and_reassign_are_logged(
    team: Team, self_approvals: Any
) -> None:
    team.seed.allow_self_approval(team.org)
    excluded = Json({"excluded_approver_ids": [str(team.users["controller"])]})
    rejected = _hitl(
        team, kind="export", data_space_id=None, assignee_capability="compliance", context=excluded
    )
    moved = _hitl(
        team, kind="export", data_space_id=None, assignee_capability="compliance", context=excluded
    )
    async with client(team) as api:
        r = await api.post(
            f"/v1/hitl-requests/{rejected}:resolve",
            headers=team.headers("controller"),
            json={"decision": "reject"},
        )
        assert r.status_code == 200, r.text
        assert self_approvals(team.org) == ["hitl.excluded_approver"]
        r = await api.post(
            f"/v1/hitl-requests/{moved}:resolve",
            headers=team.headers("controller"),
            json={"decision": "reassign", "reassign_to": str(team.users["reviewer"])},
        )
        assert r.status_code == 200, r.text
    assert self_approvals(team.org) == ["hitl.excluded_approver", "hitl.excluded_approver"]


async def test_proposal_review_self_approved_when_the_org_allows_it(
    team: Team, self_approvals: Any
) -> None:
    team.seed.allow_self_approval(team.org)
    proposal = _proposal(team)
    async with client(team) as api:
        r = await api.post(
            f"/v1/proposals/{proposal}:decide",
            headers=team.headers("proposer"),
            json={"decision": "accept"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "accepted"
    assert self_approvals(team.org) == ["proposal.review"]


async def test_proposal_accept_edit_self_approved_when_the_org_allows_it(
    team: Team, self_approvals: Any
) -> None:
    team.seed.allow_self_approval(team.org)
    proposal = _proposal(team)
    async with client(team) as api:
        edit = await api.post(
            f"/v1/proposals/{proposal}:decide",
            headers=team.headers("reviewer"),
            json={"decision": "accept_with_edit", "edited_diff": {"add": ["xăng"]}},
        )
        assert edit.status_code == 200, edit.text
        assert self_approvals(team.org) == []
        own_edit = await api.post(
            f"/v1/proposals/{proposal}:decide",
            headers=team.headers("reviewer"),
            json={"decision": "accept"},
        )
    assert own_edit.status_code == 200, own_edit.text
    assert own_edit.json()["status"] == "accepted"
    assert self_approvals(team.org) == ["proposal.accept_edit"]


async def test_proposal_second_step_self_approved_when_the_org_allows_it(
    team: Team, self_approvals: Any
) -> None:
    # One compliance holder proposed it and does both steps of BR-119: one row per step taken.
    team.seed.allow_self_approval(team.org)
    proposal = _proposal(
        team,
        kind="metric_change",
        requires_compliance=True,
        proposed_by=team.users["controller"],
    )
    async with client(team) as api:
        first = await api.post(
            f"/v1/proposals/{proposal}:decide",
            headers=team.headers("controller"),
            json={"decision": "accept"},
        )
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "pending_review"
        second = await api.post(
            f"/v1/proposals/{proposal}:decide",
            headers=team.headers("controller"),
            json={"decision": "accept"},
        )
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "accepted"
    assert self_approvals(team.org) == [
        "proposal.review",
        "proposal.review",
        "proposal.second_step",
    ]


# ---------------------------------------------------------------- the change in words (P07)
#
# GET /v1/proposals/{id} says what accepting does: a headline, the fields it changes (current and
# proposed), the evaluation and the preview in words, with every id the reviewer would have to
# look up resolved to a name under their own organisation.

MANY_TO_ONE = "nhiều–một"  # noqa: RUF001 - the screen's en dash, not a mistyped hyphen


def _dataset(team: Team, table: str, schema: str = "dbo", **values: Any) -> uuid.UUID:
    return team.seed.row(
        "catalog.dataset",
        org_id=team.org,
        data_space_id=team.space,
        # A derived table needs no source row behind it (dataset CHECK on origin).
        origin="derived",
        schema_name=schema,
        table_name=table,
        **values,
    )


def _dimension(team: Team, code: str, name: str) -> uuid.UUID:
    entity = team.seed.row(
        "context.semantic_entity",
        org_id=team.org,
        data_space_id=team.space,
        code=f"e_{code}",
        name=f"Thực thể {name}",
        primary_dataset_id=_dataset(team, f"dim_{code}"),
        key_columns=[f"{code}_key"],
    )
    return team.seed.row(
        "context.semantic_dimension",
        org_id=team.org,
        data_space_id=team.space,
        entity_id=entity,
        code=code,
        name=name,
        expression=code,
    )


def _metric(team: Team, code: str, name: str) -> tuple[uuid.UUID, uuid.UUID]:
    metric = team.seed.row(
        "context.metric",
        org_id=team.org,
        data_space_id=team.space,
        code=code,
        owner_id=team.users["steward"],
        current_version=1,
        status="active",
    )
    version = team.seed.row(
        "context.metric_version",
        org_id=team.org,
        metric_id=metric,
        version=1,
        display_name=name,
        expression="sum(amount)",
        period_semantics="sum_over_period",
    )
    return metric, version


def _skill_version(team: Team, skill: uuid.UUID) -> uuid.UUID:
    return team.seed.row(
        "skill.skill_version",
        org_id=team.org,
        skill_id=skill,
        version=1,
        params_schema=Json({}),
        steps=Json([]),
    )


async def _summary(team: Team, proposal: uuid.UUID) -> dict[str, Any]:
    async with client(team) as api:
        detail = await api.get(f"/v1/proposals/{proposal}", headers=team.headers("reviewer"))
    assert detail.status_code == 200, detail.text
    return detail.json()["summary"]


def _field(label: str, after: str | None, before: str | None = None) -> dict[str, Any]:
    return {"label": label, "before": before, "after": after}


async def test_summary_of_a_synonym_names_its_target_and_reads_the_evaluation(
    team: Team,
) -> None:
    occupation = _dimension(team, "occupation", "Nghề nghiệp")
    proposal = _proposal(
        team,
        kind="synonym",
        title="Từ đồng nghĩa “occupation”",
        diff=Json(
            {
                "phrase": "occupation",
                "target_kind": "dimension",
                "target_id": str(occupation),
                "target_value": None,
                "impact": {
                    "items": 60,
                    "passed": 10,
                    "pass_rate": "0.1667",
                    "regressions": 0,
                    "changed_items": [],
                },
            }
        ),
    )
    summary = await _summary(team, proposal)
    assert summary["headline"] == "Thêm từ đồng nghĩa “occupation” cho chiều Nghề nghiệp."
    assert summary["fields"] == [
        _field("Cụm từ", "occupation"),
        _field("Trỏ tới", "chiều Nghề nghiệp"),
    ]
    # A low pass rate without regressions is the set's baseline, not this change's doing.
    assert summary["checks_note"] == (
        "Bộ đánh giá 60 câu: 10 câu đạt trước và sau thay đổi, không câu nào tệ đi."
        " Các câu chưa đạt có từ trước, không do thay đổi này: bộ đánh giá hiện mới đạt 10/60."
    )
    assert summary["preview_note"] is None


async def test_summary_of_a_value_synonym_names_the_value_and_its_dimension(team: Team) -> None:
    city = _dimension(team, "city", "Thành phố")
    proposal = _proposal(
        team,
        kind="synonym",
        diff=Json(
            {
                "phrase": "hcm",
                "target_kind": "value",
                "target_id": str(city),
                "target_value": "Hồ Chí Minh",
                "impact": {"note": "Data Space chưa có bộ câu hỏi chuẩn; chưa đo được ảnh hưởng."},
            }
        ),
    )
    summary = await _summary(team, proposal)
    assert summary["headline"] == (
        "Thêm từ đồng nghĩa “hcm” cho giá trị “Hồ Chí Minh” của chiều Thành phố."
    )
    assert summary["fields"] == [
        _field("Cụm từ", "hcm"),
        _field("Trỏ tới", "chiều Thành phố"),
        _field("Giá trị", "Hồ Chí Minh"),
    ]
    assert summary["checks_note"] == (
        "Data Space chưa có bộ câu hỏi chuẩn; chưa đo được ảnh hưởng."
    )


async def test_summary_of_a_metric_change_reads_each_changed_field(team: Team) -> None:
    metric, _ = _metric(team, "TON_KHO", "Tồn kho")
    _dimension(team, "vung", "Vùng")
    _dimension(team, "san_pham", "Sản phẩm")
    proposal = _proposal(
        team,
        kind="metric_change",
        title="Duyệt định nghĩa TON_KHO phiên bản 2",
        diff=Json(
            {
                "action": "certify",
                "metric_id": str(metric),
                "metric_code": "TON_KHO",
                "version_id": str(uuid.uuid4()),
                "version": 2,
                "replaces_version": 1,
                "changes": {
                    "display_name": {"before": "Tồn kho", "after": "Tồn kho cuối kỳ"},
                    "decimals": {"before": 2, "after": 0},
                    "period_semantics": {"before": "sum_over_period", "after": "end_of_period"},
                    "time_grain": {"before": "day", "after": "month"},
                    "filters": {
                        "before": [],
                        "after": [{"dimension": "vung", "op": "=", "value": "Bắc"}],
                    },
                    "dimensions_added": ["san_pham"],
                    "dimensions_removed": ["vung"],
                },
                "preview": [
                    {"period": f"2026-0{m}", "new": None, "current": None, "filed": None}
                    for m in range(4, 10)
                ],
                "preview_withheld": None,
                "eval": {
                    "items": 60,
                    "passed": 12,
                    "pass_rate": "0.2",
                    "regressions": 0,
                    "changed_items": [str(uuid.uuid4()), str(uuid.uuid4())],
                },
            }
        ),
    )
    summary = await _summary(team, proposal)
    assert summary["headline"] == (
        "Chứng nhận chỉ số Tồn kho cuối kỳ (TON_KHO), phiên bản 2, thay phiên bản 1."
    )
    assert summary["fields"] == [
        _field("Tên hiển thị", "Tồn kho cuối kỳ", "Tồn kho"),
        _field("Bộ lọc", "Vùng = Bắc", "Không có"),
        _field("Số chữ số thập phân", "0", "2"),
        _field("Cách tính theo kỳ", "Giá trị cuối kỳ", "Cộng dồn trong kỳ"),
        _field("Độ hạt thời gian", "Tháng", "Ngày"),
        _field("Chiều được phép", "Thêm Sản phẩm · bỏ Vùng"),
    ]
    assert summary["checks_note"] == (
        "Bộ đánh giá 60 câu: 12 câu đạt với thay đổi này (2 câu tốt lên), không câu nào tệ đi."
        " Các câu chưa đạt có từ trước, không do thay đổi này: bộ đánh giá hiện mới đạt 12/60."
    )
    assert summary["preview_note"] == "Chưa có số liệu trong 6 kỳ gần nhất để xem trước."


async def test_summary_of_a_first_metric_version_names_the_metric(team: Team) -> None:
    metric, _ = _metric(team, "DOANH_THU", "Doanh thu")
    proposal = _proposal(
        team,
        kind="metric_change",
        diff=Json(
            {
                "action": "certify",
                "metric_id": str(metric),
                "metric_code": "DOANH_THU",
                "version": 1,
                "replaces_version": None,
                "changes": {"unit": {"before": None, "after": "VND"}},
                "preview": [],
                "preview_withheld": "Chưa bật bảng fact_sales cho Data Space.",
            }
        ),
    )
    summary = await _summary(team, proposal)
    assert summary["headline"] == "Chứng nhận chỉ số Doanh thu (DOANH_THU), phiên bản 1."
    assert summary["fields"] == [_field("Đơn vị", "VND")]
    assert summary["checks_note"] is None
    assert summary["preview_note"] == (
        "Chưa xem trước được: Chưa bật bảng fact_sales cho Data Space."
    )


async def test_summary_of_a_relationship_names_both_tables(team: Team) -> None:
    sales = _dataset(team, "fact_internet_sales")
    customers = _dataset(team, "dim_customer", display_name="Khách hàng")
    joined = {
        "from_dataset_id": str(sales),
        "from_columns": ["customer_key"],
        "to_dataset_id": str(customers),
        "to_columns": ["customer_key"],
        "cardinality": "many_to_one",
    }
    added = _proposal(
        team,
        kind="relationship",
        diff=Json(
            {
                **joined,
                "impact": {
                    "items": 60,
                    "passed": 50,
                    "pass_rate": "0.8333",
                    "regressions": 2,
                    "changed_items": [str(uuid.uuid4()), str(uuid.uuid4())],
                },
            }
        ),
    )
    retired = _proposal(
        team,
        kind="relationship",
        diff=Json({"action": "retire", "reason": "Khoá sai cột", **joined}),
    )
    summary = await _summary(team, added)
    assert summary["headline"] == (
        f"Nối bảng fact_internet_sales.customer_key → dim_customer.customer_key ({MANY_TO_ONE})."
    )
    assert summary["fields"] == [
        _field("Từ", "dbo.fact_internet_sales (customer_key)"),
        _field("Đến", "Khách hàng · dbo.dim_customer (customer_key)"),
        _field("Kiểu nối", MANY_TO_ONE),
    ]
    assert summary["checks_note"] == (
        "Bộ đánh giá 60 câu: 50 câu đạt với thay đổi này; 2 câu đang đạt sẽ không đạt nữa."
        " Xem từng câu trước khi chấp nhận."
    )

    summary = await _summary(team, retired)
    assert summary["headline"] == (
        "Gỡ quan hệ nối fact_internet_sales.customer_key → dim_customer.customer_key"
        f" ({MANY_TO_ONE})."
    )
    assert summary["fields"] == [
        _field("Từ", None, "dbo.fact_internet_sales (customer_key)"),
        _field("Đến", None, "Khách hàng · dbo.dim_customer (customer_key)"),
        _field("Kiểu nối", None, MANY_TO_ONE),
        _field("Lý do", "Khoá sai cột"),
    ]
    assert summary["checks_note"] is None


async def test_summary_of_an_example_and_a_glossary_term(team: Team) -> None:
    example = _proposal(
        team,
        kind="example",
        diff=Json(
            {
                "question": "Doanh thu năm 2013?",
                "sql": "SELECT sum(sales_amount) FROM dbo.fact_internet_sales",
                "rows": 1,
                "query_run_id": str(uuid.uuid4()),
            }
        ),
    )
    term = _proposal(
        team,
        kind="glossary",
        diff=Json({"term": "CASA", "definition": "Tiền gửi không kỳ hạn"}),
    )
    summary = await _summary(team, example)
    assert summary["headline"] == "Thêm câu hỏi mẫu “Doanh thu năm 2013?” và câu SQL trả lời nó."
    assert summary["fields"] == [
        _field("Câu hỏi", "Doanh thu năm 2013?"),
        _field("SQL", "SELECT sum(sales_amount) FROM dbo.fact_internet_sales"),
        _field("Chạy thử", "1 dòng"),
    ]
    summary = await _summary(team, term)
    assert summary["headline"] == "Thêm thuật ngữ “CASA” vào bảng thuật ngữ."
    assert summary["fields"] == [
        _field("Thuật ngữ", "CASA"),
        _field("Định nghĩa", "Tiền gửi không kỳ hạn"),
    ]


async def test_summary_of_a_data_product_names_it_and_its_reviewer(team: Team) -> None:
    product = team.seed.row(
        "context.data_product",
        org_id=team.org,
        data_space_id=team.space,
        code="ban_hang",
        name="Bán hàng trực tuyến",
        description="Doanh thu bán hàng",
        period_grain="month",
        period_from="2024-01-01",
        owner_id=team.users["steward"],
        steward_id=team.users["reviewer"],
        freshness_statement="Hằng ngày",
        scope_entities=Json([]),
    )
    proposal = _proposal(
        team,
        kind="data_product",
        diff=Json(
            {
                "data_product_id": str(product),
                "code": "ban_hang",
                "version": 1,
                "eval": {"items": 20, "passed": 20, "pass_rate": "1"},
                "assignee_id": str(team.users["steward"]),
            }
        ),
    )
    summary = await _summary(team, proposal)
    assert summary["headline"] == (
        "Kích hoạt data product Bán hàng trực tuyến (ban_hang), phiên bản 1."
    )
    assert summary["fields"] == [
        _field("Data product", "Bán hàng trực tuyến (ban_hang)"),
        _field("Phiên bản", "1"),
        _field("Người duyệt", "Owner"),
    ]
    assert summary["checks_note"] == "Bộ đánh giá 20 câu: 20 câu đạt."


async def test_summary_of_a_skill_publish_and_share(team: Team) -> None:
    skill = team.seed.row(
        "skill.skill",
        org_id=team.org,
        data_space_id=team.space,
        code="phan_ra",
        name="Phân rã biến động",
        category="decomposition",
        scope="personal",
        owner_id=team.users["proposer"],
    )
    # The proposal targets the version it publishes (worker_agent/proposals.py): its personal
    # skill is named for the reviewer because reviewing it is the request.
    version = _skill_version(team, skill)
    publish = _proposal(
        team,
        kind="skill_publish",
        target_service="skill",
        target_ref=version,
        diff=Json({"action": "publish", "skill_id": str(skill), "version": 3, "scope": "personal"}),
    )
    share = _proposal(
        team,
        kind="skill_publish",
        target_service="skill",
        target_ref=version,
        diff=Json(
            {
                "action": "share",
                "skill_id": str(skill),
                "previous_scope": "personal",
                "scope": "data_space",
            }
        ),
    )
    summary = await _summary(team, publish)
    assert summary["headline"] == "Phát hành kỹ năng Phân rã biến động, phiên bản 3."
    assert summary["fields"] == [
        _field("Kỹ năng", "Phân rã biến động"),
        _field("Phiên bản", "3"),
        _field("Phạm vi", "Cá nhân"),
    ]
    summary = await _summary(team, share)
    assert summary["headline"] == (
        "Mở rộng phạm vi kỹ năng Phân rã biến động từ Cá nhân ra Data Space."
    )
    assert summary["fields"] == [
        _field("Kỹ năng", "Phân rã biến động"),
        _field("Phạm vi", "Data Space", "Cá nhân"),
    ]


async def test_summary_of_a_writeback_counts_the_rows(team: Team) -> None:
    source = team.seed.row(
        "catalog.source",
        org_id=team.org,
        data_space_id=team.space,
        name="Core banking",
        kind="postgresql",
        secret_ref=f"secret/tenants/{team.org}/core",
        status="active",
    )
    loans = _dataset(team, "khoan_vay", schema="silver")
    proposal = _proposal(
        team,
        kind="writeback",
        target_service="pipeline",
        diff=Json(
            {
                "source_id": str(source),
                "target_table": "dbo.loan_overdue",
                "from_dataset_id": str(loans),
                "diff_summary": {"inserts": 2, "updates": 214, "deletes": 0, "sample": []},
            }
        ),
    )
    summary = await _summary(team, proposal)
    assert summary["headline"] == (
        "Ghi ngược vào bảng dbo.loan_overdue của Core banking: thêm 2 dòng, sửa 214 dòng."
    )
    assert summary["fields"] == [
        _field("Bảng đích", "dbo.loan_overdue"),
        _field("Nguồn", "Core banking"),
        _field("Lấy từ", "silver.khoan_vay"),
        _field("Dòng thêm", "2"),
        _field("Dòng sửa", "214"),
        _field("Dòng xoá", "0"),
    ]


async def test_summary_of_a_source_authority_ranks_the_sources(team: Team) -> None:
    core, dwh = (
        team.seed.row(
            "catalog.source",
            org_id=team.org,
            data_space_id=team.space,
            name=name,
            kind="postgresql",
            secret_ref=f"secret/tenants/{team.org}/{name}",
            status="active",
        )
        for name in ("Core banking", "DWH bán lẻ")
    )
    proposal = _proposal(
        team,
        kind="source_authority",
        target_service="conflict",
        diff=Json({"data_kind": "dư nợ", "ranking": [str(core), str(dwh)], "active_id": None}),
    )
    summary = await _summary(team, proposal)
    assert summary["headline"] == (
        "Đặt thứ tự thẩm quyền nguồn cho dư nợ: Core banking, rồi DWH bán lẻ."
    )
    assert summary["fields"] == [
        _field("Loại dữ liệu", "dư nợ"),
        _field("Thứ tự nguồn", "1. Core banking · 2. DWH bán lẻ"),
    ]


async def test_summary_of_a_document_validity_names_both_documents(team: Team) -> None:
    collection = uuid.uuid4()
    team.seed.row(
        "document.collection",
        id=collection,
        org_id=team.org,
        data_space_id=team.space,
        code="phap-quy",
        name="phap-quy",
        purpose="tra cứu văn bản pháp quy",
        qdrant_collection=f"doc_{collection.hex}",
        embed_model="bge-m3",
    )

    def document(title: str) -> uuid.UUID:
        sha = uuid.uuid4().hex * 2
        return team.seed.row(
            "document.document",
            org_id=team.org,
            collection_id=collection,
            status="ready",
            media="pdf",
            sha256=sha,
            file_name="qd.pdf",
            object_key=f"{team.org}/{sha}",
            size_bytes=1000,
            title=title,
        )

    new, old = document("QĐ 845/2026"), document("QĐ 120/2024")
    proposal = _proposal(
        team,
        kind="document_validity",
        target_service="document",
        target_ref=new,
        diff=Json(
            {
                "effective_from": "2026-09-01",
                "supersedes_id": str(old),
                "excerpt": "Quyết định này thay thế Quyết định 120/2024",
                "detected": True,
            }
        ),
    )
    summary = await _summary(team, proposal)
    assert summary["headline"] == "Ghi nhận văn bản QĐ 845/2026 thay thế QĐ 120/2024."
    assert summary["fields"] == [
        _field("Văn bản", "QĐ 845/2026"),
        _field("Hiệu lực từ", "01/09/2026"),
        _field("Thay thế văn bản", "QĐ 120/2024"),
        _field("Trích đoạn", "Quyết định này thay thế Quyết định 120/2024"),
    ]


async def test_summary_of_a_knowledge_page_names_the_page(team: Team) -> None:
    page = team.seed.row(
        "context.knowledge_page",
        org_id=team.org,
        data_space_id=team.space,
        kind="topic",
        subject_kind="topic",
        slug=f"trang-{uuid.uuid4().hex[:8]}",
        title="Doanh thu theo kênh",
        owner_id=team.users["steward"],
    )
    proposal = _proposal(
        team,
        kind="knowledge_page",
        diff=Json(
            {
                "page_id": str(page),
                "version_id": str(uuid.uuid4()),
                "version": 2,
                "origin": "agent",
                "change_size": "minor",
            }
        ),
    )
    summary = await _summary(team, proposal)
    assert summary["headline"] == "Phục vụ trang tri thức Doanh thu theo kênh, phiên bản 2."
    assert summary["fields"] == [
        _field("Trang", "Doanh thu theo kênh"),
        _field("Phiên bản", "2"),
        _field("Nguồn gốc", "agent"),
        _field("Mức thay đổi", "minor"),
    ]


async def test_summary_never_shows_an_id_the_caller_cannot_read(team: Team, seed: Seed) -> None:
    # A metric of another organisation: RLS hides it, and its id is not shown in its place.
    elsewhere = seed.org()
    foreign = seed.row(
        "org.app_user",
        org_id=elsewhere,
        external_id=f"kc-x-{elsewhere.hex[:6]}",
        username="x",
        display_name="X",
    )
    hidden = seed.row(
        "context.metric",
        org_id=elsewhere,
        data_space_id=seed.row(
            "org.data_space", org_id=elsewhere, code="DS-X", name="X", steward_id=foreign
        ),
        code="BI_MAT",
        owner_id=foreign,
    )
    proposal = _proposal(
        team,
        kind="synonym",
        diff=Json(
            {
                "phrase": "doanh thu",
                "target_kind": "metric",
                "target_id": str(hidden),
                "target_value": None,
                "impact": {},
            }
        ),
    )
    summary = await _summary(team, proposal)
    assert summary["headline"] == "Thêm từ đồng nghĩa “doanh thu” cho chỉ số (không xem được)."
    assert summary["fields"][1] == _field("Trỏ tới", "chỉ số (không xem được)")
    assert str(hidden) not in str(summary)
    assert "BI_MAT" not in str(summary)


async def test_summary_of_an_unknown_kind_falls_back_to_the_title(team: Team) -> None:
    proposal = _proposal(
        team,
        # A kind no summary is written for (yet): the CHECK on kind admits it, describe does not.
        kind="feedback_fix",
        title="Một thay đổi chưa biết",
        diff=Json({"rate": 0.5, "label": "mới", "enabled": True, "nested": {"a": 1}, "none": None}),
    )
    summary = await _summary(team, proposal)
    assert summary == {
        "headline": "Một thay đổi chưa biết",
        "fields": [
            _field("rate", "0.5"),
            _field("label", "mới"),
            _field("enabled", "có"),
        ],
        "checks_note": None,
        "preview_note": None,
    }
    # A known kind whose diff has not its shape is read the same way, and never fails the page.
    broken = _proposal(team, kind="synonym", title="Từ đồng nghĩa lạ", diff=Json(["x"]))
    summary = await _summary(team, broken)
    assert summary["headline"] == "Từ đồng nghĩa lạ"
    assert summary["fields"] == []


async def test_summary_hides_a_table_personal_to_someone_else_or_deleted(team: Team) -> None:
    # The catalog's own rule (BR-111): a personal table is its owner's only.
    mine = _dataset(team, "fact_sales")
    personal = _dataset(team, "nhap_cua_toi", scope="personal", created_by=team.users["proposer"])
    gone = _dataset(team, "da_xoa", deleted_at="2026-10-01")
    proposals = [
        _proposal(
            team,
            kind="relationship",
            diff=Json(
                {
                    "from_dataset_id": str(mine),
                    "from_columns": ["k"],
                    "to_dataset_id": str(hidden),
                    "to_columns": ["k"],
                    "cardinality": "many_to_one",
                }
            ),
        )
        for hidden in (personal, gone)
    ]
    for proposal, name in zip(proposals, ("nhap_cua_toi", "da_xoa"), strict=True):
        summary = await _summary(team, proposal)
        assert summary["fields"][1] == _field("Đến", "(không xem được) (k)")
        assert name not in str(summary)


async def test_summary_hides_a_knowledge_page_above_the_readers_clearance(team: Team) -> None:
    # The reader policy (BR-163): a confidential page is hidden from an `internal` reader.
    page = team.seed.row(
        "context.knowledge_page",
        org_id=team.org,
        data_space_id=team.space,
        kind="topic",
        subject_kind="topic",
        slug=f"trang-{uuid.uuid4().hex[:8]}",
        title="Lương ban điều hành",
        owner_id=team.users["steward"],
        classification="confidential",
    )
    proposal = _proposal(
        team, kind="knowledge_page", diff=Json({"page_id": str(page), "version": 1})
    )
    summary = await _summary(team, proposal)
    assert summary["headline"] == "Phục vụ trang tri thức (không xem được), phiên bản 1."
    assert "Lương ban điều hành" not in str(summary)


async def test_summary_hides_a_personal_skill_the_proposal_does_not_review(team: Team) -> None:
    personal = team.seed.row(
        "skill.skill",
        org_id=team.org,
        data_space_id=team.space,
        code="rieng",
        name="Kỹ năng riêng",
        category="custom",
        scope="personal",
        owner_id=team.users["proposer"],
    )
    reviewed = team.seed.row(
        "skill.skill",
        org_id=team.org,
        data_space_id=team.space,
        code="dang_duyet",
        name="Kỹ năng đang duyệt",
        category="custom",
        scope="personal",
        owner_id=team.users["proposer"],
    )
    # Its target is another skill's version: naming that one does not name this one.
    proposal = _proposal(
        team,
        kind="skill_publish",
        target_service="skill",
        target_ref=_skill_version(team, reviewed),
        diff=Json({"action": "publish", "skill_id": str(personal), "version": 1}),
    )
    summary = await _summary(team, proposal)
    assert summary["headline"] == "Phát hành kỹ năng (không xem được), phiên bản 1."
    assert "Kỹ năng riêng" not in str(summary)
