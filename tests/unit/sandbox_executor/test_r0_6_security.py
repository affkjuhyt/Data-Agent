from __future__ import annotations

import pytest
from api.schemas import ExecuteSQLRequest
from auth import require_service_assertion
from fastapi import HTTPException

pytestmark = pytest.mark.unit


def test_sandbox_rejects_missing_service_assertion(monkeypatch):
    monkeypatch.setenv("SERVICE_AUTH_SECRET", "test-secret")

    with pytest.raises(HTTPException) as exc:
        require_service_assertion("")

    assert exc.value.status_code == 401


def test_sql_request_accepts_connection_ref_without_password():
    request = ExecuteSQLRequest(
        sql="SELECT 1",
        connection={
            "type": "postgres",
            "host": "warehouse",
            "port": 5432,
            "user": "reader",
            "dbname": "analytics",
            "connection_ref": "warehouse-ref-1",
        },
    )

    assert request.connection["connection_ref"] == "warehouse-ref-1"
    assert "password" not in request.connection


def test_sql_request_rejects_plaintext_password():
    with pytest.raises(ValueError, match="connection_ref"):
        ExecuteSQLRequest(
            sql="SELECT 1",
            connection={
                "type": "postgres",
                "host": "warehouse",
                "user": "reader",
                "dbname": "analytics",
                "password": "must-not-cross-service-boundary",
            },
        )
