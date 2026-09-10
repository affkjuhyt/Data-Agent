"""Backward-compatible facade for conflict resolution.

The implementation is organized under ``conflict_resolution`` by responsibility.
Keep this module so existing worker/bootstrap imports continue to work unchanged.
"""

from services.kb_conflict_resolution.libs.keep_existing import _apply_keep_existing
from services.kb_conflict_resolution.libs.keep_incoming import (
    _apply_keep_incoming,
    _commit_incoming_chunk,
    _rollback_mark_payload_committed,
    _rollback_soft_delete_block,
    _set_block_deleted,
    _soft_delete_block,
)
from services.kb_conflict_resolution.libs.merge import (
    _apply_merge,
    _build_merged_block,
    _fetch_active_block_table,
    _merge_contents,
    _merge_into_working_content,
    _publish_merged_block,
    _reopen_conflict_after_recheck,
    _revert_merged_version,
    _rollback_published_merged_block,
    _summarize_working_content,
    _write_merged_version,
)
from services.kb_conflict_resolution.libs.models import (
    KEEP_EXISTING as _KEEP_EXISTING,
    KEEP_INCOMING as _KEEP_INCOMING,
    MERGE as _MERGE,
    MODE_ALL_KEEP_EXISTING,
    MODE_INVALID,
    MODE_MERGE,
    MODE_SINGLE_KEEP_INCOMING,
    RESOLVED as _RESOLVED,
    SUMMARY_FALLBACK_CHARS,
    BatchContext,
    Conflict,
    MergedBlock,
    ResolverStores,
)
from services.kb_conflict_resolution.libs.orchestrator import resolve_batch
from services.kb_conflict_resolution.libs.polling import (
    AWAITING_BATCH_SCAN_LIMIT,
    POLL_INTERVAL,
    conflict_resolve_loop,
    fetch_awaiting_batch_ids,
)
from services.kb_conflict_resolution.libs.state import (
    _batch_has_open_conflicts,
    _claim_batch,
    _classify_batch_resolution,
    _close_out_conflicts,
    _conflict_ids,
    _fetch_block_point,
    _ids_with_method,
    _load_batch_anchor,
    _load_batch_conflicts,
    _mark_batch_resolved,
    _return_batch_to_user,
    _rollback_close_out_conflicts,
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
