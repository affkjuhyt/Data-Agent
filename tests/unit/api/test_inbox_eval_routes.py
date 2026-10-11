"""API-04 request validation and error mapping, without a database.

The fake identities give one user every capability of the group (ask, approve, steward)
inside Data Space DS-OIL, so a request reaches body validation; anything that would need
Postgres is in tests/integration/api/test_inbox_eval.py. Acceptance criteria are named in comments
(tests/design_checklist.py reads them there): the req marker of tests/conftest.py does not take
AC-xx-yy-zz ids yet.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.config import Settings

from .conftest import ORG, FakeIdentities, FakeVerifier

pytestmark = [pytest.mark.unit, pytest.mark.contract]

REVIEWER = uuid.UUID("01920000-0000-7000-8000-0000000000a1")
TOKENS = {
    "reviewer": {"sub": "kc-reviewer", "org_id": str(ORG)},
    "asker": {"sub": "kc-asker", "org_id": str(ORG)},
}
USERS = {
    "kc-reviewer": (REVIEWER, frozenset(), frozenset({"ask", "approve", "steward"})),
    "kc-asker": (uuid.UUID(int=7), frozenset({"ask"}), frozenset()),
}
SPACE_HEADERS = {"Authorization": "Bearer reviewer", "X-Data-Space": "DS-OIL"}
ID = "01920000-0000-7000-8000-00000000beef"


@pytest.fixture
def api() -> TestClient:
    app = create_app(
        Settings(), token_verifier=FakeVerifier(TOKENS), identity_resolver=FakeIdentities(USERS)
    )
    return TestClient(app, raise_server_exceptions=False)


def _problem(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == code
    return body


def test_inbox_needs_a_data_space(api: TestClient) -> None:
    response = api.get("/v1/inbox", headers={"Authorization": "Bearer asker"})
    _problem(response, 400, "data_space_required")


def test_error_book_needs_steward(api: TestClient) -> None:
    response = api.get("/v1/error-book-entries", headers={"Authorization": "Bearer asker"})
    _problem(response, 403, "forbidden")


@pytest.mark.parametrize(
    "body",
    [
        {},  # decision is required
        {"status": "approved"},  # the pre-A3 shape
        {"decision": "approved"},
        {"decision": "reassign"},  # reassign_to missing
        {"decision": "approve", "reassign_to": ID},  # reassign_to only with reassign
        {"decision": "request_changes"},  # UC-07-07 6a: the agent revises from the note
    ],
)
def test_resolve_validates_the_a3_body(api: TestClient, body: dict) -> None:
    response = api.post(f"/v1/hitl-requests/{ID}:resolve", headers=SPACE_HEADERS, json=body)
    _problem(response, 422, "validation_failed")


@pytest.mark.parametrize(
    "body",
    [
        {"note": "ok"},  # decision is required
        {"decision": "approve"},
        {"decision": "accept_with_edit"},  # edited_diff missing
        {"decision": "accept", "edited_diff": {"a": 1}},  # edited_diff only with accept_with_edit
    ],
)
def test_decide_validates_the_a3_body(api: TestClient, body: dict) -> None:
    response = api.post(f"/v1/proposals/{ID}:decide", headers=SPACE_HEADERS, json=body)
    _problem(response, 422, "validation_failed")


def test_eval_item_needs_input_and_expected(api: TestClient) -> None:
    response = api.post(
        f"/v1/eval-sets/{ID}/items", headers=SPACE_HEADERS, json={"input": {"q": "x"}}
    )
    body = _problem(response, 422, "validation_failed")
    assert [e["field"] for e in body["errors"]] == ["expected"]


@pytest.mark.parametrize(
    ("source_kind", "refs"),
    [
        ("user_report", {"answer_id": ID}),  # feedback_id missing
        ("reviewer_reject", {"answer_id": ID}),  # hitl_request_id missing
        ("replay_mismatch", {}),
        ("forecast_miss", {"accuracy_log_id": ID}),  # forecast_detail missing
        ("knowledge_lint", {}),
    ],
)
def test_error_book_entry_needs_the_reference_of_its_source(
    api: TestClient, source_kind: str, refs: dict
) -> None:
    body = {"source_kind": source_kind, "data_space_id": ID, "title": "Số sai", **refs}
    response = api.post("/v1/error-book-entries", headers=SPACE_HEADERS, json=body)
    problem = _problem(response, 422, "validation_failed")
    assert "BR-179" in problem["errors"][0]["message"]


def test_dismiss_needs_a_reason_or_a_duplicate(api: TestClient) -> None:
    # AC-07-14-04: an entry dismissed without a reason is refused.
    response = api.post(
        f"/v1/error-book-entries/{ID}:dismiss", headers=SPACE_HEADERS, json={"reason": "  "}
    )
    _problem(response, 422, "validation_failed")


def test_mark_fixed_needs_fix_kind(api: TestClient) -> None:
    response = api.post(
        f"/v1/error-book-entries/{ID}:mark-fixed", headers=SPACE_HEADERS, json={"fix_ref": "abc"}
    )
    _problem(response, 422, "validation_failed")


def test_propose_to_pack_needs_pack_and_summary(api: TestClient) -> None:
    response = api.post(
        f"/v1/error-book-entries/{ID}:propose-to-pack", headers=SPACE_HEADERS, json={"pack_id": ID}
    )
    _problem(response, 422, "validation_failed")


def test_patch_rejects_an_unknown_error_kind(api: TestClient) -> None:
    response = api.patch(
        f"/v1/error-book-entries/{ID}", headers=SPACE_HEADERS, json={"error_kind": "typo"}
    )
    _problem(response, 422, "validation_failed")


def test_list_error_book_takes_the_a3_filters(api: TestClient) -> None:
    spec = api.app.openapi()  # type: ignore[attr-defined]
    params = {p["name"] for p in spec["paths"]["/v1/error-book-entries"]["get"]["parameters"]}
    assert {
        "status",
        "error_kind",
        "source_kind",
        "data_product_id",
        "metric_code",
        "assignee_id",
        "cursor",
    } <= params
