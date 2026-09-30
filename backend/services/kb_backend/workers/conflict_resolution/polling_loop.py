"""Backward-compatible facade for conflict resolution.
The implementation is organized under ``conflict_resolution`` by responsibility.
Keep this module so existing worker/bootstrap imports continue to work unchanged.
"""

from services.kb_backend.workers.conflict_resolution.models import (
    MODE_ALL_KEEP_EXISTING,
    MODE_INVALID,
    MODE_MERGE,
    MODE_SINGLE_KEEP_INCOMING,
    SUMMARY_FALLBACK_CHARS,
    BatchContext,
    Conflict,
    MergedBlock,
    ResolverStores,
)
from services.kb_backend.workers.conflict_resolution.orchestrator import resolve_batch
from services.kb_backend.workers.conflict_resolution.polling import (
    AWAITING_BATCH_SCAN_LIMIT,
    POLL_INTERVAL,
    conflict_resolve_loop,
    fetch_awaiting_batch_ids,
)

__all__ = [
    "AWAITING_BATCH_SCAN_LIMIT",
    "BatchContext",
    "Conflict",
    "MODE_ALL_KEEP_EXISTING",
    "MODE_INVALID",
    "MODE_MERGE",
    "MODE_SINGLE_KEEP_INCOMING",
    "MergedBlock",
    "POLL_INTERVAL",
    "ResolverStores",
    "SUMMARY_FALLBACK_CHARS",
    "conflict_resolve_loop",
    "fetch_awaiting_batch_ids",
    "resolve_batch",
]
