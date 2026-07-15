"""LightRAG retrieval schemas — request, response."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from services.kb_backend.pipeline.light_rag import config as cfg


class RetrieveRequest(BaseModel):
    query: str
    tenant_id: str = ""
    collection: str = cfg.QDRANT_COLLECTION
    # Restrict retrieval to one knowledge base. Empty = all KBs.
    knowledge_base: str = ""
    # Front-layer query-enhancement fields: provided entities skip the internal
    # canonicalize LLM and drive pass-1 filter + graph seeds.
    entities: list[str] = []
    intent: str = ""
    entity_types: dict[str, str] = {}


class RetrieveResponse(BaseModel):
    source: str
    canonical: dict[str, Any]
    results: list[dict[str, Any]]
    timings: dict[str, float] = {}
