"""Every error is application/problem+json with a stable code and a trace id (A1.7)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError

from services.api.kernel.problems import install_problem_handlers
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


class _PgError(Exception):
    """Stands in for asyncpg's error under SQLAlchemy's adapter."""

    def __init__(self, sqlstate: str, message: str, constraint_name: str | None = None) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate
        self.message = message
        self.constraint_name = constraint_name
        self.table_name = "app_user" if constraint_name else None


def _db_error(sqlstate: str, message: str, constraint_name: str | None = None) -> DBAPIError:
    adapted = Exception("adapted")
    adapted.__cause__ = _PgError(sqlstate, message, constraint_name)
    return DBAPIError("INSERT …", {}, adapted)


@pytest.mark.parametrize(
    ("error", "status", "code", "detail"),
    [
        (
            _db_error("23514", "Kho thiếu khoá bọc (BR-189)"),
            422,
            "business_rule_violated",
            "Kho thiếu khoá bọc (BR-189)",
        ),
        (
            _db_error(
                "23514",
                'new row violates check constraint "app_user_kind_check"',
                "app_user_kind_check",
            ),
            422,
            "business_rule_violated",
            None,
        ),
        (_db_error("23505", "duplicate key", "role_org_id_code_key"), 409, "conflict", None),
        (_db_error("42501", "permission denied for table x"), 403, "forbidden", None),
        (
            _db_error("42501", "Workflow chỉ ghi dòng actor_kind = workflow (BR-192)"),
            403,
            "forbidden",
            "Workflow chỉ ghi dòng actor_kind = workflow (BR-192)",
        ),
    ],
)
def test_database_refusals_become_a17_problems(error, status, code, detail) -> None:
    app = FastAPI()
    install_problem_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise error

    response = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert response.status_code == status
    body = response.json()
    assert body["code"] == code
    # A constraint's message names a table (A1.7 keeps it private); a trigger's message is meant to be read.
    assert body.get("detail") == detail
