"""Conflict schemas — domain, request, response."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


# ── Domain ────────────────────────────────────────────────────────────────────

class ConflictSummary(BaseModel):
    conflict_id: str
    conflict_type: str
    severity: str
    detected_at: str


class PendingConflictBatch(BaseModel):
    batch_id: str
    batch_name: str | None
    extracted_date: str
    number_pending_conflict: int
    conflicts: list[ConflictSummary]


# ── Request ───────────────────────────────────────────────────────────────────

class BatchConflictChoice(BaseModel):
    conflict_id: str
    selected_resolution_method: str
    resolution_instruction: Optional[str] = None


class ResolveBatchRequest(BaseModel):
    resolutions: list[BatchConflictChoice]


# ── Response ──────────────────────────────────────────────────────────────────

class ConflictBucketsResponse(BaseModel):
    pending: list[PendingConflictBatch]
    awaiting: list[ConflictSummary]
    resolved: list[ConflictSummary]


class ConflictDetailResponse(BaseModel):
    conflict_id: str
    conflict_type: str
    where_happens: str
    severity: str
    detected_at: str
    status: str | None
    batch_id: str
    detailed_explanation: str
    existing_snapshot: dict[str, Any]
    incoming_snapshot: dict[str, Any]
    affected_location: str
    resolution_instruction: str
    selected_resolution_method: str | None
    resolved_at: str | None
    resolved_by: str | None


class ConflictResolvedResponse(BaseModel):
    conflict_id: str
    status: str


class BatchResolvedResponse(BaseModel):
    batch_id: str
    status: str
