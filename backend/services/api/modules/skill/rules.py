"""Rules of the skill module that the database does not hold: the parameter schema a version
declares and the parameters a run brings (A3 POST /v1/skills/{id}/runs, 422 validation_failed).

Both are checked by jsonschema (Draft 2020-12, with format checking). What a skill may declare
is still held to the subset its parameters use (UC-03-03 step 3: period, metric, dimension,
threshold): object properties, required, enum, type, string format "date", minimum/maximum and
array items. A schema using anything else is refused when the version is saved, so a form or a
run never meets a keyword the product does not draw or explain.
"""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from services.api.kernel.problems import FieldError

# skill.skill.category CHECK; a suggestion with another category becomes "custom".
CATEGORIES = frozenset(
    {"decomposition", "causal", "forecast", "reconciliation", "quality", "report", "custom"}
)
TYPES = ("string", "number", "integer", "boolean", "array", "object")
KEYWORDS = frozenset(
    {
        "type",
        "properties",
        "required",
        "enum",
        "format",
        "minimum",
        "maximum",
        "items",
        "default",
        "title",
        "description",
        "additionalProperties",
        "$schema",
    }
)
# BR-98 / AC-03-07-05: a suggestion carries step kinds, metrics and operations, never what a
# session produced. The nightly scan is meant to write structure only; these keys are dropped on
# the way out in case it does not, since the viewer may not be the sessions' author.
CONTENT_KEYS = frozenset(
    {
        "result",
        "results",
        "output",
        "outputs",
        "rows",
        "data",
        "values",
        "cells",
        "content",
        "preview",
        "sample",
        "samples",
    }
)
# Skill scopes from the narrowest; widening needs the review of UC-03-05 (BR-12, BR-96).
SCOPE_RANK = {"personal": 0, "data_space": 1, "org": 2}


def _error(field: str, message: str, code: str = "invalid") -> FieldError:
    return FieldError(field=field, code=code, message=message)


def _path(where: str, path: Any) -> str:
    out = where
    for part in path:
        out += f"[{part}]" if isinstance(part, int) else f".{part}"
    return out


def _unsupported(schema: Any, where: str) -> list[FieldError]:
    """The product's subset: keywords outside it, at any depth, are refused by name."""
    if not isinstance(schema, dict):
        return []
    errors: list[FieldError] = []
    unknown = sorted(set(schema) - KEYWORDS)
    if unknown:
        errors.append(_error(where, f"Unsupported keywords: {', '.join(unknown)}."))
    kind = schema.get("type")
    if isinstance(kind, str) and kind not in TYPES:
        errors.append(_error(f"{where}.type", f"type is one of {', '.join(TYPES)}."))
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, sub in properties.items():
            errors.extend(_unsupported(sub, f"{where}.properties.{name}"))
    errors.extend(_unsupported(schema.get("items"), f"{where}.items"))
    return errors


def schema_errors(schema: Any, where: str = "params_schema") -> list[FieldError]:
    """Why `schema` is not a parameter schema this module can check runs against."""
    if not isinstance(schema, dict):
        return [_error(where, "A JSON Schema object is expected.")]
    errors = _unsupported(schema, where)
    if schema.get("type") != "object":
        errors.append(_error(f"{where}.type", "The parameters of a skill are an object."))
    # A valid schema by the meta-schema: a keyword's own value has the right type ("minimum": "0"
    # would fail every run that compares to it), a type is one JSON Schema knows.
    for problem in Draft202012Validator(Draft202012Validator.META_SCHEMA).iter_errors(schema):
        field = _path(where, problem.absolute_path)
        if not any(e.field == field for e in errors):
            errors.append(_error(field, problem.message))
    properties = schema.get("properties")
    required = schema.get("required")
    # JSON Schema allows a required name nothing declares; a skill's form could never ask it.
    if (
        isinstance(properties, dict)
        and isinstance(required, list)
        and (missing := sorted({r for r in required if isinstance(r, str)} - set(properties)))
    ):
        errors.append(_error(f"{where}.required", f"Not declared: {', '.join(missing)}."))
    return errors


# How each failed keyword reads to the caller, as `code`.
_CODES = {"required": "missing", "additionalProperties": "extra"}


def param_errors(schema: dict[str, Any], value: Any, where: str = "params") -> list[FieldError]:
    """Why `value` does not satisfy `schema` (a schema that passed `schema_errors`)."""
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    found: list[FieldError] = []
    for problem in validator.iter_errors(value):
        found.extend(_param_errors(problem, where))
    # A value of the wrong type says only that; its other checks mean nothing.
    typed = {e.field for e in found if e.code == "type"}
    return [e for e in found if e.code == "type" or e.field not in typed]


def _param_errors(problem: ValidationError, where: str) -> list[FieldError]:
    field = _path(where, problem.absolute_path)
    keyword = str(problem.validator)
    instance: Any = problem.instance
    if keyword == "required" and isinstance(instance, dict):
        return [
            _error(f"{field}.{name}", "Required.", "missing")
            for name in problem.validator_value
            if name not in instance
        ]
    if keyword == "additionalProperties" and isinstance(instance, dict):
        declared = problem.schema.get("properties", {})
        return [
            _error(f"{field}.{name}", "Not a parameter of this skill.", "extra")
            for name in instance
            if name not in declared
        ]
    return [_error(field, problem.message, _CODES.get(keyword, keyword))]


def widens(current: str, requested: str) -> bool:
    return SCOPE_RANK[requested] > SCOPE_RANK[current]


def approval_steps(scope: str) -> int:
    """BR-96: org-wide needs the system administrator's step after the steward's."""
    return 2 if scope == "org" else 1


def structure_only(pattern: Any) -> Any:
    """`pattern` without the session content BR-98 keeps from other people (CONTENT_KEYS).
    A params_schema is kept whole: its property names are the parameters, not data."""
    if isinstance(pattern, list):
        return [structure_only(item) for item in pattern]
    if not isinstance(pattern, dict):
        return pattern
    return {
        key: value if key == "params_schema" else structure_only(value)
        for key, value in pattern.items()
        if key not in CONTENT_KEYS
    }
