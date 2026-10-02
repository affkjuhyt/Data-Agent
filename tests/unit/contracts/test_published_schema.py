"""The committed schema artifacts match the models (NOP-150).

generated/ is what the frontend and other branches read without importing
Python; a model change that is not re-exported would leave them on a contract
that no longer exists.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from libs.contracts import CONTRACT_VERSION
from libs.contracts.schema_export import GENERATED_DIR, OPENAPI_NAME, render_all, stale_files
from libs.contracts.v1 import OPEN_ENUMS, PARTITION_KEY_FIELD, PAYLOAD_MODELS, PUBLIC_MODELS

from . import compat_fixtures

pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.req("NOP-150")]


@pytest.fixture(scope="module")
def openapi() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(
        (GENERATED_DIR / OPENAPI_NAME).read_text(encoding="utf-8")
    )
    return document


def test_generated_artifacts_are_current() -> None:
    stale = stale_files(render_all())
    assert not stale, "run `python -m libs.contracts.schema_export` and commit generated/"


def test_openapi_carries_the_contract_version(openapi: dict[str, Any]) -> None:
    assert openapi["info"]["version"] == CONTRACT_VERSION


def test_every_public_model_is_published_with_version_and_example(openapi: dict[str, Any]) -> None:
    schemas = openapi["components"]["schemas"]
    for model in PUBLIC_MODELS:
        published = schemas[model.__name__]
        assert published["x-schema-version"] == model.SCHEMA_VERSION
        assert len(published["examples"]) == 1


def test_every_typed_payload_version_has_its_own_schema_file() -> None:
    names = {p.name for p in (GENERATED_DIR / "events").glob("*.json")}
    expected = {f"{t.value}.v{v}.schema.json" for t, v in PAYLOAD_MODELS} | {
        "envelope.v1.schema.json"
    }
    assert names == expected


def test_the_wire_spelling_from_is_published(openapi: dict[str, Any]) -> None:
    time_rule = openapi["components"]["schemas"]["TimeRule"]["properties"]
    assert "from" in time_rule and "from_" not in time_rule


@pytest.mark.req("NOP-152")
def test_only_the_open_enums_are_published_as_open(openapi: dict[str, Any]) -> None:
    # The compatibility gate lets an x-open-enum grow without a bump; every
    # other enum is closed, so the flag must sit on exactly OPEN_ENUMS.
    schemas = openapi["components"]["schemas"]
    flagged = {name for name, schema in schemas.items() if schema.get("x-open-enum")}
    assert flagged == {enum.__name__ for enum in OPEN_ENUMS}


@pytest.mark.req("NOP-152")
def test_every_payload_schema_publishes_its_partition_key() -> None:
    for (event_type, version), _ in PAYLOAD_MODELS.items():
        path = GENERATED_DIR / "events" / f"{event_type.value}.v{version}.schema.json"
        schema = json.loads(path.read_text(encoding="utf-8"))
        assert schema["x-partition-key"] == PARTITION_KEY_FIELD[event_type]


def test_compat_fixtures_match_the_current_schema() -> None:
    for path, text in compat_fixtures.render().items():
        assert (
            path.read_text(encoding="utf-8") == text
        ), f"{path.name} is stale: run `python -m tests.unit.contracts.compat_fixtures`"


def test_breaking_without_bump_differs_from_base_in_one_field_at_the_same_version() -> None:
    def observation(path: Path) -> dict[str, Any]:
        document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        schema: dict[str, Any] = document["components"]["schemas"]["Observation"]
        return schema

    base = observation(compat_fixtures.COMPAT_DIR / "base.openapi.json")
    head = observation(compat_fixtures.COMPAT_DIR / "breaking_without_bump" / "head.openapi.json")
    changed = {k for k in base["properties"] if base["properties"][k] != head["properties"].get(k)}
    assert changed == {"unit"}
    assert base["x-schema-version"] == head["x-schema-version"]
