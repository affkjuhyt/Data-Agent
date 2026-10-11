"""API-04 HITL, evaluation and Error Book flows against PostgreSQL."""

from __future__ import annotations

import uuid

import pytest

from .test_qa_query import World, build, client

pytestmark = [pytest.mark.integration, pytest.mark.contract]


@pytest.fixture
def api04_world(seed) -> World:
    return build(seed, "-api04")


async def test_hitl_and_proposal_decisions(api04_world: World, seed) -> None:
    hitl_id = seed.row(
        "inbox.hitl_request",
        org_id=api04_world.org,
        data_space_id=api04_world.space,
        kind="clarification",
        title="Cần làm rõ phạm vi",
        source_service="test",
        assignee_group="askers",
    )
    proposal_id = seed.row(
        "inbox.proposal",
        org_id=api04_world.org,
        data_space_id=api04_world.space,
        kind="synonym",
        target_service="context",
        title="Thêm từ đồng nghĩa",
        proposed_by=uuid.uuid4(),
    )
    headers = {"Authorization": "Bearer kc-api-api04", "X-Data-Space": str(api04_world.space)}
    async with client(api04_world) as api:
        inbox = await api.get("/v1/inbox", headers=headers)
        assert inbox.status_code == 200, inbox.text
        assert any(item["id"] == str(hitl_id) for item in inbox.json()["items"])

        resolved = await api.post(
            f"/v1/hitl-requests/{hitl_id}:resolve",
            headers=headers,
            json={"status": "approved", "response": {"choice": "yes"}},
        )
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["status"] == "approved"

        decided = await api.post(
            f"/v1/proposals/{proposal_id}:decide",
            headers=headers,
            json={"decision": "accept", "reviewed_regressions": True},
        )
        assert decided.status_code == 200, decided.text
        assert decided.json()["status"] == "accepted"


async def test_eval_and_error_book(api04_world: World, seed) -> None:
    eval_set_id = seed.row(
        "eval.eval_set",
        org_id=api04_world.org,
        data_space_id=api04_world.space,
        subject_type="context",
        name="API-04 smoke",
    )
    headers = {"Authorization": "Bearer kc-api-api04", "X-Data-Space": str(api04_world.space)}
    async with client(api04_world) as api:
        item = await api.post(
            f"/v1/eval-sets/{eval_set_id}/items",
            headers=headers,
            json={"input": {"q": "1+1"}, "expected": {"answer": 2}},
        )
        assert item.status_code == 201, item.text
        run = await api.post(f"/v1/eval-sets/{eval_set_id}/runs", headers=headers)
        assert run.status_code == 202, run.text
        assert run.json()["item_count"] == 1

        entry = await api.post(
            "/v1/error-book-entries",
            headers=headers,
            json={
                "entry_no": "EB-API04-1",
                "data_space_id": str(api04_world.space),
                "source_kind": "user_report",
                "title": "Số liệu không khớp",
                "feedback_id": str(uuid.uuid4()),
                "answer_id": str(uuid.uuid4()),
            },
        )
        assert entry.status_code == 201, entry.text
        entry_id = entry.json()["id"]
        patched = await api.patch(
            f"/v1/error-book-entries/{entry_id}",
            headers=headers,
            json={"error_kind": "data", "root_cause": "Nguồn chậm cập nhật"},
        )
        assert patched.status_code == 200, patched.text
        fixed = await api.post(f"/v1/error-book-entries/{entry_id}:mark-fixed", headers=headers)
        assert fixed.status_code == 200, fixed.text
        regression = await api.post(f"/v1/error-book-entries/{entry_id}:add-regression", headers=headers)
        assert regression.status_code == 200, regression.text
        assert regression.json()["status"] == "regression_added"
