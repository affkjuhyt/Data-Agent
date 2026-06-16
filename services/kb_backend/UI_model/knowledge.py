from typing import Any, List, Optional
from pydantic import BaseModel, ConfigDict, Field
from .enum_type import *

# DATABASE tab
class ChunkVersionDetail(BaseModel):
    version_number: str
    create_at: str
    status: Status
    embedding_models: str
    entities: List[str]
    intent: str
    text: str

# DATABASE tab — warehouse config table entry
class KnowledgeConfigTable(BaseModel):
    id: str
    schema: str
    table_name: str
    row_count: int
    columns: int

# QDRANT tab — single search result
class QdrantSearchResult(BaseModel):
    point_id: str
    score: float
    summary: str
    entities: List[str]
    intent: List[str]

# - Request Models
# PATCH /api/knowledge/documents/:id/chunks/:chunkId/activate
class RequestActivateChunkVersion(BaseModel):
    version_number: str

# POST /api/knowledge/documents/:id/chunks/:chunkId/versions
class RequestCreateChunkVersion(BaseModel):
    text: str

# POST /api/knowledge/warehouses/:id/configs
class RequestWarehouseConfigCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str
    version: str
    status: Optional[str] = 'Draft'
    tables: list
    sync_schedule: Optional[str] = Field(default='Manual', alias='syncSchedule')

# PATCH /api/knowledge/documents/:doc_id/tables/:table_id/rows/:row_index
class RequestTableRowUpdate(BaseModel):
    column: str
    value: Any = None

# POST /api/knowledge/documents/:doc_id/configs
class RequestDocConfig(BaseModel):
    version_number: str
    connection: Optional[dict] = None
    tables: list

# POST /api/knowledge/neo4j/query
class RequestNeo4jQuery(BaseModel):
    cypher: str

# PATCH /api/knowledge/qdrant/collections/:id
class RequestToggleQdrantCollection(BaseModel):
    active: bool

# POST /api/knowledge/qdrant/collections/:id/search
class RequestSearchQdrant(BaseModel):
    query: str



# - Response Models
# GET /api/knowledge/documents/:id/chunks
class ChunkConfigure(BaseModel):
    id: str
    title: str
    text: str
    versions: List[ChunkVersionDetail]

# GET /api/knowledge/warehouses/:id/configs
# POST /api/knowledge/warehouses/:id/configs
class WarehouseConfigConfigure(BaseModel):
    id: str
    name: str
    version: str
    created_at: str
    status: str
    tables: List[KnowledgeConfigTable]
    sync_schedule: Optional[str] = None

# GET /api/knowledge/qdrant/collections
# PATCH /api/knowledge/qdrant/collections/:id
class QdrantCollectionConfigure(BaseModel):
    id: str
    name: str
    points: int
    active: bool
    dimensions: int
    distance: str
    indexed: Optional[int] = None
    embedding_model: Optional[str] = None
