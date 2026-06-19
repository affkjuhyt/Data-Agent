from typing import Optional
from pydantic import BaseModel
from .enum_type import *

# - Request Models
# POST /api/knowledge/policies/filtering
class RequestCreateFilterPolicy(BaseModel):
    name: str
    type: str
    content: str
    added_by: Optional[str] = 'user'

# PUT /api/knowledge/policies/filtering/:id
class RequestUpdateFilterPolicy(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    content: Optional[str] = None
    active: Optional[bool] = None

# PUT /api/knowledge/policies/extraction/custom
class RequestExtractionCustom(BaseModel):
    custom: str


# - Response Models
# GET /api/knowledge/policies/filtering
# POST/PUT /api/knowledge/policies/filtering
class FilterPolicyConfigure(BaseModel):
    id: str
    name: str
    type: str
    content: str
    added_by: str
    added_when: str
    active: bool

# GET /api/knowledge/policies/extraction
class ExtractionPolicyConfigure(BaseModel):
    content: str
    last_modified_by: str
    last_modified_at: str
