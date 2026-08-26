"""Filter / extraction policy schemas — request, response."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class CreateFilterPolicyRequest(BaseModel):
    name: str
    type: str
    content: str
    added_by: Optional[str] = "user"


class UpdateFilterPolicyRequest(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    content: Optional[str] = None
    active: Optional[bool] = None


class UpdateExtractionPolicyRequest(BaseModel):
    custom: str


class FilterPolicyResponse(BaseModel):
    id: str
    name: str | None
    type: str
    content: str
    added_by: str
    added_when: str
    active: bool


class ExtractionPolicyResponse(BaseModel):
    base: str
    custom: str
