"""V3 live smoke: Kong → api ``/v1/questions`` → worker-agent → SSE."""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx
import pytest

from tests.conftest import live_token

pytestmark = pytest.mark.e2e

DATA_SPACE = os.environ.get("E2E_DATA_SPACE", "a0000000-0000-0000-0000-000000000001")
QUESTION_TIMEOUT = float(os.environ.get("E2E_TURN_TIMEOUT", "240"))


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Data-Space": DATA_SPACE}


def _events(response: httpx.Response) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    event_name = "message"
    for line in response.iter_lines():
        if line.startswith("event: "):
            event_name = line[7:]
        elif line.startswith("data: "):
            data = json.loads(line[6:])
            events.append({"event": event_name, "data": data})
    return events


@pytest.fixture(scope="session")
def token(live_stack: dict[str, str]) -> str:
    return live_token(live_stack["keycloak"], "engineer")


@pytest.fixture(scope="session")
def kong(live_stack: dict[str, str]) -> str:
    return live_stack["kong"]


def _wait_for_terminal(kong: str, token: str, question_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + QUESTION_TIMEOUT
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = httpx.get(
            f"{kong}/v1/questions/{question_id}", headers=_headers(token), timeout=15
        )
        assert response.status_code == 200, response.text[:300]
        last = response.json()
        if last.get("status") in {"answered", "clarifying", "refused", "failed"}:
            return last
        time.sleep(0.5)
    pytest.fail(f"question did not reach a terminal state: {last}")


def _ask(kong: str, token: str, text: str) -> tuple[dict[str, Any], dict[str, Any]]:
    response = httpx.post(
        f"{kong}/v1/questions",
        json={"text": text, "stream": True},
        headers=_headers(token),
        timeout=15,
    )
    assert response.status_code == 202, response.text[:300]
    accepted = response.json()
    assert accepted["question_id"] == accepted["id"]
    view = _wait_for_terminal(kong, token, accepted["question_id"])
    return accepted, view


def test_v3_questions_reject_anonymous_requests(kong: str) -> None:
    response = httpx.post(
        f"{kong}/v1/questions",
        json={"text": "Doanh thu thuần tháng này", "stream": True},
        headers={"X-Data-Space": DATA_SPACE},
        timeout=15,
    )
    assert response.status_code in (401, 403), response.text[:300]


def test_v3_question_reaches_worker_and_streams_done(kong: str, token: str) -> None:
    accepted, view = _ask(kong, token, "Doanh thu thuần tháng này theo vùng")
    assert accepted["stream_url"] == f"/v1/questions/{accepted['question_id']}/stream"
    assert view["status"] in {"answered", "clarifying", "refused", "failed"}

    response = httpx.get(f"{kong}{accepted['stream_url']}", headers=_headers(token), timeout=15)
    assert response.status_code == 200, response.text[:300]
    events = _events(response)
    assert any(item["event"] == "done" for item in events), events

    if view["status"] == "answered":
        answer_id = view["answer_id"]
        answer = httpx.get(f"{kong}/v1/answers/{answer_id}", headers=_headers(token), timeout=15)
        assert answer.status_code == 200, answer.text[:300]
        body = answer.json()
        assert body["question_id"] == accepted["question_id"]
        assert body["text"].strip()
