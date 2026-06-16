from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from .enum_type import *

#Model
class ConflictSummary(BaseModel):
    conflict_id: str
    conflict_type: ConflictType
    severity: ConflictSeverity
    detected_at: str

class ConflictBatch(BaseModel):
    batch_id: str
    batch_name: str
    extracted_date: str
    number_pending_conflict: int
    conflicts: List[ConflictSummary]


# API Model
# - Request
# PATCH /api/knowledge/conflicts/:conflictId
class RequestResolveConflict(BaseModel):
    selected_resolution_method: str  # UI sends "Merge" / "Keep Existing" (Title Case)
    resolution_instruction: Optional[str] = None



# - Response Model
# GET /api/knowledge/conflicts
class ConflictsConfigure(BaseModel):
    pending: List[ConflictBatch]
    awaiting: List[ConflictSummary]
    resolved: List[ConflictSummary]

# GET /api/knowledge/conflicts/:conflictId
class ConflictDetailConfigure(BaseModel):
    conflict_id: str
    conflict_type: ConflictType
    where_happens: str
    severity: str
    detected_at: str
    status: ConflictStatus
    detailed_explanation: str
    existing_snapshot: Dict[str, Any]
    incoming_snapshot: Dict[str, Any]
    affected_location: str
    batch_id: str
    resolution_instruction: Optional[str] = None
    selected_resolution_method: Optional[ConflictResolution] = None
    resolved_at: Optional[str] = None
    resolved_by: Optional[str] = None

# PATCH /api/knowledge/conflicts/:conflictId
class ConflictResolveConfigure(BaseModel):
    conflict_id: str
    status: ConflictStatus
    selected_resolution_method: ConflictResolution
    resolution_instruction: Optional[str] = None
    resolved_at: Optional[str] = None
    resolved_by: Optional[str] = None
