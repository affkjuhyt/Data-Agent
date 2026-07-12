"""Shared helpers + generic action/status responses used across domains."""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel


# ── Row-shaping helpers (used by response validators + kb_ui_operation) ────────

def to_string(v) -> str:
    return str(v) if v is not None else ""


def parse_jsonb(v) -> dict:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (ValueError, TypeError):
            return {}
    return v or {}


def coerce_jsonb_dict(v) -> dict:
    """Parse a JSONB value to a dict, recovering from the list shape left by a
    legacy double-encode bug: `object || "stringified-json"` concatenates into an
    array `[{...}, "{...}"]` instead of merging into one object. Merge every dict /
    JSON-string element back together so a single corrupt row can't 500 the caller.
    """
    parsed = parse_jsonb(v)
    if isinstance(parsed, dict):
        return parsed
    recovered: dict = {}
    for el in (parsed if isinstance(parsed, list) else []):
        if isinstance(el, dict):
            recovered.update(el)
        elif isinstance(el, str):
            try:
                inner = json.loads(el)
                if isinstance(inner, dict):
                    recovered.update(inner)
            except (ValueError, TypeError):
                pass
    return recovered


# ── Generic mutation / action responses ───────────────────────────────────────

class StatusResponse(BaseModel):
    status: str


class ConfigActivateResponse(BaseModel):
    status: str
    config_id: str


class VersionCreatedResponse(BaseModel):
    version_id: str | None = None
    version_number: int


class ConfigCreatedResponse(BaseModel):
    config_id: str | None = None
    version_number: int


class PolicyCreatedResponse(BaseModel):
    policy_id: str | None = None


class RowUpdateResponse(BaseModel):
    status: str
    row: dict[str, Any]
