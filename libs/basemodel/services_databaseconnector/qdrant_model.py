import datetime
from enum import Enum
from pydantic import BaseModel, ConfigDict
from typing import Any, List, Optional
import uuid


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
class RetryNumber(BaseModel):
    count: int = 5

class HealthCheckLoopConfig(BaseModel):
    count: int = 5
    interval: int = 5
    timeout_for_health_check: int = 5

# Point Data Requirement
class PointPayload(BaseModel):
    tenant_id: Optional[str] = None
    data_id: uuid.UUID
    block_id: uuid.UUID
    summary: str
    entities: List[str]
    intents: List[str]
    created_at: datetime.datetime = datetime.datetime.now()
    is_deleted: bool = False

class PointData(BaseModel):
    vector: list[float]
    payload: PointPayload

class MatchingPayload(BaseModel):
    field: str
    values: List[Any]

class PaginationConfig(BaseModel):
    pagination_on: str
    pagination_cursor: Any | None = None


"""
Request Model
"""
class BuildFilterModel(BaseModel):
    tenant_id: str
    data: List[MatchingPayload]
    type: MatchType | None = None
    pagination_config : PaginationConfig | None = None

# Request Model
class CreateCollectionRequest(BaseModel):
    name: str
    vector_size: int = 128
    distance_metric: DistanceMetric = DistanceMetric.Cosine
    model_config = ConfigDict(use_enum_values=True)

class DeleteCollectionRequest(BaseModel):
    collection_name: str


class AddPointsRequest(BaseModel):
    tenant_id: str
    collection_name: str
    points: list[PointData]

class SoftDeletePointsRequest(BaseModel):
    tenant_id: List[str]
    collection_name: str
    data_ids: List[str] = []
    block_ids: List[str] = []
    matching_payload: List[MatchingPayload] = []

class DeletePointsRequest(BaseModel):
    tenant_id: List[str]
    collection_name: str

class UpdatePayloadRequest(BaseModel):
    tenant_id: str
    collection_name: str
    matching_payload: List[MatchingPayload]
    payload: dict[str, Any]

class QueryByPayloadRequest(BaseModel):
    tenant_id: str
    collection_name: str
    matching_payload: List[MatchingPayload] | None = None
    limit: int = 10
    match_type: MatchType = MatchType.must
    pagination_config: PaginationConfig | None = None
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