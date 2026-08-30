"""LiteLLM-backed model catalogue and health.

Static sub-paths of /api/llm-providers. routers/catalog.py owns the bare
GET /api/llm-providers; there is no path-parameter route under that prefix, so
ordering between routers is not load-bearing — but these are registered before
catalog in app.py anyway so it stays that way if one is ever added.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from runtime_services import litellm_proxy

router = APIRouter()


@router.get("/api/llm-providers/models")
async def get_llm_models() -> dict[str, Any]:
    return await litellm_proxy.list_models()


@router.get("/api/llm-providers/health")
async def get_llm_health() -> dict[str, Any]:
    return await litellm_proxy.models_health()
