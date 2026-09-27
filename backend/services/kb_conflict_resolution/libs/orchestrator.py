"""Batch-level conflict resolution orchestration."""
from __future__ import annotations

from libs.basemodel.services_databaseconnector.postgres_model import ConflictStatus
from libs.database_connector.model_connector import ModelServiceClient
from libs.database_connector.postgres_connector import PostgresClient, set_current_tenant
from libs.database_connector.qdrant_connector import QdrantClient as QdrantService
from services.kb_conflict_resolution.libs.keep_existing import _apply_keep_existing
from services.kb_conflict_resolution.libs.keep_incoming import _apply_keep_incoming
from services.kb_conflict_resolution.libs.merge import _apply_merge
from services.kb_conflict_resolution.libs.models import (
    MODE_ALL_KEEP_EXISTING,
    MODE_MERGE,
    MODE_SINGLE_KEEP_INCOMING,
    BatchContext,
    ResolverStores,
)
from services.kb_conflict_resolution.libs.state import (
    _claim_batch,
    _classify_batch_resolution,
    _load_batch_anchor,
    _load_batch_conflicts,
    _return_batch_to_user,
)
from libs.ingestion.gold import set_conflict_batch_status
from libs.ingestion import conflict_store
from libs.ingestion.graph_store import GraphStore
from libs.ingestion.log_writer import IngestionLogger
from libs.ingestion.stage_chain import StageChain


async def resolve_batch(
    batch_id: str,
    postgres: PostgresClient,
    qdrant_svc: QdrantService,
    graph: GraphStore,
    model: ModelServiceClient,
    database,
) -> None:
    """Execute one batch's user-chosen resolution as a whole."""
    logger = IngestionLogger(db=database)
    stage_record = StageChain()

    if not await _claim_batch(postgres, batch_id):
        return
    stage_record.push("batch_claimed")

    stores = ResolverStores(
        postgres=postgres,
        qdrant=qdrant_svc.get_client(),
        graph=graph,
        model=model,
        mongo=database,
    )
    try:
        batch_document = await conflict_store.load_resolution_batch(database, batch_id)
        if batch_document is None:
            await logger.logging(
                f"batch_id={batch_id} has no working document - returning to PENDING",
                level="WARNING",
            )
            await set_conflict_batch_status(postgres, batch_id, ConflictStatus.PENDING.value)
            return

        conflicts, resolved_by = await _load_batch_conflicts(postgres, batch_id)
        ctx = BatchContext(
            batch_id=batch_id,
            tenant_id=batch_document.get("tenant_id", ""),
            knowledge_base=batch_document.get("knowledge_base", ""),
            existing_block_id=await _load_batch_anchor(postgres, batch_id) or "",
            resolved_by=resolved_by,
            conflicts=conflicts,
        )
        # See kb_promote_silver/libs/handler.py's identical fix — RLS-scoped
        # reads (postgres.read(ReadJoinRequest(tenant_id=...))) rely on this
        # ContextVar, which nothing else in a Kafka consumer ever sets. Known
        # only once ctx.tenant_id is loaded, so set from here on.
        if ctx.tenant_id:
            set_current_tenant(ctx.tenant_id)
        stage_record.push("batch_loaded")

        mode = _classify_batch_resolution(ctx.conflicts)
        if mode == MODE_ALL_KEEP_EXISTING:
            await _apply_keep_existing(stores, ctx, stage_record)
        elif mode == MODE_SINGLE_KEEP_INCOMING:
            await _apply_keep_incoming(stores, ctx, stage_record)
        elif mode == MODE_MERGE:
            await _apply_merge(stores, ctx, stage_record)
        else:
            await logger.logging(
                f"batch_id={batch_id} invalid resolution selection - returning to PENDING",
                level="WARNING",
            )
            await _return_batch_to_user(postgres, batch_id)
            return
        stage_record.push("resolution_applied")
    except Exception as exc:
        await stage_record.unwind()
        await logger.logging(
            f"batch resolution failed batch_id={batch_id}: {exc} - reverting to AWAITING",
            level="ERROR",
        )
        try:
            await set_conflict_batch_status(postgres, batch_id, ConflictStatus.AWAITING.value)
        except Exception:
            await logger.logging(
                f"batch_id={batch_id} STRANDED as {ConflictStatus.PENDING.value} - "
                "revert to AWAITING failed",
                level="CRITICAL",
            )
        return

    await logger.logging(f"batch_id={batch_id} resolved (mode={mode})")
