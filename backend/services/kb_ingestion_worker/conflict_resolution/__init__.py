"""Conflict-resolution package public API."""

from services.kb_ingestion_worker.conflict_resolution.orchestrator import resolve_batch
from services.kb_ingestion_worker.conflict_resolution.polling import (
    conflict_resolve_loop,
    fetch_awaiting_batch_ids,
)

__all__ = [
    "conflict_resolve_loop",
    "fetch_awaiting_batch_ids",
    "resolve_batch",
]
