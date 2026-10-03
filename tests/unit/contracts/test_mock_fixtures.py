"""The mock fixtures are Contract v1 (NOP-151): what consumers build against is what the API sends."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, TypeAdapter

from libs.contracts.v1 import (
    HTTP_STATUS,
    PAYLOAD_MODELS,
    CapabilityRef,
    EntityRef,
    ErrorBody,
    ErrorCode,
    Observation,
    ReviewCaseRef,
    SeriesRef,
    VersionedWrite,
    parse_envelope,
    typed_payload,
)
from tests.contract_mock.app import FIXTURES_DIR

pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.req("NOP-151")]

HAPPY: dict[str, type[BaseModel]] = {
    "entities.json": EntityRef,
    "series.json": SeriesRef,
    "observations.json": Observation,
    "review_cases.json": ReviewCaseRef,
    "capabilities.json": CapabilityRef,
}


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def test_every_fixture_file_is_covered_here() -> None:
    # A new fixture nobody validates could drift from the contract unnoticed.
    found = {p.relative_to(FIXTURES_DIR).as_posix() for p in FIXTURES_DIR.rglob("*.json")}
    expected = (
        {f"happy/{name}" for name in HAPPY}
        | {f"errors/{code.value}.json" for code in ErrorCode}
        | {"version_conflict/request.json", "version_conflict/response.409.json"}
        | {f"events/{t.value}.v{v}.json" for t, v in PAYLOAD_MODELS}
    )
    assert found == expected


@pytest.mark.parametrize("name", sorted(HAPPY))
def test_happy_fixture_is_a_non_empty_list_of_its_model(name: str) -> None:
    data = _read(FIXTURES_DIR / "happy" / name)
    items: list[BaseModel] = TypeAdapter(list[HAPPY[name]]).validate_python(data)  # type: ignore[valid-type]
    assert items
    # Round-trips unchanged, so the file is exactly what the API serialises.
    assert [i.model_dump(mode="json") for i in items] == data


def test_happy_series_and_observations_refer_to_each_other() -> None:
    entities = {e["entity_id"] for e in _read(FIXTURES_DIR / "happy" / "entities.json")}
    series = _read(FIXTURES_DIR / "happy" / "series.json")
    assert {s["entity_id"] for s in series} <= entities
    keys = {s["series_key"] for s in series}
    observed = {o["series_key"] for o in _read(FIXTURES_DIR / "happy" / "observations.json")}
    assert observed == keys


def test_happy_observations_include_a_revision() -> None:
    # Two versions of one period let a consumer handle effective_version > 1.
    versions = [o["effective_version"] for o in _read(FIXTURES_DIR / "happy" / "observations.json")]
    assert max(versions) >= 2


@pytest.mark.parametrize("code", list(ErrorCode))
def test_each_error_code_has_its_body(code: ErrorCode) -> None:
    body = ErrorBody.model_validate(_read(FIXTURES_DIR / "errors" / f"{code.value}.json"))
    assert body.error.code is code
    assert code in HTTP_STATUS


def test_version_conflict_pair_is_a_stale_write_and_its_409() -> None:
    request = VersionedWrite.model_validate(
        _read(FIXTURES_DIR / "version_conflict" / "request.json")
    )
    response = ErrorBody.model_validate(
        _read(FIXTURES_DIR / "version_conflict" / "response.409.json")
    )
    assert response.error.code is ErrorCode.VERSION_CONFLICT
    assert HTTP_STATUS[response.error.code] == 409
    assert response.error.current_version is not None
    assert response.error.current_version > request.expected_version


@pytest.mark.parametrize(("event_type", "version"), sorted(PAYLOAD_MODELS))
def test_each_typed_topic_has_a_valid_envelope(event_type: Any, version: int) -> None:
    raw = (FIXTURES_DIR / "events" / f"{event_type.value}.v{version}.json").read_text(
        encoding="utf-8"
    )
    envelope = parse_envelope(raw)
    assert envelope.event_type is event_type
    assert envelope.schema_version == version
    assert isinstance(typed_payload(envelope), PAYLOAD_MODELS[(event_type, version)])
