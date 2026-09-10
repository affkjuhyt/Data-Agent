"""Conflict-resolution package public API."""

from services.kb_conflict_resolution.libs.orchestrator import resolve_batch
from services.kb_conflict_resolution.libs.polling import (
    conflict_resolve_loop,
    fetch_awaiting_batch_ids,
)

__all__ = [
    "conflict_resolve_loop",
    "fetch_awaiting_batch_ids",
    "resolve_batch",
]
