import math
import os
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, List, Optional, cast
from uuid import UUID, uuid4

import httpx
from croniter import croniter
from sqlalchemy import select, func, update
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, VectorParams, Filter, FieldCondition, MatchValue

from libs.database_connector.postgres_connector import client
from libs.basemodel.services_databaseconnector.postgres_orm.workflow_orm import (
    AgentMemoriesORM, MemoryPolicyORM, EpisodicMemoryORM, FutureMemoryORM,
)
from kafka_producer import produce_event

logger = logging.getLogger(__name__)

_DEV_AGENT_ID  = os.getenv("DEV_AGENT_ID",  "00000000-0000-0000-0000-000000000030")
_DEV_TENANT_ID = os.getenv("DEV_TENANT_ID", "00000000-0000-0000-0000-000000000001")

_LITELLM_BASE  = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
_LITELLM_KEY   = os.environ.get("LITELLM_API_KEY",  "sk-dev")
_QDRANT_URL    = os.environ.get("QDRANT_URL",        "http://localhost:6333")
_EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "1024"))
_COLLECTION    = "agent_memories"

# Retrieval Score = α·recency + β·importance + γ·relevance (doc §IV).
# user_pref facts change rarely → weigh importance higher; everything else
# uses the doc's recommended defaults.
_RETRIEVAL_WEIGHTS = {
    "user_pref": {"alpha": 0.2, "beta": 0.5, "gamma": 0.3},
    "default":   {"alpha": 0.3, "beta": 0.3, "gamma": 0.4},
}
_RECENCY_HALFLIFE_DAYS = 30
_EPISODIC_LOOKBACK_DAYS = 30

_qdrant: Optional[AsyncQdrantClient] = None


async def init_qdrant() -> None:
    global _qdrant
    _qdrant = AsyncQdrantClient(url=_QDRANT_URL)
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


async def close_qdrant() -> None:
    global _qdrant
    if _qdrant:
        await _qdrant.close()
        _qdrant = None
        logger.info("qdrant | closed")


async def _embed(text: str) -> Optional[list[Any]]:
    try:
        async with httpx.AsyncClient(timeout=30) as client_:
            resp = await client_.post(
                f"{_LITELLM_BASE}/v1/embeddings",
                headers={"Authorization": f"Bearer {_LITELLM_KEY}"},
                json={"model": "embedder", "input": text},
            )
            resp.raise_for_status()
            return cast(list[Any], resp.json()["data"][0]["embedding"])
    except Exception as e:
        logger.warning("embed failed — falling back to full-text search: %s", e)
        return None


def _rrf(vec_ids: list[Any], fts_ids: list[Any], k: int = 60) -> list[Any]:
    """Reciprocal Rank Fusion — combine vector and full-text search candidate sets for recall."""
    scores: dict[str, Any] = {}
    for rank, id_ in enumerate(vec_ids):
        scores[id_] = scores.get(id_, 0.0) + 1 / (k + rank + 1)
    for rank, id_ in enumerate(fts_ids):
        scores[id_] = scores.get(id_, 0.0) + 1 / (k + rank + 1)
    return sorted(scores, key=lambda x: scores[x], reverse=True)


def _retrieval_score(row: dict[str, Any], relevance: float, weights: dict[str, Any]) -> float:
    """score = α·recency + β·importance + γ·relevance (doc §IV)."""
    now = datetime.now(timezone.utc)
    touched_at = row.get("updated_at")
    if touched_at:
        if touched_at.tzinfo is None:
            touched_at = touched_at.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (now - touched_at).total_seconds() / 86400)
    else:
        age_days = _RECENCY_HALFLIFE_DAYS  # unknown age -> neutral recency
    recency = math.exp(-age_days / _RECENCY_HALFLIFE_DAYS)
    importance = row.get("importance") if row.get("importance") is not None else 0.5
    return cast(
        float,
        weights["alpha"] * recency + weights["beta"] * importance + weights["gamma"] * relevance,
    )


async def retrieve_memories(
    tenant_id: str,
    agent_id: str,
    user_ref: str,
    query: str,
    limit: int = 5,
    domain: Optional[str] = None,
) -> List[dict[str, Any]]:
    """Semantic LTM retrieval: RRF merges vector+FTS candidates for recall, then
    re-ranks the merged pool by the recency/importance/relevance formula (doc §IV)."""
    if not client.is_connected():
        return []
    try:
        async def _vector_search() -> dict[str, float]:
            if not _qdrant:
                return {}
            vector = await _embed(query)
            if not vector:
                return {}
            must = [
                FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
                FieldCondition(key="agent_id",  match=MatchValue(value=agent_id)),
            ]
            if domain:
                must.append(FieldCondition(key="domain", match=MatchValue(value=domain)))
            hits = await _qdrant.search(
                _COLLECTION,
                query_vector=vector,
                query_filter=Filter(must=must),
                score_threshold=0.7,
                limit=limit * 4,
            )
            return {h.payload["pg_id"]: h.score for h in hits}

        async def _fts_search(session: Any) -> list[Any]:
            fts_q = (
                select(
                    AgentMemoriesORM.id, AgentMemoriesORM.embedding_ref, AgentMemoriesORM.content,
                    AgentMemoriesORM.memory_type, AgentMemoriesORM.scope, AgentMemoriesORM.domain,
                    AgentMemoriesORM.importance, AgentMemoriesORM.updated_at,
                )
                .where(AgentMemoriesORM.tenant_id == UUID(tenant_id))
                .where(AgentMemoriesORM.agent_id  == UUID(agent_id))
                .where(AgentMemoriesORM.deleted_at.is_(None))
                .where(
                    func.to_tsvector("simple", AgentMemoriesORM.content).op("@@")(
                        func.plainto_tsquery("simple", query[:100])
                    )
                )
            )
            if domain:
                fts_q = fts_q.where(AgentMemoriesORM.domain == domain)
            if user_ref and user_ref != "anonymous":
                fts_q = fts_q.where(
                    (AgentMemoriesORM.scope == "global") |
                    ((AgentMemoriesORM.scope == "user") & (AgentMemoriesORM.user_ref == user_ref))
                )
            rows = (await session.execute(fts_q.limit(limit * 4))).fetchall()
            return cast(list[Any], rows)

        async with client.get_client(tenant_id=tenant_id) as session:
            vec_scores, fts_rows = await asyncio.gather(
                _vector_search(),
                _fts_search(session),
            )

            # Qdrant's "pg_id" payload field is actually the deterministic point id,
            # stored on the Postgres row as embedding_ref (NOT the row's own `id` —
            # see services/memory-consumer/main.py:_qdrant_point_id). Key everything
            # by embedding_ref so vector and FTS candidates can actually overlap in
            # RRF, falling back to `id` only for rows with no embedding (FTS-only).
            vec_ids    = list(vec_scores.keys())
            fts_ids    = [str(r.embedding_ref or r.id) for r in fts_rows]
            rows_by_id = {str(r.embedding_ref or r.id): r._asdict() for r in fts_rows}

            missing = [id_ for id_ in vec_ids if id_ not in rows_by_id]
            if missing:
                extra = await session.execute(
                    select(
                        AgentMemoriesORM.id, AgentMemoriesORM.embedding_ref, AgentMemoriesORM.content,
                        AgentMemoriesORM.memory_type, AgentMemoriesORM.scope, AgentMemoriesORM.domain,
                        AgentMemoriesORM.importance, AgentMemoriesORM.updated_at,
                    )
                    .where(AgentMemoriesORM.embedding_ref.in_(missing))
                    .where(AgentMemoriesORM.deleted_at.is_(None))
                )
                for r in extra.fetchall():
                    rows_by_id[str(r.embedding_ref)] = r._asdict()

            # RRF widens the candidate pool for recall; final ranking uses the weighted formula.
            candidate_ids = _rrf(vec_ids, fts_ids)[:limit * 2] if (vec_ids or fts_ids) else []
            weights = (
                _RETRIEVAL_WEIGHTS.get(domain, _RETRIEVAL_WEIGHTS["default"])
                if domain is not None
                else _RETRIEVAL_WEIGHTS["default"]
            )

            scored = []
            for id_ in candidate_ids:
                row = rows_by_id.get(id_)
                if not row:
                    continue
                relevance = vec_scores.get(id_, 0.5 if id_ in fts_ids else 0.0)
                scored.append((_retrieval_score(row, relevance, weights), row))
            scored.sort(key=lambda x: x[0], reverse=True)

            memories = [row for _, row in scored[:limit]]
            logger.info("memory | retrieved %d (vec=%d fts=%d) for agent=%s",
                        len(memories), len(vec_ids), len(fts_ids), agent_id)
            return memories

    except Exception as e:
        logger.warning("memory | retrieve failed: %s", e)
        return []


async def retrieve_episodic_past(
    tenant_id: str,
    agent_id: str,
    user_ref: str,
    limit: int = 10,
    days: int = _EPISODIC_LOOKBACK_DAYS,
) -> List[dict[str, Any]]:
    """doc §IV — Episodic Past: WHERE user_ref=? AND created_at > now()-30d ORDER BY importance DESC."""
    if not client.is_connected():
        return []
    try:
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        async with client.get_client(tenant_id=tenant_id) as session:
            rows = (await session.execute(
                select(EpisodicMemoryORM.content, EpisodicMemoryORM.importance_score,
                       EpisodicMemoryORM.created_at, EpisodicMemoryORM.domain)
                .where(EpisodicMemoryORM.tenant_id == UUID(tenant_id))
                .where(EpisodicMemoryORM.agent_id  == UUID(agent_id))
                .where(EpisodicMemoryORM.user_ref  == user_ref)
                .where(EpisodicMemoryORM.status    == "active")
                .where(EpisodicMemoryORM.created_at > cutoff)
                .order_by(EpisodicMemoryORM.importance_score.desc(), EpisodicMemoryORM.created_at.desc())
                .limit(limit)
            )).fetchall()
            return [r._asdict() for r in rows]
    except Exception as e:
        logger.warning("memory | retrieve_episodic_past failed: %s", e)
        return []


async def retrieve_pending_reminders(tenant_id: str, agent_id: str, user_ref: str) -> List[dict[str, Any]]:
    """doc §IV/§3.4 — Episodic Future: surfaced once at the start of a new
    session. doc §3.4 also specifies what happens after surfacing: a
    one-off reminder is marked executed; a recurring one gets a new row at
    the next cron occurrence instead of staying pending forever. Without
    this, the same due reminder would resurface every session indefinitely."""
    if not client.is_connected():
        return []
    try:
        async with client.get_client(tenant_id=tenant_id) as session:
            rows = (await session.execute(
                select(FutureMemoryORM.id, FutureMemoryORM.content,
                       FutureMemoryORM.execution_time, FutureMemoryORM.recurrence)
                .where(FutureMemoryORM.tenant_id == UUID(tenant_id))
                .where(FutureMemoryORM.agent_id  == UUID(agent_id))
                .where(FutureMemoryORM.user_ref  == user_ref)
                .where(FutureMemoryORM.status    == "pending")
                .where(FutureMemoryORM.execution_time <= func.now())
            )).fetchall()
            reminders = [r._asdict() for r in rows]
            if not reminders:
                return []

            now = datetime.now(timezone.utc)
            for r in reminders:
                if r["recurrence"]:
                    try:
                        next_time = croniter(r["recurrence"], now).get_next(datetime)
                        session.add(FutureMemoryORM(
                            id=uuid4(),
                            tenant_id=UUID(tenant_id),
                            agent_id=UUID(agent_id),
                            user_ref=user_ref,
                            content=r["content"],
                            execution_time=next_time,
                            recurrence=r["recurrence"],
                        ))
                    except Exception as e:
                        logger.warning("memory | bad cron expression %r for reminder=%s: %s",
                                       r["recurrence"], r["id"], e)
                await session.execute(
                    update(FutureMemoryORM)
                    .where(FutureMemoryORM.id == r["id"])
                    .values(status="executed", executed_at=now)
                )
            await session.commit()
            return reminders
    except Exception as e:
        logger.warning("memory | retrieve_pending_reminders failed: %s", e)
        return []


async def apply_memory_policy(
    state: dict[str, Any],
    agent_id: str,
    tenant_id: str,
    user_ref: str,
    conversation_id: Optional[str] = None,
    message_id: Optional[str] = None,
) -> None:
    """Emits memory.extraction.requested for the async consumer (doc §II) instead
    of extracting/writing inline. Payload stays minimal — the consumer re-reads the
    turn text from Messages by message_id, so Postgres remains the single source of
    truth and retries are naturally idempotent."""
    if not client.is_connected() or not conversation_id or not message_id:
        return
    try:
        async with client.get_client(tenant_id=tenant_id) as session:
            policies = (await session.execute(
                select(MemoryPolicyORM.action_type)
                .where(MemoryPolicyORM.agent_id == UUID(agent_id))
                .where(MemoryPolicyORM.enabled.is_(True))
            )).fetchall()
        if not any(action == "add" for (action,) in policies):
            return
    except Exception as e:
        logger.warning("memory | apply_policy policy lookup failed: %s", e)
        return

    produce_event(
        "memory.extraction.requested",
        key=conversation_id,
        value={
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "user_ref": user_ref,
            "conversation_id": conversation_id,
            "message_id": message_id,
        },
    )
