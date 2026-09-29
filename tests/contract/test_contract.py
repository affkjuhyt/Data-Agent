"""Contract tests for indicator-api (P0-09): the committed openapi.json is the agreed definition.

Runs in the indicator-api environment (Python 3.11 + its requirements). The
check against the base branch itself runs in CI (`contract` job), since it
needs that branch's openapi.json; here its logic is tested on edited copies.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from services.indicator_api.contract_check import contract_violations
from services.indicator_api.domains.catalog import contract_models
from services.indicator_api.export_openapi import OPENAPI_PATH, render

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def current() -> dict[str, Any]:
    schema: dict[str, Any] = json.loads(render())
    return schema


def test_committed_openapi_is_current():
    assert (
        OPENAPI_PATH.read_text(encoding="utf-8") == render()
    ), "run `python -m services.indicator_api.export_openapi` and commit openapi.json"


def test_every_business_object_is_published(current: dict[str, Any]):
    names = set(current["components"]["schemas"])
    expected = {m.__name__ for m in contract_models()} | {"EventEnvelope", "ErrorBody"}
    assert expected <= names, expected - names


def test_published_schemas_carry_their_version(current: dict[str, Any]):
    schemas = current["components"]["schemas"]
    for model in contract_models():
        assert schemas[model.__name__]["x-schema-version"] == model.SCHEMA_VERSION


def test_unchanged_contract_passes(current: dict[str, Any]):
    assert contract_violations(current, current) == []


def test_changing_a_field_without_a_bump_is_refused_and_named(current: dict[str, Any]):
    """AC-2: the type of Observation.unit changes, the version does not."""
    edited = copy.deepcopy(current)
    edited["components"]["schemas"]["Observation"]["properties"]["unit"] = {"type": "object"}
    violations = contract_violations(current, edited)
    assert len(violations) == 1
    assert violations[0].startswith("Observation: unit changed")


def test_the_same_change_with_a_bump_passes(current: dict[str, Any]):
    edited = copy.deepcopy(current)
    observation = edited["components"]["schemas"]["Observation"]
    observation["properties"]["unit"] = {"type": "object"}
    observation["x-schema-version"] += 1
    assert contract_violations(current, edited) == []


def test_a_new_required_field_is_a_change(current: dict[str, Any]):
    edited = copy.deepcopy(current)
    series = edited["components"]["schemas"]["Series"]
    series["properties"]["region"] = {"type": "string"}
    series["required"] = [*series["required"], "region"]
    assert contract_violations(current, edited) == [
        "Series: region, (required fields) changed but x-schema-version is still 1 (was 1); "
        "bump SCHEMA_VERSION"
    ]


def test_removing_a_business_object_is_refused(current: dict[str, Any]):
    edited = copy.deepcopy(current)
    del edited["components"]["schemas"]["Claim"]
    assert contract_violations(current, edited) == ["Claim: removed from the contract"]
