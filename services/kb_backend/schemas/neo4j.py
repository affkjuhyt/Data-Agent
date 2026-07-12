"""Neo4j graph / schema schemas — domain, request, response."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


# ── Domain ────────────────────────────────────────────────────────────────────

class Neo4jNode(BaseModel):
    id: str
    name: str | None
    description: str | None


class Neo4jEdge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    source: str = Field(alias="from")
    to: str
    description: str | None
    score: float | None


# ── Request ───────────────────────────────────────────────────────────────────

class Neo4jQueryRequest(BaseModel):
    cypher: str


# ── Response ──────────────────────────────────────────────────────────────────

class Neo4jGraphResponse(BaseModel):
    nodes: list[Neo4jNode]
    edges: list[Neo4jEdge]


class Neo4jSchemaResponse(BaseModel):
    entities: list[str]
    connections: dict[str, list[str]]
