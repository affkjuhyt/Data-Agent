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

from neo4j import AsyncDriver
from qdrant_client import models as qmodels
from qdrant_client.async_qdrant_client import AsyncQdrantClient
from sqlalchemy import select, update as sa_update

from basemodel.services_databaseconnector.postgres_model import (
    ConflictResolution,
    ConflictStatus,
)
from basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBConflictORM,
    KBTextBlockORM,
    KBTextBlockVersionORM,
)
from services.database_connector.model_connector import ModelServiceClient
from services.database_connector.postgres_connector import PostgresClient
from services.database_connector.qdrant_connector import QdrantClient as QdrantService
from services.database_connector.neo4j_connector import Neo4jClient

from gold import _commit_chunk_atomic
import llm_client
import config as cfg

log = logging.getLogger(__name__)

POLL_INTERVAL = int(cfg.__dict__.get("CONFLICT_POLL_INTERVAL", 30))  # seconds

_MERGE_SYSTEM = """\
You are a knowledge editor. Two text chunks contain related but conflicting information.
Merge them into a single coherent chunk that preserves the most important facts from both.
If a resolution instruction is provided, follow it strictly.
Return only the merged text — no commentary, no headers."""


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


async def _mark_resolved(
    postgres: PostgresClient,
    conflict_id: _uuid.UUID,
) -> None:
    # resolved_by was already set to the requesting user when the backend queued the
    # conflict as AWAITING — preserve it; only flip status and stamp completion time.
    async with postgres.get_client() as session:
        await session.execute(
            sa_update(KBConflictORM)
            .where(KBConflictORM.conflict_id == conflict_id)
            .values(
                status=ConflictStatus.RESOLVED.value,
                resolved_at=datetime.datetime.now(datetime.timezone.utc),
            )
        )
        await session.commit()


# ── Resolution handlers ───────────────────────────────────────────────────────
# Chunk-level conflicts no longer carry graph data: entities and relationships
# are extracted ONCE per document during gold promotion, so resolving a chunk
# conflict only touches Postgres + the Qdrant chunk collection. The document
# graph and doc-summary point are untouched (they reflect the doc summary, which
# a single chunk swap does not change materially).

async def _handle_keep_existing(conflict: KBConflictORM, **_) -> None:
    """Incoming was already skipped at ingestion time — nothing to do."""
    log.info("conflict_resolver | keep_existing conflict_id=%s — no-op", conflict.conflict_id)


async def _handle_keep_incoming(
    conflict: KBConflictORM,
    postgres: PostgresClient,
    qdrant_client: AsyncQdrantClient,
    neo4j: AsyncDriver,
    model: ModelServiceClient,
) -> None:
    snap           = conflict.incoming_snapshot or {}
    existing_snap  = conflict.existing_snapshot or {}
    tenant_id      = str(conflict.tenant_id)
    embed_model_id = snap.get("embed_model_id") or cfg.EMBED_MODEL_ID
    knowledge_base = snap.get("knowledge_base", "")

    # Soft-delete the existing chunk so we don't leave duplicate vectors in
    # Qdrant / stale rows in Postgres.
    existing_block_id = existing_snap.get("block_id", "")
    if existing_block_id:
        await qdrant_client.set_payload(
            collection_name=cfg.QDRANT_COLLECTION,
            payload={"is_deleted": True},
            points=[existing_block_id],
        )
        async with postgres.get_client() as session:
            await session.execute(
                sa_update(KBTextBlockORM)
                .where(KBTextBlockORM.block_id == _uuid.UUID(existing_block_id))
                .values(is_deleted=True)
            )
            await session.commit()
        log.info(
            "conflict_resolver | keep_incoming: soft-deleted existing block_id=%s",
            existing_block_id,
        )

    await _commit_chunk_atomic(
        postgres=postgres,
        qdrant=qdrant_client,
        tenant_id=tenant_id,
        data_id=snap["data_id"],
        block_index=snap["block_index"],
        content=snap["content"],
        table_involved=snap.get("table_involved", False),
        table=snap.get("table"),
        payload_meta={
            "summary":            snap["summary"],
            "embedding_model_id": embed_model_id,
            "knowledge_base":     knowledge_base,
        },
        embedding=snap["embedding"],
        summary=snap["summary"],
        approved_by=snap.get("approved_by", "system"),
        embed_model_id=embed_model_id,
        knowledge_base=knowledge_base,
    )
    log.info("conflict_resolver | keep_incoming committed conflict_id=%s", conflict.conflict_id)


async def _handle_merge(
    conflict: KBConflictORM,
    postgres: PostgresClient,
    qdrant_client: AsyncQdrantClient,
    neo4j: AsyncDriver,
    model: ModelServiceClient,
) -> None:
    existing_snap = conflict.existing_snapshot or {}
    incoming_snap = conflict.incoming_snapshot or {}
    block_id      = existing_snap.get("block_id", "")
    tenant_id     = str(conflict.tenant_id)
    # Conflict detection is KB-scoped, so existing and incoming share a knowledge base.
    embed_model_id = incoming_snap.get("embed_model_id") or cfg.EMBED_MODEL_ID
    knowledge_base = incoming_snap.get("knowledge_base", "")

    fetched = await _fetch_existing_content(postgres, block_id, tenant_id)
    if fetched is None:
        log.warning("conflict_resolver | merge: existing block_id=%s not found — falling back to keep_incoming", block_id)
        await _handle_keep_incoming(conflict, postgres=postgres, qdrant_client=qdrant_client, neo4j=neo4j, model=model)
        return

    existing_content, current_version = fetched
    incoming_content = incoming_snap.get("content", "")
    instruction      = conflict.resolution_instruction or ""

    user_msg = (
        f"Existing chunk:\n{existing_content}\n\n"
        f"Incoming chunk:\n{incoming_content}"
    )
    if instruction:
        user_msg += f"\n\nResolution instruction: {instruction}"

    merged_content = await llm_client.chat_plain(
        messages=[
            {"role": "system", "content": _MERGE_SYSTEM},
            {"role": "user",   "content": user_msg},
        ],
        max_tokens=1024,
        temperature=0.2,
    )

    # Re-embed merged content for the updated Qdrant point
    new_vecs    = await model.embed([merged_content])
    new_embedding = new_vecs[0]

    # Update Qdrant point in-place (same block_id, new vector + summary). The
    # point stays attached to the EXISTING document, so preserve its data_id from
    # the live point — the incoming snapshot may belong to a different document.
    existing_data_id = ""
    carried_entities: dict = {}
    try:
        live = await qdrant_client.retrieve(
            collection_name=cfg.QDRANT_COLLECTION, ids=[block_id], with_payload=True,
        )
        if live:
            live_payload = live[0].payload or {}
            existing_data_id = live_payload.get("data_id", "")
            # Preserve the document-level entity back-stamp (entities + ent_* typed
            # fields). A PointStruct upsert replaces the whole payload, so without
            # this an in-place merge would wipe the chunk's entity filter.
            carried_entities = {
                k: v for k, v in live_payload.items()
                if k == "entities" or k.startswith("ent_")
            }
    except Exception as exc:
        log.warning("conflict_resolver | merge: could not read live point %s: %s", block_id, exc)

    existing_payload = {
        "tenant_id":          tenant_id,
        "data_id":            existing_data_id,
        "block_id":           block_id,
        "summary":            merged_content[:200],
        **carried_entities,
        "embedding_model_id": embed_model_id,
        "knowledge_base":     knowledge_base,
        "is_deleted":         False,
    }
    await qdrant_client.upsert(
        collection_name=cfg.QDRANT_COLLECTION,
        points=[qmodels.PointStruct(
            id=block_id,
            vector=new_embedding,
            payload=existing_payload,
        )],
    )

    # Create new active Postgres version (deactivate old first)
    async with postgres.get_client() as session:
        await session.execute(
            sa_update(KBTextBlockVersionORM)
            .where(KBTextBlockVersionORM.block_id == _uuid.UUID(block_id))
            .where(KBTextBlockVersionORM.is_active.is_(True))
            .values(is_active=False)
        )
        new_ver = KBTextBlockVersionORM(
            block_id=_uuid.UUID(block_id),
            version_number=current_version + 1,
            content=merged_content,
            created_by=conflict.resolved_by or "system",
            table_involved=False,
            payload=existing_payload,
            is_active=True,
        )
        session.add(new_ver)
        await session.commit()

    log.info("conflict_resolver | merge complete conflict_id=%s block_id=%s v%d→v%d",
             conflict.conflict_id, block_id, current_version, current_version + 1)


async def _handle_delete(
    conflict: KBConflictORM,
    postgres: PostgresClient,
    qdrant_client: AsyncQdrantClient,
    neo4j: AsyncDriver,
    model: ModelServiceClient,
) -> None:
    """Explicitly delete the existing chunk and commit the incoming one.

    keep_incoming and delete are now identical in data operations — both
    soft-delete existing then commit incoming. _handle_keep_incoming owns
    that logic; this handler is a named alias with an explanatory log line.
    """
    log.info("conflict_resolver | delete: replacing existing chunk conflict_id=%s", conflict.conflict_id)
    await _handle_keep_incoming(conflict, postgres=postgres, qdrant_client=qdrant_client, neo4j=neo4j, model=model)


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
    neo4j_svc: Neo4jClient,
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

    try:
        await handler(
            conflict,
            postgres=postgres,
            qdrant_client=qdrant_svc.get_client(),
            neo4j=neo4j_svc.get_client(),
            model=model,
        )
        await _mark_resolved(postgres, conflict.conflict_id)
    except Exception as exc:
        log.error(
            "conflict_resolver | resolution failed conflict_id=%s method=%s: %s",
            conflict.conflict_id, method, exc,
        )


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
    neo4j_svc: Neo4jClient,
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
                    await _resolve_conflict(conflict, postgres, qdrant_svc, neo4j_svc, model)
        except Exception as exc:
            log.error("conflict_resolver | poll error: %s", exc)

        await asyncio.sleep(POLL_INTERVAL)
