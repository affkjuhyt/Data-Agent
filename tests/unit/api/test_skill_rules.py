"""A skill's parameter schema and the parameters a run brings (A3 POST /v1/skills/{id}/runs)."""

from __future__ import annotations

from typing import Any

import pytest

from services.api.modules.skill import rules

pytestmark = pytest.mark.unit

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "period": {"type": "string", "format": "date"},
        "threshold": {"type": "number", "minimum": 0, "maximum": 1},
        "regions": {"type": "array", "items": {"type": "string", "enum": ["north", "south"]}},
        "top": {"type": "integer"},
    },
    "required": ["period"],
    "additionalProperties": False,
}


def _fields(errors: list[Any]) -> dict[str, str]:
    return {e.field: e.code for e in errors}


def test_a_schema_of_the_supported_keywords_passes() -> None:
    assert rules.schema_errors(SCHEMA) == []


@pytest.mark.parametrize(
    ("schema", "field"),
    [
        ({"type": "object", "oneOf": []}, "params_schema"),
        ({"type": "array"}, "params_schema.type"),
        (
            {"type": "object", "properties": {"a": {"type": "decimal"}}},
            "params_schema.properties.a.type",
        ),
        ({"type": "object", "required": ["b"], "properties": {}}, "params_schema.required"),
        # A keyword's own value is checked too: a string minimum was compared at run time, a 500.
        (
            {"type": "object", "properties": {"t": {"type": "number", "minimum": "0"}}},
            "params_schema.properties.t.minimum",
        ),
        (
            {"type": "object", "properties": {"e": {"enum": "north"}}},
            "params_schema.properties.e.enum",
        ),
    ],
)
def test_a_schema_the_runs_could_not_be_checked_against_is_refused(schema: Any, field: str) -> None:
    assert field in _fields(rules.schema_errors(schema))


def test_good_parameters_have_no_errors() -> None:
    params = {"period": "2026-09-30", "threshold": 0.5, "regions": ["north"], "top": 3}
    assert rules.param_errors(SCHEMA, params) == []


def test_each_wrong_parameter_is_named_with_why() -> None:
    errors = rules.param_errors(
        SCHEMA,
        {"period": "2026-02-30", "threshold": 2, "regions": ["east"], "top": True, "x": 1},
    )
    assert _fields(errors) == {
        "params.period": "format",
        "params.threshold": "maximum",
        "params.regions[0]": "enum",
        # A boolean is not an integer, though Python says True == 1.
        "params.top": "type",
        "params.x": "extra",
    }


def test_a_missing_parameter_is_named() -> None:
    assert _fields(rules.param_errors(SCHEMA, {})) == {"params.period": "missing"}


def test_a_wrong_type_is_the_only_error_of_its_parameter() -> None:
    assert _fields(rules.param_errors(SCHEMA, {"period": 20260930})) == {"params.period": "type"}
