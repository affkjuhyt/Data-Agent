"""Conflict-resolution package public API."""

from services.kb_backend.workers.conflict_resolution.orchestrator import resolve_batch
from services.kb_backend.workers.conflict_resolution.polling import (
    conflict_resolve_loop,
    fetch_awaiting_batch_ids,
)

__all__ = [
    "conflict_resolve_loop",
    "fetch_awaiting_batch_ids",
    "resolve_batch",
]
