from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from services.kb_backend.pipeline.light_rag import config as cfg


class RetrieveRequest(BaseModel):
    query: str
    tenant_id: str = ""
    collection: str = cfg.QDRANT_COLLECTION
    knowledge_base: str = ""
    entities: list[str] = []
    intent: str = ""
    entity_types: dict[str, str] = {}


class RetrieveResponse(BaseModel):
    source: str
    canonical: dict[str, Any]
    results: list[dict[str, Any]]
    timings: dict[str, float] = {}
    degraded: list[str] = []
