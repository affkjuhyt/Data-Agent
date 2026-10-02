"""Workflow Memory — doc §3.7 (STM_LTM_Memory_Architecture.docx).

The LTM layer specific to a data task platform, on top of the generic
conversational memory in memory_middleware.py:

  * Workflow Memory — Episodic Memory for data execution context. Written
    after a data tool (text_to_sql, text_to_pandas, ...) finishes; retrieved
    before the planner decides how to tackle a new data task, so the agent
    can reuse what worked before instead of re-planning from scratch.
    PostgreSQL holds structured metadata, Qdrant (collection
    "workflow_memory") holds the task_description embedding.

  * Schema Memory (doc §3.7) was dropped (migration 9d4f2b7c1e3a): the
    text_to_sql handler takes its schema from the Schema Catalog
    (schema_catalog.py) or live introspection instead.
"""
import logging
import math
import os
from datetime import datetime, timezone
from typing import Any, List, Optional
from uuid import UUID, uuid4

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)
from sqlalchemy import select

from libs.basemodel.services_databaseconnector.postgres_orm.workflow_orm import (
    WorkflowMemoryORM,
)
from libs.database_connector.postgres_connector import client
from libs.shared.embeddings import aembed_one

logger = logging.getLogger(__name__)

_LITELLM_BASE  = os.environ.get("LITELLM_BASE_URL", "http://localhost:8500")
_LITELLM_KEY   = os.environ.get("LITELLM_API_KEY",  "sk-dev")
_QDRANT_URL    = os.environ.get("QDRANT_URL",        "http://localhost:6333")
_EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "1536"))
_COLLECTION    = "workflow_memory"

# doc §IV — "Top-3, rank theo relevance × recency × inverse(duration)".
_RECENCY_HALFLIFE_DAYS = 30
_DURATION_NORMALIZER_MS = 5000  # duration_ms at which inverse_duration = 0.5 (half-life, same shape as recency)

_qdrant: Optional[AsyncQdrantClient] = None


async def init_workflow_qdrant() -> None:
    global _qdrant
    _qdrant = AsyncQdrantClient(url=_QDRANT_URL, check_compatibility=False)
    collections = await _qdrant.get_collections()
    names = [c.name for c in collections.collections]
    if _COLLECTION not in names:
        await _qdrant.create_collection(
            _COLLECTION,
            vectors_config=VectorParams(size=_EMBEDDING_DIM, distance=Distance.COSINE),
        )
        logger.info("qdrant | collection '%s' created (dim=%d)", _COLLECTION, _EMBEDDING_DIM)
    else:
        logger.info("qdrant | collection '%s' ready", _COLLECTION)


async def close_workflow_qdrant() -> None:
    global _qdrant
    if _qdrant:
        await _qdrant.close()
        _qdrant = None
        logger.info("qdrant | '%s' closed", _COLLECTION)


async def _embed(text: str) -> Optional[list[Any]]:
    try:
        return await aembed_one(text, base_url=_LITELLM_BASE, api_key=_LITELLM_KEY)
    except Exception as e:
        logger.warning("workflow_memory | embed failed, point will be PG-only: %s", e)
        return None


# ─── Workflow Memory ────────────────────────────────────────────────────────

async def record_workflow_outcome(
    tenant_id: str,
    agent_id: str,
    task_type: str,
    task_description: str,
    param_snapshot: dict[str, Any],
    outcome: str,
    duration_ms: Optional[int] = None,
) -> None:
    """Fire-and-forget — called from the request's `finally` block, same
    pattern as request_memory_extraction(), so a slow embed call never delays the
    response already streamed to the user."""
    if not client.is_connected() or not task_description.strip():
        return
    try:
        row_id = uuid4()
        point_id = str(row_id)
        vector = await _embed(task_description)

        async with client.get_client(tenant_id=tenant_id) as session:
            session.add(WorkflowMemoryORM(
                id=row_id,
                tenant_id=UUID(tenant_id),
                agent_id=UUID(agent_id),
                task_type=task_type,
                task_description=task_description,
                param_snapshot=param_snapshot,
                outcome=outcome,
                duration_ms=duration_ms,
                qdrant_point_id=point_id if vector else None,
            ))
            await session.commit()

        if vector and _qdrant:
            await _qdrant.upsert(_COLLECTION, points=[PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "tenant_id": tenant_id,
                    "agent_id":  agent_id,
                    "task_type": task_type,
                    "outcome":   outcome,
                    "pg_id":     point_id,
                },
            )])
        logger.info("workflow_memory | recorded task_type=%s outcome=%s agent=%s", task_type, outcome, agent_id)
    except Exception as e:
        logger.warning("workflow_memory | record failed: %s", e)


def _workflow_score(row: dict[str, Any], relevance: float) -> float:
    """doc §IV: relevance × recency × inverse(duration)."""
    now = datetime.now(timezone.utc)
    created_at = row.get("created_at")
    if created_at:
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (now - created_at).total_seconds() / 86400)
    else:
        age_days = _RECENCY_HALFLIFE_DAYS
    recency = math.exp(-age_days / _RECENCY_HALFLIFE_DAYS)

    duration_ms = row.get("duration_ms")
    inverse_duration = _DURATION_NORMALIZER_MS / (duration_ms + _DURATION_NORMALIZER_MS) if duration_ms else 1.0

    return relevance * recency * inverse_duration


async def retrieve_workflow_memory(
    tenant_id: str,
    agent_id: str,
    task_type: str,
    query: str,
    limit: int = 3,
) -> List[dict[str, Any]]:
    """doc §IV: retrieve BEFORE the agent plans a data task. Semantic search
    filtered by task_type + outcome=success, ranked by relevance × recency ×
    inverse(duration)."""
    if not client.is_connected() or not _qdrant:
        return []
    try:
        vector = await _embed(query)
        if not vector:
            return []

        hits = await _qdrant.search(
            _COLLECTION,
            query_vector=vector,
            query_filter=Filter(must=[
                FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
                FieldCondition(key="agent_id",  match=MatchValue(value=agent_id)),
                FieldCondition(key="task_type", match=MatchValue(value=task_type)),
                FieldCondition(key="outcome",   match=MatchValue(value="success")),
            ]),
            score_threshold=0.7,
            limit=limit * 3,
        )
        if not hits:
            return []

        pg_ids = [h.payload["pg_id"] for h in hits]
        relevance_by_id = {h.payload["pg_id"]: h.score for h in hits}

        async with client.get_client(tenant_id=tenant_id) as session:
            rows = (await session.execute(
                select(
                    WorkflowMemoryORM.qdrant_point_id, WorkflowMemoryORM.task_description,
                    WorkflowMemoryORM.param_snapshot, WorkflowMemoryORM.outcome,
                    WorkflowMemoryORM.duration_ms, WorkflowMemoryORM.created_at,
                )
                .where(WorkflowMemoryORM.qdrant_point_id.in_(pg_ids))
            )).fetchall()

        scored = []
        for r in rows:
            row = r._asdict()
            relevance = relevance_by_id.get(row["qdrant_point_id"], 0.0)
            scored.append((_workflow_score(row, relevance), row))
        scored.sort(key=lambda x: x[0], reverse=True)

        memories = [row for _, row in scored[:limit]]
        logger.info("workflow_memory | retrieved %d for task_type=%s agent=%s", len(memories), task_type, agent_id)
        return memories
    except Exception as e:
        logger.warning("workflow_memory | retrieve failed: %s", e)
        return []
