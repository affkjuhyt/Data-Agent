"""Fleet / dashboard stats schemas."""
from __future__ import annotations

from pydantic import BaseModel


class FleetContent(BaseModel):
    documents: int
    web: int
    media: int
    warehouses: int


class FleetStats(BaseModel):
    content: FleetContent
    qdrant_collections: int
    neo4j_nodes: int
    neo4j_relationships: int
    unresolved_conflict_batches: int
