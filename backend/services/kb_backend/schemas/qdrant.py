"""Qdrant collection / search schemas — request, response."""
from __future__ import annotations

from pydantic import BaseModel, Field


class QdrantSearchRequest(BaseModel):
    query: str
    limit: int = Field(default=10, ge=1, le=20)


class ToggleQdrantCollectionRequest(BaseModel):
    active: bool
