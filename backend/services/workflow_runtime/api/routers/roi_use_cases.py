from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from services.workflow_runtime.api.auth import get_current_user
from services.workflow_runtime.application.runtime_services import platform_queries, roi_use_cases

router = APIRouter(prefix="/api/platform/roi")


def _tenant_and_actor(caller: dict[str, Any]) -> tuple[str, str | None]:
    tenant_id = platform_queries.resolve_tenant(caller)
    updated_by = caller.get("user_name") or caller.get("user_id") or None
    return tenant_id, updated_by


@router.get("/use-cases")
async def get_roi_use_cases(caller: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    tenant_id, _ = _tenant_and_actor(caller)
    return await roi_use_cases.summarize_use_cases(tenant_id)


@router.post("/run/{kind}")
async def run_roi_use_case(kind: str, caller: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    if kind not in {"weekly_report", "data_quality", "anomaly", "forecast"}:
        raise HTTPException(status_code=404, detail="unknown ROI use case")
    tenant_id, updated_by = _tenant_and_actor(caller)
    return await roi_use_cases.run_use_case(tenant_id, kind, updated_by=updated_by)


@router.post("/run-schedules")
async def run_roi_schedules(caller: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    tenant_id, updated_by = _tenant_and_actor(caller)
    return await roi_use_cases.run_due_schedules(tenant_id, updated_by=updated_by)
