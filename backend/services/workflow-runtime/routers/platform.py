"""Admin dashboard data: tenant metrics, dependency health, settings store.

All reads are real queries (runtime_services/platform_queries.py) or live
probes (runtime_services/service_probes.py); an empty tenant gets zeros.
Authentication is attached at include time in app.py, and the tenant comes
from the verified caller, never a header.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from auth import get_current_user
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel
from runtime_services import platform_queries, service_probes

logger = logging.getLogger(__name__)

router = APIRouter()

SETTINGS_KEY_RE = re.compile(r"^[a-z0-9_\-]{1,100}$")


def validate_settings_key(key: str) -> str:
    if not SETTINGS_KEY_RE.match(key or ""):
        raise HTTPException(
            status_code=422,
            detail="settings key must match ^[a-z0-9_\\-]{1,100}$",
        )
    return key


class SettingBody(BaseModel):
    value: Any


@router.get("/api/platform/metrics")
async def get_platform_metrics(
    days: int = Query(7, ge=1, le=90),
    caller: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    tenant_id = platform_queries.resolve_tenant(caller)
    try:
        return await platform_queries.platform_metrics(tenant_id, days=days)
    except Exception as e:  # noqa: BLE001 — dashboard must render, with zeros, over a DB hiccup
        logger.warning("platform/metrics failed tenant=%s: %s", tenant_id, e)
        payload = platform_queries.empty_metrics(days)
        payload["error"] = str(e)[:300]
        return payload


@router.get("/api/platform/services")
async def get_platform_services(
    caller: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    return {"items": await service_probes.probe_all()}


@router.get("/api/platform/settings/{key}")
async def get_platform_setting(
    key: str = Path(...),
    caller: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    validate_settings_key(key)
    tenant_id = platform_queries.resolve_tenant(caller)
    return await platform_queries.get_setting(tenant_id, key)


@router.put("/api/platform/settings/{key}")
async def put_platform_setting(
    body: SettingBody,
    key: str = Path(...),
    caller: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    validate_settings_key(key)
    tenant_id = platform_queries.resolve_tenant(caller)
    updated_by = caller.get("user_name") or caller.get("user_id") or None
    try:
        return await platform_queries.put_setting(tenant_id, key, body.value, updated_by)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
