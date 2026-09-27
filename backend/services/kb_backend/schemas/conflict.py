"""Conflict schemas — domain, request, response."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class BatchConflictChoice(BaseModel):
    conflict_id: str
    selected_resolution_method: str
    resolution_instruction: Optional[str] = None


class ResolveBatchRequest(BaseModel):
    resolutions: list[BatchConflictChoice]
