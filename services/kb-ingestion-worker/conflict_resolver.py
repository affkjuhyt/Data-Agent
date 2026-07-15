"""
Conflict Resolution Loop
========================
Polls KBConflict for AWAITING records and executes the user-chosen resolution.

Status flow:
  PENDING   — conflict saved during ingestion; user reviews in UI
  AWAITING  — user has set resolution_method (and optionally resolution_instruction);
              the resolver picks it up and executes
  RESOLVED  — action completed; record is archived

Resolution methods:
  keep_existing  — incoming was already skipped; no further action needed
  keep_incoming  — commit the incoming chunk using the stored snapshot
  merge          — LLM-merge existing + incoming content; update existing record
  delete         — soft-delete existing chunk; then commit incoming
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import uuid as _uuid

from qdrant_client import models as qmodels
from qdrant_client.async_qdrant_client import AsyncQdrantClient
from sqlalchemy import select, update as sa_update

from libs.basemodel.services_databaseconnector.postgres_model import (
    ConflictResolution,
    ConflictStatus,
)
from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBConflictORM,
    KBTextBlockORM,
    KBTextBlockVersionORM,
)
from libs.database_connector.model_connector import ModelServiceClient
from libs.database_connector.postgres_connector import PostgresClient
from libs.database_connector.qdrant_connector import QdrantClient as QdrantService

from gold import (
    _commit_chunk_atomic,
    canonicalize_chunk_entities,
    remap_chunk_relationships,
)
from utils import config as cfg
from utils import llm_client
from utils.entity_types import entities_by_type_fields
from utils.graph_store import GraphStore
from utils.prompts import MERGE_SYSTEM

log = logging.getLogger(__name__)

POLL_INTERVAL = int(cfg.__dict__.get("CONFLICT_POLL_INTERVAL", 30))  # seconds


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _fetch_existing_content(
    postgres: PostgresClient,
    block_id: str,
    tenant_id: str,
) -> tuple[str, int] | None:
    """Return (content, version_number) of the active version for block_id, or None."""
    async with postgres.get_client() as session:
        result = await session.execute(
            select(KBTextBlockVersionORM)
            .join(KBTextBlockORM, KBTextBlockVersionORM.block_id == KBTextBlockORM.block_id)
            .where(KBTextBlockORM.block_id == _uuid.UUID(block_id))
            .where(KBTextBlockVersionORM.is_active.is_(True))
            .where(KBTextBlockVersionORM.is_deleted.is_(False))
            .limit(1)
        )
        row = result.scalars().first()
    if row is None:
        return None
    return row.content, row.version_number


async def _claim_conflict(
    postgres: PostgresClient,
    conflict_id: _uuid.UUID,
) -> bool:
    """Atomically claim an AWAITING conflict by flipping it to RESOLVED.

    The Kafka-driven handler and the 30s poll loop can both pick up the same
    AWAITING conflict; without this they would each run a resolution handler and
    commit the incoming chunk twice (duplicate block rows + Qdrant points). The
    conditional UPDATE (status == AWAITING) succeeds for exactly one caller — the
    loser sees rowcount 0 and backs off. resolved_by is preserved (the backend set
    it to the requesting user when queuing the conflict).
    """
    async with postgres.get_client() as session:
        try:
            result = await session.execute(
                sa_update(KBConflictORM)
                .where(KBConflictORM.conflict_id == conflict_id)
                .where(KBConflictORM.status == ConflictStatus.AWAITING.value)
                .values(
                    status=ConflictStatus.RESOLVED.value,
                    resolved_at=datetime.datetime.now(datetime.timezone.utc),
                )
            )
            await session.commit()
            return (result.rowcount or 0) == 1
        except Exception as exc:
            await session.rollback()
            log.error("conflict_resolver | claim failed conflict_id=%s: %s", conflict_id, exc)
            return False


async def _revert_to_awaiting(
    postgres: PostgresClient,
    conflict_id: _uuid.UUID,
) -> None:
    """Return a claimed conflict to AWAITING after a failed handler, so it is retried
    (by the next poll tick) rather than left silently marked RESOLVED with no effect."""
    async with postgres.get_client() as session:
        try:
            await session.execute(
                sa_update(KBConflictORM)
                .where(KBConflictORM.conflict_id == conflict_id)
                .values(status=ConflictStatus.AWAITING.value, resolved_at=None)
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            log.error("conflict_resolver | revert failed conflict_id=%s: %s", conflict_id, exc)


# ── Resolution handlers ───────────────────────────────────────────────────────
async def _handle_keep_existing(conflict: KBConflictORM, **_) -> None:
    """Incoming was already skipped at ingestion time — nothing to do."""
    log.info("conflict_resolver | keep_existing conflict_id=%s — no-op", conflict.conflict_id)


async def _handle_keep_incoming(
    conflict: KBConflictORM,
    postgres: PostgresClient,
    qdrant_client: AsyncQdrantClient,
    graph: GraphStore,
    model: ModelServiceClient,
) -> None:
    incoming_snapshot = conflict.incoming_snapshot or {}
    existing_snapshot = conflict.existing_snapshot or {}
    tenant_id      = str(conflict.tenant_id)
    embed_model_id = incoming_snapshot.get("embed_model_id") or cfg.EMBED_MODEL_ID
    knowledge_base = incoming_snapshot.get("knowledge_base", "")
    data_id        = incoming_snapshot["data_id"]

    # Soft-delete the existing chunk (Qdrant + Postgres) and drop its graph edges.
    existing_block_id = existing_snapshot.get("block_id", "")
    if existing_block_id:
        await qdrant_client.set_payload(
            collection_name=cfg.QDRANT_COLLECTION,
            payload={"is_deleted": True},
            points=[existing_block_id],
        )
        async with postgres.get_client() as session:
            try:
                await session.execute(
                    sa_update(KBTextBlockORM)
                    .where(KBTextBlockORM.block_id == _uuid.UUID(existing_block_id))
                    .values(is_deleted=True)
                )
                await session.commit()
            except Exception:
                await session.rollback()
                raise
        await graph.remove_chunk(
            tenant_id=tenant_id, knowledge_base=knowledge_base, block_id=existing_block_id,
        )
        log.info("conflict_resolver | keep_incoming: soft-deleted existing block_id=%s",
                 existing_block_id)

    # Canonicalize the incoming chunk's silver entities exactly as the gold loop
    # would — so the committed chunk carries its entity payload and graph edges.
    (canonicals, type_by_canonical, description_by_canonical,
     display_by_canonical, raw_to_canonical) = await canonicalize_chunk_entities(
        qdrant_client, postgres, model, tenant_id, incoming_snapshot.get("entities") or [],
        knowledge_base=knowledge_base, embed_model_id=embed_model_id,
    )

    block_id = await _commit_chunk_atomic(
        postgres=postgres,
        qdrant=qdrant_client,
        tenant_id=tenant_id,
        data_id=data_id,
        block_index=incoming_snapshot["block_index"],
        content=incoming_snapshot["content"],
        table_involved=incoming_snapshot.get("table_involved", False),
        table=incoming_snapshot.get("table"),
        payload_meta={
            "summary":            incoming_snapshot["summary"],
            "entities":           canonicals,
            "embedding_model_id": embed_model_id,
            "knowledge_base":     knowledge_base,
        },
        embedding=incoming_snapshot["embedding"],
        summary=incoming_snapshot["summary"],
        approved_by=incoming_snapshot.get("approved_by", "system"),
        embed_model_id=embed_model_id,
        knowledge_base=knowledge_base,
        entities=canonicals,
        entity_type_fields=entities_by_type_fields(type_by_canonical, canonicals) if canonicals else None,
    )

    await graph.add_chunk(
        tenant_id=tenant_id,
        knowledge_base=knowledge_base,
        data_id=data_id,
        block_id=block_id,
        entities=[
            {"canonical": canonical, "display": display_by_canonical.get(canonical, canonical),
             "description": description_by_canonical.get(canonical, ""),
             "type": type_by_canonical.get(canonical, "concept")}
            for canonical in canonicals
        ],
        relationships=remap_chunk_relationships(incoming_snapshot.get("relationships"), raw_to_canonical),
        embed_model_id=embed_model_id,
    )

    log.info("conflict_resolver | keep_incoming committed conflict_id=%s (entities=%d)",
             conflict.conflict_id, len(canonicals))


async def _handle_merge(
    conflict: KBConflictORM,
    postgres: PostgresClient,
    qdrant_client: AsyncQdrantClient,
    graph: GraphStore,
    model: ModelServiceClient,
) -> None:
    existing_snapshot = conflict.existing_snapshot or {}
    incoming_snapshot = conflict.incoming_snapshot or {}
    block_id      = existing_snapshot.get("block_id", "")
    tenant_id     = str(conflict.tenant_id)
    # Conflict detection is KB-scoped, so existing and incoming share a knowledge base.
    embed_model_id = incoming_snapshot.get("embed_model_id") or cfg.EMBED_MODEL_ID
    knowledge_base = incoming_snapshot.get("knowledge_base", "")

    fetched = await _fetch_existing_content(postgres, block_id, tenant_id)
    if fetched is None:
        log.warning("conflict_resolver | merge: existing block_id=%s not found — falling back to keep_incoming", block_id)
        await _handle_keep_incoming(conflict, postgres=postgres, qdrant_client=qdrant_client, graph=graph, model=model)
        return

    existing_content, current_version = fetched
    incoming_content = incoming_snapshot.get("content", "")
    instruction      = conflict.resolution_instruction or ""

    user_msg = (
        f"Existing chunk:\n{existing_content}\n\n"
        f"Incoming chunk:\n{incoming_content}"
    )
    if instruction:
        user_msg += f"\n\nResolution instruction: {instruction}"

    merged_content = await llm_client.chat_plain(
        messages=[
            {"role": "system", "content": MERGE_SYSTEM},
            {"role": "user",   "content": user_msg},
        ],
        max_tokens=1024,
        temperature=0.2,
    )

    # Re-embed merged content for the updated Qdrant point
    new_embedding = (await model.embed([merged_content]))[0]

    # Update Qdrant point in-place (same block_id, new vector + summary). The
    # point stays attached to the EXISTING document, so preserve its data_id from
    # the live point — the incoming snapshot may belong to a different document.
    existing_data_id = ""
    carried_entity_fields: dict = {}
    try:
        live_points = await qdrant_client.retrieve(
            collection_name=cfg.QDRANT_COLLECTION, ids=[block_id], with_payload=True,
        )
        if live_points:
            live_payload = live_points[0].payload or {}
            existing_data_id = live_payload.get("data_id", "")
            # Preserve the per-chunk entity payload (entities + ent_* typed fields).
            # A PointStruct upsert replaces the whole payload, so without this an
            # in-place merge would wipe the chunk's entity filter.
            carried_entity_fields = {
                key: value for key, value in live_payload.items()
                if key == "entities" or key.startswith("ent_")
            }
    except Exception as exc:
        log.warning("conflict_resolver | merge: could not read live point %s: %s", block_id, exc)

    merged_payload = {
        "tenant_id":          tenant_id,
        "data_id":            existing_data_id,
        "block_id":           block_id,
        "summary":            merged_content[:200],
        **carried_entity_fields,
        "embedding_model_id": embed_model_id,
        "knowledge_base":     knowledge_base,
        "is_deleted":         False,
    }
    await qdrant_client.upsert(
        collection_name=cfg.QDRANT_COLLECTION,
        points=[qmodels.PointStruct(
            id=block_id,
            vector=new_embedding,
            payload=merged_payload,
        )],
    )

    # Create new active Postgres version (deactivate old first), rolled back together.
    async with postgres.get_client() as session:
        try:
            await session.execute(
                sa_update(KBTextBlockVersionORM)
                .where(KBTextBlockVersionORM.block_id == _uuid.UUID(block_id))
                .where(KBTextBlockVersionORM.is_active.is_(True))
                .values(is_active=False)
            )
            new_version_row = KBTextBlockVersionORM(
                block_id=_uuid.UUID(block_id),
                version_number=current_version + 1,
                content=merged_content,
                created_by=conflict.resolved_by or "system",
                table_involved=False,
                payload=merged_payload,
                is_active=True,
            )
            session.add(new_version_row)
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    log.info("conflict_resolver | merge complete conflict_id=%s block_id=%s v%d→v%d",
             conflict.conflict_id, block_id, current_version, current_version + 1)


async def _handle_delete(
    conflict: KBConflictORM,
    postgres: PostgresClient,
    qdrant_client: AsyncQdrantClient,
    graph: GraphStore,
    model: ModelServiceClient,
) -> None:
    """Explicitly delete the existing chunk and commit the incoming one — identical
    in data operations to keep_incoming, which owns that logic."""
    log.info("conflict_resolver | delete: replacing existing chunk conflict_id=%s", conflict.conflict_id)
    await _handle_keep_incoming(conflict, postgres=postgres, qdrant_client=qdrant_client, graph=graph, model=model)


# ── Dispatcher ────────────────────────────────────────────────────────────────

_HANDLERS = {
    ConflictResolution.KEEP_EXISTING.value: _handle_keep_existing,
    ConflictResolution.KEEP_INCOMING.value: _handle_keep_incoming,
    ConflictResolution.MERGE.value:         _handle_merge,
    ConflictResolution.DELETE.value:        _handle_delete,
}


async def _resolve_conflict(
    conflict: KBConflictORM,
    postgres: PostgresClient,
    qdrant_svc: QdrantService,
    graph: GraphStore,
    model: ModelServiceClient,
) -> None:
    method = conflict.resolution_method
    handler = _HANDLERS.get(method)
    if handler is None:
        log.warning(
            "conflict_resolver | unknown resolution_method=%r conflict_id=%s — skipping",
            method, conflict.conflict_id,
        )
        return

    # Claim before doing any work — only one of (Kafka handler, poll loop) wins, so
    # the incoming chunk is never committed twice.
    if not await _claim_conflict(postgres, conflict.conflict_id):
        log.info("conflict_resolver | conflict_id=%s already claimed/resolved — skipping",
                 conflict.conflict_id)
        return

    try:
        await handler(
            conflict,
            postgres=postgres,
            qdrant_client=qdrant_svc.get_client(),
            graph=graph,
            model=model,
        )
    except Exception as exc:
        log.error(
            "conflict_resolver | resolution failed conflict_id=%s method=%s: %s — reverting to AWAITING",
            conflict.conflict_id, method, exc,
        )
        await _revert_to_awaiting(postgres, conflict.conflict_id)


# ── Kafka-driven entry point ──────────────────────────────────────────────────

async def fetch_awaiting_conflict(
    postgres: PostgresClient,
    conflict_id: str,
) -> KBConflictORM | None:
    """Load a single AWAITING conflict by ID for Kafka-driven resolution.

    Returns None if the conflict doesn't exist, is already resolved, or was
    deleted — the Kafka handler should skip it in that case.
    """
    async with postgres.get_client() as session:
        result = await session.execute(
            select(KBConflictORM)
            .where(KBConflictORM.conflict_id == _uuid.UUID(conflict_id))
            .where(KBConflictORM.status == ConflictStatus.AWAITING.value)
            .where(KBConflictORM.is_deleted.is_(False))
            .limit(1)
        )
        return result.scalars().first()


# ── Polling loop ──────────────────────────────────────────────────────────────

async def conflict_resolve_loop(
    postgres: PostgresClient,
    qdrant_svc: QdrantService,
    graph: GraphStore,
    model: ModelServiceClient,
) -> None:
    """Background loop that polls for AWAITING conflicts and resolves them."""
    log.info("conflict_resolver | loop started (poll_interval=%ds)", POLL_INTERVAL)
    while True:
        try:
            async with postgres.get_client() as session:
                result = await session.execute(
                    select(KBConflictORM)
                    .where(KBConflictORM.status == ConflictStatus.AWAITING.value)
                    .where(KBConflictORM.is_deleted.is_(False))
                    .where(KBConflictORM.resolution_method.isnot(None))
                    .order_by(KBConflictORM.detected_at)
                    .limit(50)
                )
                pending = result.scalars().all()

            if pending:
                log.info("conflict_resolver | %d awaiting conflict(s) found", len(pending))
                for conflict in pending:
                    await _resolve_conflict(conflict, postgres, qdrant_svc, graph, model)
        except Exception as exc:
            log.error("conflict_resolver | poll error: %s", exc)

        await asyncio.sleep(POLL_INTERVAL)
