from __future__ import annotations

import json
from typing import Any, cast

from pydantic import BaseModel


def to_string(v: Any) -> str:
    return str(v) if v is not None else ""


def parse_jsonb(v: Any) -> dict[str, Any]:
    if isinstance(v, str):
        try:
            return cast("dict[str, Any]", json.loads(v))
        except (ValueError, TypeError):
            return {}
    return cast("dict[str, Any]", v or {})


def coerce_jsonb_dict(v: Any) -> dict[str, Any]:
    parsed = parse_jsonb(v)
    if isinstance(parsed, dict):
        return parsed
    recovered: dict[str, Any] = {}
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
