"""Ingestion schemas — response (promote)."""
from __future__ import annotations

from pydantic import BaseModel


class PromoteResponse(BaseModel):
    data_id: str
    stage: str
    message: str
