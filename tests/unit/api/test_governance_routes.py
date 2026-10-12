"""Governance provider validation that does not require a database."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.config import Settings

from .conftest import ORG, FakeIdentities, FakeVerifier

pytestmark = [pytest.mark.unit, pytest.mark.contract]

ADMIN = "admin"
TOKENS = {ADMIN: {"sub": "kc-admin", "org_id": str(ORG)}}
USERS = {
    "kc-admin": (
        uuid.UUID("01920000-0000-7000-8000-000000000001"),
        frozenset({"admin"}),
        frozenset(),
    )
}


@pytest.fixture
def api() -> TestClient:
    app = create_app(
        Settings(), token_verifier=FakeVerifier(TOKENS), identity_resolver=FakeIdentities(USERS)
    )
    return TestClient(app, raise_server_exceptions=False)


def test_frontier_provider_requires_a_safe_external_contract(api: TestClient) -> None:
    response = api.post(
        "/v1/admin/model-providers",
        headers={"Authorization": f"Bearer {ADMIN}"},
        json={
            "code": "frontier-a",
            "display_name": "Frontier A",
            "kind": "frontier",
            "endpoint": "http://not-allowed",
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_failed"


def test_model_provider_approval_requires_compliance(api: TestClient) -> None:
    response = api.post(
        "/v1/admin/model-providers/01920000-0000-7000-8000-000000000002:approve",
        headers={"Authorization": f"Bearer {ADMIN}"},
        json={"note": "Reviewed contract."},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_model_provider_test_requires_a_model_name(api: TestClient) -> None:
    response = api.post(
        "/v1/admin/model-providers/01920000-0000-7000-8000-000000000002:test",
        headers={"Authorization": f"Bearer {ADMIN}"},
        json={},
    )
    assert response.status_code == 422
