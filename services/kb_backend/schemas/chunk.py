"""Chunk / version schemas — domain, request, response."""
from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel

from services.kb_backend.schemas.enums import Status


# ── Domain ────────────────────────────────────────────────────────────────────

class ChunkVersionDetail(BaseModel):
    version_number: str
    create_at: str
    status: Status
    embedding_models: str
    entities: List[str]
    intent: str
    text: str


class ChunkVersion(BaseModel):
    version_number: str
    create_at: str
    status: str
    embedding_models: str
    entities: list[Any]
    intent: str
    text: str


# ── Request ───────────────────────────────────────────────────────────────────

class ActivateChunkVersionRequest(BaseModel):
    version_number: str


class CreateChunkVersionRequest(BaseModel):
    text: str
    entities: list[str] = []
    intents: list[str] = []
    embedding_model: str = ""


class CreateDocConfigRequest(BaseModel):
    version_number: str
    connection: Optional[dict[str, Any]] = None
    tables: list[Any]


# ── Response ──────────────────────────────────────────────────────────────────

class ChunkResponse(BaseModel):
    id: str
    title: str
    text: str
    versions: list[ChunkVersion]
