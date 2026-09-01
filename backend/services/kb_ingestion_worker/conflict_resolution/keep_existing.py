"""Keep-existing conflict resolution strategy."""
from __future__ import annotations

from services.kb_ingestion_worker.conflict_resolution.models import BatchContext, ResolverStores
from services.kb_ingestion_worker.conflict_resolution.state import (
    _close_out_conflicts,
    _conflict_ids,
    _mark_batch_resolved,
)
from services.kb_ingestion_worker.utils.stage_chain import StageChain


async def _apply_keep_existing(
    stores: ResolverStores,
    ctx: BatchContext,
    stage_record: StageChain,
) -> None:
    await _close_out_conflicts(stores.postgres, _conflict_ids(ctx.conflicts), stage_record)
    await _mark_batch_resolved(stores, ctx, stage_record)
