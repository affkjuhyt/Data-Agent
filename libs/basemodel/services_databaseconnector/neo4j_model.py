from pydantic import BaseModel, ConfigDict
from typing import Optional, Any, List
import uuid
from enum import Enum

"""
Enum
"""
class RelationshipDirection(Enum):
    OUTGOING = ">"
    INCOMING = "<"
    ANY = ""



"""
Base Struct
"""
class NodePayload(BaseModel):
    block_id: uuid.UUID
    tenant_id: uuid.UUID
    data_id: uuid.UUID
    description: str

class NodeData(BaseModel):
    label: str
    properties: NodePayload
    embedding: Optional[list[float]] = None

class RelationshipData(BaseModel):
    type: str
    properties: Optional[dict[str, Any]] = None
    direction: RelationshipDirection = RelationshipDirection.OUTGOING

    model_config = ConfigDict(use_enum_values=True)


"""
Request Model
"""
# API Request
class AddNodeRequest(BaseModel):
    node: NodeData

class AddRelationshipRequest(BaseModel):
    from_node_id: str
    to_node_id: str
    relationship: RelationshipData

class GraphExpandRequest(BaseModel):
    start_node_id: str
    max_hops: int = 1
    max_neighbours: int = 10
    query_vector: Optional[list[float]] = None
