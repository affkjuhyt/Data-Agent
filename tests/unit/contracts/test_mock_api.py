"""The Contract v1 mock API (NOP-151): every answer is a v1 model taken from the fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from libs.contracts.v1 import (
    HTTP_STATUS,
    CapabilityRef,
    EntityRef,
    ErrorBody,
    ErrorCode,
    Observation,
    ReviewCaseRef,
    SeriesRef,
)
from tests.contract_mock import create_app
from tests.contract_mock.app import FIXTURES_DIR

pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.req("NOP-151")]

BRENT = "EIA:RBRTE:D"
GOLD = "SJC:SJC_BAR:HCM:SELL:D"
DRIFT_CASE = "2e3f4a5b-6c7d-4e8f-9a0b-1c2d3e4f5a06"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app())


def _fixture(path: str) -> Any:
    return json.loads((FIXTURES_DIR / path).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("path", "fixture", "model"),
    [
        ("/entities", "happy/entities.json", EntityRef),
        ("/series", "happy/series.json", SeriesRef),
        ("/review/cases", "happy/review_cases.json", ReviewCaseRef),
        ("/capabilities", "happy/capabilities.json", CapabilityRef),
    ],
)
def test_list_routes_answer_their_fixture(
    client: TestClient, path: str, fixture: str, model: type
) -> None:
    response = client.get(path)
    assert response.status_code == 200
    TypeAdapter(list[model]).validate_python(response.json())  # type: ignore[valid-type]
    assert response.json() == _fixture(fixture)


def test_get_one_entity_series_and_case(client: TestClient) -> None:
    assert (
        EntityRef.model_validate(client.get("/entities/BRENT_SPOT").json()).entity_id
        == "BRENT_SPOT"
    )
    assert SeriesRef.model_validate(client.get(f"/series/{BRENT}").json()).series_key == BRENT
    case = ReviewCaseRef.model_validate(client.get(f"/review/cases/{DRIFT_CASE}").json())
    assert str(case.case_id) == DRIFT_CASE


def test_observations_of_a_series_are_its_fixture_rows(client: TestClient) -> None:
    response = client.get(f"/series/{BRENT}/observations")
    assert response.status_code == 200
    expected = [o for o in _fixture("happy/observations.json") if o["series_key"] == BRENT]
    assert response.json() == expected
    rows = TypeAdapter(list[Observation]).validate_python(response.json())
    # Values travel as decimal strings, never floats.
    assert all(isinstance(o["value"], str) for o in response.json())
    assert {o.effective_version for o in rows} == {1, 2}


def test_query_returns_the_observations_of_each_series_asked(client: TestClient) -> None:
    response = client.get("/query", params=[("series_key", BRENT), ("series_key", GOLD)])
    assert response.status_code == 200
    rows = TypeAdapter(list[Observation]).validate_python(response.json())
    assert {o.series_key for o in rows} == {BRENT, GOLD}


def test_query_without_a_series_is_a_malformed_request(client: TestClient) -> None:
    assert client.get("/query").status_code == 422


@pytest.mark.parametrize(
    "path",
    [
        "/entities/BRENT_FUTURES",
        "/series/EIA:UNKNOWN:D",
        "/series/EIA:UNKNOWN:D/observations",
        "/query?series_key=EIA:UNKNOWN:D",
        "/review/cases/00000000-0000-0000-0000-000000000000",
    ],
)
def test_unknown_id_is_unresolved_entity(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == HTTP_STATUS[ErrorCode.UNRESOLVED_ENTITY] == 404
    assert ErrorBody.model_validate(response.json()).error.code is ErrorCode.UNRESOLVED_ENTITY


@pytest.mark.parametrize("code", list(ErrorCode))
def test_x_mock_error_answers_each_standard_error(client: TestClient, code: ErrorCode) -> None:
    response = client.get("/capabilities", headers={"X-Mock-Error": code.value})
    assert response.status_code == HTTP_STATUS[code]
    assert response.json() == _fixture(f"errors/{code.value}.json")


def test_x_mock_error_works_on_writes_too(client: TestClient) -> None:
    response = client.post(
        f"/review/cases/{DRIFT_CASE}/approve",
        json={"expected_version": 3},
        headers={"X-Mock-Error": "ACCESS_DENIED"},
    )
    assert response.status_code == 403


def test_unknown_x_mock_error_is_a_malformed_request(client: TestClient) -> None:
    assert client.get("/entities", headers={"X-Mock-Error": "TEAPOT"}).status_code == 422


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_deciding_on_the_current_version_bumps_it(client: TestClient, decision: str) -> None:
    response = client.post(f"/review/cases/{DRIFT_CASE}/{decision}", json={"expected_version": 3})
    assert response.status_code == 200
    assert ReviewCaseRef.model_validate(response.json()).version == 4


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_deciding_on_a_stale_version_is_the_version_conflict_fixture(
    client: TestClient, decision: str
) -> None:
    response = client.post(
        f"/review/cases/{DRIFT_CASE}/{decision}", json=_fixture("version_conflict/request.json")
    )
    assert response.status_code == 409
    assert response.json() == _fixture("version_conflict/response.409.json")


def test_a_decision_is_not_stored(client: TestClient) -> None:
    # The mock is stateless: deciding twice on version 3 succeeds twice.
    for _ in range(2):
        response = client.post(f"/review/cases/{DRIFT_CASE}/approve", json={"expected_version": 3})
        assert response.status_code == 200
    assert client.get(f"/review/cases/{DRIFT_CASE}").json()["version"] == 3


def test_a_write_without_expected_version_is_refused(client: TestClient) -> None:
    response = client.post(f"/review/cases/{DRIFT_CASE}/approve", json={})
    assert response.status_code == 422


def test_the_frontend_dev_server_may_call_it(client: TestClient) -> None:
    response = client.options(
        "/entities",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_a_fixture_that_drifted_from_the_contract_fails_at_startup(tmp_path: Path) -> None:
    for path in FIXTURES_DIR.rglob("*.json"):
        target = tmp_path / path.relative_to(FIXTURES_DIR)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    rows = json.loads((tmp_path / "happy" / "observations.json").read_text(encoding="utf-8"))
    rows[0]["value"] = 130.8
    (tmp_path / "happy" / "observations.json").write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(ValueError, match="float"):
        create_app(tmp_path)
