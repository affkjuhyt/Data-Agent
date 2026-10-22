"""Hỏi đáp and Truy vấn: the A3 request shapes, validation and error mapping that need no
database, and the pure rules the routes apply (stream ids, chip origin, replay label).

The fake identities below hold the capabilities of both groups inside Data Space DS-OIL, so a
request reaches body validation; everything that needs Postgres is in
tests/integration/api/test_qa_*.py.
"""

from __future__ import annotations

import datetime
import decimal
import uuid

import pytest
from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.config import Settings
from services.api.modules.qa import routes
from services.api.modules.qa.schemas import (
    REPLAY_TEXT_LABEL,
    PlanView,
    QuestionView,
    ReceiptFigureView,
    ReplayView,
)
from tests.unit.api.test_a3_contract import contract_gaps

from .conftest import ORG, FakeIdentities, FakeVerifier, bearer

pytestmark = pytest.mark.unit

ANALYST = uuid.UUID("01920000-0000-7000-8000-0000000a7a1e")
REVIEWER = uuid.UUID("01920000-0000-7000-8000-0000000be71e")
TOKENS = {
    "analyst": {"sub": "kc-analyst", "org_id": str(ORG)},
    "reviewer": {"sub": "kc-reviewer", "org_id": str(ORG)},
}
USERS = {
    "kc-analyst": (ANALYST, frozenset({"ask"}), frozenset()),
    "kc-reviewer": (REVIEWER, frozenset({"ask", "approve", "view_detail"}), frozenset()),
}
SOME_ID = "01920000-0000-7000-8000-0000000000aa"
NOW = datetime.datetime(2026, 10, 11, 9, 0, tzinfo=datetime.UTC)


@pytest.fixture(scope="module")
def api() -> TestClient:
    app = create_app(
        Settings(), token_verifier=FakeVerifier(TOKENS), identity_resolver=FakeIdentities(USERS)
    )
    return TestClient(app, raise_server_exceptions=False)


def headers(token: str = "analyst", **extra: str) -> dict[str, str]:
    return {**bearer(token), "X-Data-Space": "DS-OIL", **extra}


def fields(response) -> set[str]:
    return {e["field"] for e in response.json().get("errors", [])}


# --- the A3 gate --------------------------------------------------------------------------------


def test_both_groups_take_the_request_shapes_of_a3() -> None:
    spec = create_app(Settings()).openapi()
    assert contract_gaps(spec, frozenset({"Hỏi đáp", "Truy vấn"})) == []


# --- request validation -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/v1/questions"),
        ("GET", f"/v1/questions/{SOME_ID}"),
        ("GET", "/v1/conversations"),
        ("GET", f"/v1/conversations/{SOME_ID}"),
        ("GET", f"/v1/answers/{SOME_ID}"),
        ("GET", "/v1/receipts/BN-20260702-000184"),
        ("GET", f"/v1/query-runs/{SOME_ID}"),
    ],
)
def test_qa_resources_need_a_data_space(api: TestClient, method: str, path: str) -> None:
    response = api.request(method, path, headers=bearer("analyst"), json={"text": "Doanh thu?"})
    assert response.status_code == 400
    assert response.json()["code"] == "data_space_required"


def test_question_requires_text_and_refuses_fields_a3_does_not_name(api: TestClient) -> None:
    missing = api.post("/v1/questions", headers=headers(), json={})
    assert missing.status_code == 422
    assert missing.json()["code"] == "validation_failed"
    assert "text" in fields(missing)

    unknown = api.post("/v1/questions", headers=headers(), json={"text": "x", "org_id": SOME_ID})
    assert unknown.status_code == 422
    assert "org_id" in fields(unknown)

    mode = api.post("/v1/questions", headers=headers(), json={"text": "x", "knowledge_mode": "now"})
    assert mode.status_code == 422
    assert "knowledge_mode" in fields(mode)


@pytest.mark.req("AC-01-05-01")
def test_wrong_rating_needs_an_error_kind(api: TestClient) -> None:
    response = api.post(
        f"/v1/answers/{SOME_ID}/feedback", headers=headers(), json={"rating": "wrong"}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_failed"


def test_reviewing_feedback_needs_approve(api: TestClient) -> None:
    response = api.patch(f"/v1/feedback/{SOME_ID}", headers=headers(), json={"status": "confirmed"})
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


@pytest.mark.req("AC-01-05-04")
def test_a_rejection_carries_its_reason(api: TestClient) -> None:
    response = api.patch(
        f"/v1/feedback/{SOME_ID}", headers=headers("reviewer"), json={"status": "rejected"}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_failed"


def test_to_eval_requires_an_eval_set(api: TestClient) -> None:
    response = api.post(f"/v1/feedback/{SOME_ID}:to-eval", headers=headers("reviewer"), json={})
    assert response.status_code == 422
    assert "eval_set_id" in fields(response)


def test_plan_edit_requires_chips(api: TestClient) -> None:
    response = api.patch(f"/v1/questions/{SOME_ID}/plan", headers=headers(), json={"chips": []})
    assert response.status_code == 422
    assert "chips" in fields(response)


def test_export_format_is_one_of_d4(api: TestClient) -> None:
    response = api.post(
        f"/v1/answers/{SOME_ID}/exports", headers=headers(), json={"format": "docx"}
    )
    assert response.status_code == 422
    assert "format" in fields(response)


def test_replay_mode_is_figures_or_figures_and_text(api: TestClient) -> None:
    response = api.post(
        "/v1/receipts/BN-20260702-000184:replay", headers=headers(), json={"mode": "text"}
    )
    assert response.status_code == 422
    assert "mode" in fields(response)


def test_explore_requires_metric_ids(api: TestClient) -> None:
    response = api.post("/v1/explore/queries", headers=headers(), json={"text": "select 1"})
    assert response.status_code == 422
    assert {"metric_ids", "text"} <= fields(response)


@pytest.mark.req("AC-01-02-04")
def test_sql_needs_view_detail(api: TestClient) -> None:
    response = api.get(f"/v1/query-runs/{SOME_ID}/sql", headers=headers())
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


@pytest.mark.req("AC-01-02-07")
def test_rows_need_view_detail(api: TestClient) -> None:
    response = api.get(f"/v1/query-runs/{SOME_ID}/rows", headers=headers())
    assert response.status_code == 403


def test_rows_refuse_a_cursor_the_api_did_not_issue(api: TestClient) -> None:
    response = api.get(
        f"/v1/query-runs/{SOME_ID}/rows", headers=headers("reviewer"), params={"cursor": "%%%"}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"


def test_stream_refuses_a_foreign_last_event_id(api: TestClient) -> None:
    response = api.get(
        f"/v1/questions/{SOME_ID}/stream", headers=headers(**{"Last-Event-ID": "abc"})
    )
    assert response.status_code == 400


# --- pure rules ---------------------------------------------------------------------------------


def _question(status: str, plan_version: int | None, answer: bool = False) -> QuestionView:
    plan = (
        PlanView(
            id=uuid.uuid4(),
            question_id=uuid.uuid4(),
            version=plan_version,
            chips=[],
            steps=[],
            confidence=None,
            edited_by_user=False,
            edit_note=None,
            created_at=NOW,
        )
        if plan_version
        else None
    )
    return QuestionView(
        id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        seq=1,
        text="Doanh thu?",
        parent_question_id=None,
        as_of=None,
        knowledge_mode="latest",
        knowledge_at=None,
        purpose=None,
        status=status,
        received_at=NOW,
        answered_at=None,
        plan=plan,
        answer_id=uuid.uuid4() if answer else None,
    )


def test_stream_uses_the_a16_event_names_with_growing_ids() -> None:
    received = routes._stream_events(_question("received", None), None)
    assert [e.event for e in received] == ["progress"]

    answered = routes._stream_events(_question("answered", 1, answer=True), "data")
    assert [e.event for e in answered] == ["understood", "answer", "done"]
    ids = [int(e.id) for e in answered]
    assert ids == sorted(ids) and ids[0] > int(received[0].id)

    # A plan edit starts a new decade, so a client that saw the old answer still gets the new one.
    replanned = routes._stream_events(_question("answered", 2, answer=True), "data")
    assert min(int(e.id) for e in replanned) > max(ids)

    failed = routes._stream_events(_question("failed", 1), None)
    assert [e.event for e in failed] == ["understood", "error", "done"]

    clarifying = routes._stream_events(_question("clarifying", 1), None)
    assert [e.event for e in clarifying] == ["understood", "clarification"]


@pytest.mark.req("AC-01-03-01")
def test_changed_chips_record_the_user_as_their_origin() -> None:
    previous = [{"kind": "metric", "value": "NPL_RATIO", "origin": "default"}]
    edited = routes._stamp_chips(
        [previous[0], {"kind": "dimension", "value": "region", "origin": "default"}],
        previous,
        ANALYST,
    )
    assert edited[0] == previous[0]
    assert edited[1]["origin"] == "user"
    assert edited[1]["edited_by"] == str(ANALYST)


@pytest.mark.req("AC-01-07-08")
def test_regenerated_replay_text_is_always_labelled() -> None:
    base = {
        "id": uuid.uuid4(),
        "receipt_id": uuid.uuid4(),
        "requested_by": ANALYST,
        "reason": None,
        "mode": "figures_and_text",
        "compiler_version_used": "semcomp 1.8.2",
        "status": "completed",
        "outcome": "identical",
        "figure_results": [],
        "differs_count": 0,
        "cause_summary": None,
        "retro_change_ids": [],
        "replay_model": "qwen",
        "query_run_ids": [],
        "error_book_entry_id": None,
        "started_at": NOW,
        "finished_at": NOW,
        "created_at": NOW,
    }
    assert ReplayView(**base, replay_text="Giá trần ...").replay_text_label == REPLAY_TEXT_LABEL
    assert ReplayView(**base, replay_text=None).replay_text_label is None


def test_receipt_figures_travel_as_decimal_strings() -> None:
    figure = ReceiptFigureView(
        id=uuid.uuid4(),
        figure_key="F1",
        label="Giá trần E5RON92",
        value=decimal.Decimal("19730.000000000001"),
        unit="đ/lít",
        display_state="shown",
        stale_note=None,
        requested_period="2026-07-02",
        metric_id=None,
        metric_version_id=None,
        metric_version_no=None,
        metric_certified_by=None,
        metric_certified_at=None,
        source_kind="query_run",
        query_run_id=uuid.uuid4(),
        compute_run_id=None,
        published_result_id=None,
        compiled_sql_sha256="0" * 64,
        expr_sha256=None,
        result_sha256=None,
        dataset_versions=[],
        as_of=None,
        freshness_state="fresh",
        due_at=None,
        created_at=NOW,
    )
    assert figure.model_dump(mode="json")["value"] == "19730.000000000001"
