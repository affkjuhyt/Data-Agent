"""Spend as reported by LiteLLM — the only place model cost is metered."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from services.workflow_runtime.runtime_services import litellm_proxy

router = APIRouter()


@router.get("/api/billing/spend")
async def get_billing_spend(days: int = Query(30, ge=1, le=365)) -> dict[str, Any]:
    return await litellm_proxy.spend(days=days)
