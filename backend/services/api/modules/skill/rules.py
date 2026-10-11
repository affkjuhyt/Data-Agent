"""Rules of the skill module that the database does not hold: the parameter schema a version
declares and the parameters a run brings (A3 POST /v1/skills/{id}/runs, 422 validation_failed).

The api image carries no JSON Schema library, so this reads the subset a skill's parameters use
(UC-03-03 step 3: period, metric, dimension, threshold): object properties, required, enum,
type, string format "date", minimum/maximum and array items. A schema using anything else is
refused when the version is saved, so a run never meets a keyword it would silently skip.
"""

from __future__ import annotations

import datetime
from typing import Any

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
# Skill scopes from the narrowest; widening needs the review of UC-03-05 (BR-12, BR-96).
SCOPE_RANK = {"personal": 0, "data_space": 1, "org": 2}


def _error(field: str, message: str, code: str = "invalid") -> FieldError:
    return FieldError(field=field, code=code, message=message)


def schema_errors(schema: Any, where: str = "params_schema") -> list[FieldError]:
    """Why `schema` is not a parameter schema this module can check runs against."""
    if not isinstance(schema, dict):
        return [_error(where, "A JSON Schema object is expected.")]
    errors: list[FieldError] = []
    unknown = sorted(set(schema) - KEYWORDS)
    if unknown:
        errors.append(_error(where, f"Unsupported keywords: {', '.join(unknown)}."))
    kind = schema.get("type")
    if kind is not None and kind not in TYPES:
        errors.append(_error(f"{where}.type", f"type is one of {', '.join(TYPES)}."))
    if where == "params_schema" and kind != "object":
        errors.append(_error(f"{where}.type", "The parameters of a skill are an object."))
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        errors.append(_error(f"{where}.properties", "properties is an object."))
        properties = {}
    for name, sub in properties.items():
        errors.extend(schema_errors(sub, f"{where}.properties.{name}"))
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(isinstance(r, str) for r in required):
        errors.append(_error(f"{where}.required", "required is a list of property names."))
    elif missing := sorted(set(required) - set(properties)):
        errors.append(_error(f"{where}.required", f"Not declared: {', '.join(missing)}."))
    if "enum" in schema and not isinstance(schema["enum"], list):
        errors.append(_error(f"{where}.enum", "enum is a list."))
    if "items" in schema:
        errors.extend(schema_errors(schema["items"], f"{where}.items"))
    return errors


def _type_ok(value: Any, kind: str) -> bool:
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "array":
        return isinstance(value, list)
    return isinstance(value, dict)


def param_errors(schema: dict[str, Any], value: Any, where: str = "params") -> list[FieldError]:
    """Why `value` does not satisfy `schema` (a schema that passed `schema_errors`)."""
    kind = schema.get("type")
    if kind is not None and not _type_ok(value, kind):
        return [_error(where, f"Expected {kind}.", "type")]
    errors: list[FieldError] = []
    if "enum" in schema and value not in schema["enum"]:
        errors.append(_error(where, "Not one of the allowed values.", "enum"))
    if schema.get("format") == "date" and isinstance(value, str):
        try:
            datetime.date.fromisoformat(value)
        except ValueError:
            errors.append(_error(where, "Expected a date YYYY-MM-DD.", "format"))
    if isinstance(value, int | float) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(_error(where, f"Below the minimum {schema['minimum']}.", "minimum"))
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(_error(where, f"Above the maximum {schema['maximum']}.", "maximum"))
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(value):
            errors.extend(param_errors(schema["items"], item, f"{where}[{i}]"))
    if isinstance(value, dict):
        properties: dict[str, Any] = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                errors.append(_error(f"{where}.{name}", "Required.", "missing"))
        for name, item in value.items():
            if name in properties:
                errors.extend(param_errors(properties[name], item, f"{where}.{name}"))
            elif schema.get("additionalProperties") is False:
                errors.append(_error(f"{where}.{name}", "Not a parameter of this skill.", "extra"))
    return errors


def widens(current: str, requested: str) -> bool:
    return SCOPE_RANK[requested] > SCOPE_RANK[current]


def approval_steps(scope: str) -> int:
    """BR-96: org-wide needs the system administrator's step after the steward's."""
    return 2 if scope == "org" else 1
