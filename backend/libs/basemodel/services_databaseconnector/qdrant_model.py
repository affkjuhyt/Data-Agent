from enum import Enum
from pydantic import BaseModel, ConfigDict
from typing import Any, List


"""
Enum
"""
class DistanceMetric(Enum):
    Cosine = "Cosine"
    Euclidean = "Euclidean"
    Dot = "Dot"

class MatchType(Enum):
    must = "must"
    any = "any"


"""
Base Struct
"""


class MatchingPayload(BaseModel):
    field: str
    values: List[Any]

class PaginationConfig(BaseModel):
    pagination_on: str
    pagination_cursor: Any | None = None


"""
Request Model
"""

# Request Model
class CreateCollectionRequest(BaseModel):
    name: str
    vector_size: int = 128
    distance_metric: DistanceMetric = DistanceMetric.Cosine
    model_config = ConfigDict(use_enum_values=True)


class SearchRequest(BaseModel):
    tenant_id: str
    collection_name: str
    query_vector: list[float]
    limit: int = 10
    # Scope results to one knowledge base (the "kind of knowledge" a point belongs to).
    # Applied as a hard AND filter alongside tenant_id. None = search across all KBs.
    knowledge_base: str | None = None
    matching_payload: List[MatchingPayload] | None = None
    match_type: MatchType = MatchType.must
    pagination_config: PaginationConfig | None = None
