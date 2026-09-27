"""Neo4j graph / schema schemas — domain, request, response."""
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


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


# A node label as Neo4j would accept it unquoted. The server interpolates these
# into Cypher, so this pattern is what keeps the query injection-free.
Neo4jLabel = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=64)]


class Neo4jQueryRequest(BaseModel):
    """A path from `start_label` through `step_labels`: the shape the
    Knowledge Hub query builder offers. The server writes the Cypher itself.
    It used to accept raw Cypher and reject only a few leading keywords, so
    `MATCH (n) DETACH DELETE n` ran, and a query that left out the
    `$tenant_id` predicate read other tenants' graphs."""
    start_label: Neo4jLabel
    step_labels: list[Neo4jLabel] = Field(default_factory=list, max_length=4)


class Neo4jGraphResponse(BaseModel):
    nodes: list[Neo4jNode]
    edges: list[Neo4jEdge]


class Neo4jSchemaResponse(BaseModel):
    entities: list[str]
    connections: dict[str, list[str]]
