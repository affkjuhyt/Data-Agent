"""indicator-api's OpenAPI is generated from Contract v1 (NOP-150) models."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from libs.contracts import CONTRACT_VERSION
from services.indicator_api.app import create_app

pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.req("NOP-162")]

CONTRACT_OPENAPI = (
    Path(__file__).resolve().parents[3]
    / "backend"
    / "libs"
    / "contracts"
    / "generated"
    / "contracts.openapi.json"
)

# FastAPI's own 422 body, not a contract model.
FRAMEWORK_SCHEMAS = {"HTTPValidationError", "ValidationError"}

# The route table the NOP-151 mock published (tests/contract_mock/app.py).
MOCK_ROUTES = {
    ("get", "/entities"),
    ("get", "/entities/{entity_id}"),
    ("get", "/series"),
    ("get", "/series/{series_key}"),
    ("get", "/series/{series_key}/observations"),
    ("get", "/query"),
    ("get", "/review/cases"),
    ("get", "/review/cases/{case_id}"),
    ("post", "/review/cases/{case_id}/approve"),
    ("post", "/review/cases/{case_id}/reject"),
    ("get", "/capabilities"),
}


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    return create_app(adapters=[]).openapi()


@pytest.fixture(scope="module")
def contract() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(CONTRACT_OPENAPI.read_text(encoding="utf-8"))
    return loaded


def test_the_api_version_is_the_contract_version(spec: dict[str, Any]) -> None:
    assert spec["info"]["version"] == CONTRACT_VERSION


def test_every_mock_route_is_published(spec: dict[str, Any]) -> None:
    published = {(method, path) for path, ops in spec["paths"].items() for method in ops}
    assert published >= MOCK_ROUTES


def test_every_schema_is_a_contract_v1_schema(
    spec: dict[str, Any], contract: dict[str, Any]
) -> None:
    schemas = set(spec["components"]["schemas"]) - FRAMEWORK_SCHEMAS
    assert schemas
    assert schemas <= set(contract["components"]["schemas"])


def test_every_schema_carries_its_contract_schema_version(
    spec: dict[str, Any], contract: dict[str, Any]
) -> None:
    for name, schema in spec["components"]["schemas"].items():
        if name in FRAMEWORK_SCHEMAS:
            continue
        published = contract["components"]["schemas"][name]
        assert schema.get("x-schema-version") == published.get("x-schema-version"), name
