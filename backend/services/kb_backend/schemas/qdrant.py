"""Qdrant collection / search schemas — request, response."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class QdrantSearchRequest(BaseModel):
    query: str
    limit: int = Field(default=10, ge=1, le=20)


class ToggleQdrantCollectionRequest(BaseModel):
    active: bool


class QdrantCollectionResponse(BaseModel):
    id: str
    name: str | None
    active: bool
    points: int
    dimensions: int
    distance: str
    indexed: int
    embedding_model: str | None


class QdrantPointResponse(BaseModel):
    point_id: str
    score: float
    summary: str
    entities: list[Any]
    intent: list[Any]


class ToggleCollectionResponse(BaseModel):
    collection_id: str
    active: bool
