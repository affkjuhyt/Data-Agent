"""Polling loop for conflict batches awaiting resolution."""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from libs.basemodel.services_databaseconnector.postgres_model import ConflictStatus
from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import KBConflictBatchORM
from libs.database_connector.model_connector import ModelServiceClient
from libs.database_connector.postgres_connector import PostgresClient
from libs.database_connector.qdrant_connector import QdrantClient as QdrantService
from services.kb_conflict_resolution.libs.orchestrator import resolve_batch
from libs.ingestion import config as cfg
from libs.ingestion.graph_store import GraphStore


POLL_INTERVAL = int(cfg.__dict__.get("CONFLICT_POLL_INTERVAL", 30))
AWAITING_BATCH_SCAN_LIMIT = 50


async def fetch_awaiting_batch_ids(postgres: PostgresClient) -> list[str]:
    """Return batch ids whose user-selected resolution is ready to execute."""
    async with postgres.get_client() as session:
        result = await session.execute(
            select(KBConflictBatchORM.batch_id)
            .where(KBConflictBatchORM.status == ConflictStatus.AWAITING.value)
            .where(KBConflictBatchORM.is_deleted.is_(False))
            .order_by(KBConflictBatchORM.created_at)
            .limit(AWAITING_BATCH_SCAN_LIMIT)
        )
        return [str(batch_id) for batch_id in result.scalars().all()]


async def conflict_resolve_loop(
    postgres: PostgresClient,
    qdrant_svc: QdrantService,
    graph: GraphStore,
    model: ModelServiceClient,
    mongo,
) -> None:
    """Poll for AWAITING conflict batches and resolve them serially."""
    database = mongo.get_client()
    while True:
        try:
            batch_ids = await fetch_awaiting_batch_ids(postgres)
            for batch_id in batch_ids:
                await resolve_batch(batch_id, postgres, qdrant_svc, graph, model, database)
        except Exception:
            pass

        await asyncio.sleep(POLL_INTERVAL)
