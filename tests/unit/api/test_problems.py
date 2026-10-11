"""Every error is application/problem+json with a stable code and a trace id (A1.7)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.api.kernel.tracing import trace_id_from

from .conftest import bearer

pytestmark = [pytest.mark.unit, pytest.mark.contract]

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
TRACEPARENT = f"00-{TRACE}-00f067aa0ba902b7-01"


def test_unknown_path_is_a_problem(client: TestClient) -> None:
    r = client.get("/v1/nowhere", headers={"traceparent": TRACEPARENT})
    assert r.status_code == 404
    assert r.headers["content-type"] == "application/problem+json"
    body = r.json()
    assert body["type"] == "https://docs.dataagent/errors/not_found"
    assert body["code"] == "not_found"
    assert body["status"] == 404
    assert body["trace_id"] == TRACE


def test_unexpected_error_hides_internals_and_keeps_trace(client: TestClient) -> None:
    r = client.get(
        "/v1/receipt-replays/01920000-0000-7000-8000-000000000001",
        headers={**bearer("asker"), "traceparent": TRACEPARENT},
    )
    assert r.status_code == 500
    body = r.json()
    assert body["code"] == "internal_error"
    assert "boom" not in r.text
    assert body["trace_id"] == TRACE


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (TRACEPARENT, TRACE),
        ("00-" + "0" * 32 + "-00f067aa0ba902b7-01", None),
        ("garbage", None),
        (None, None),
    ],
)
def test_trace_id_from_traceparent(header: str | None, expected: str | None) -> None:
    got = trace_id_from(header)
    assert len(got) == 32
    if expected:
        assert got == expected
