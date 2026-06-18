import os
import asyncio
import logging
import httpx
from typing import List, Optional
from uuid import UUID, uuid4

from sqlalchemy import select, func
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue

from services.database_connector.postgres_connector import client
from basemodel.services_databaseconnector.postgres_orm.workflow_orm import AgentMemoriesORM, MemoryPolicyORM

logger = logging.getLogger(__name__)

_DEV_AGENT_ID  = os.getenv("DEV_AGENT_ID",  "00000000-0000-0000-0000-000000000030")
_DEV_TENANT_ID = os.getenv("DEV_TENANT_ID", "00000000-0000-0000-0000-000000000001")

_LITELLM_BASE  = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
_LITELLM_KEY   = os.environ.get("LITELLM_API_KEY",  "sk-dev")
_QDRANT_URL    = os.environ.get("QDRANT_URL",        "http://localhost:6333")
_EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "1024"))
_COLLECTION    = "agent_memories"

_qdrant: Optional[AsyncQdrantClient] = None


async def init_qdrant():
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


async def close_qdrant():
    global _qdrant
    if _qdrant:
        await _qdrant.close()
        _qdrant = None
        logger.info("qdrant | closed")


_PROMPT_DIR = os.path.join(os.path.dirname(__file__), "prompts")
with open(os.path.join(_PROMPT_DIR, "memory_extract.txt")) as _f:
    _EXTRACT_PROMPT = _f.read()


async def _extract_memory_content(query: str, response: str) -> Optional[str]:
    """Dùng LLM trích xuất facts/preferences đáng nhớ. Trả None nếu không có gì."""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{_LITELLM_BASE}/v1/chat/completions",
                headers={"Authorization": f"Bearer {_LITELLM_KEY}"},
                json={
                    "model": "planner",
                    "messages": [{"role": "user", "content": _EXTRACT_PROMPT.format(
                        query=query[:500], response=response[:1000]
                    )}],
                    "max_tokens": 100,
                    "temperature": 0,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            return None if content.upper() == "NONE" else content
    except Exception as e:
        logger.warning("memory extract failed: %s", e)
        return None


async def _embed(text: str) -> Optional[list]:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{_LITELLM_BASE}/v1/embeddings",
                headers={"Authorization": f"Bearer {_LITELLM_KEY}"},
                json={"model": "embedder", "input": text},
            )
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]
    except Exception as e:
        logger.warning("embed failed — falling back to full-text search: %s", e)
        return None


def _rrf(vec_ids: list, fts_ids: list, k: int = 60) -> list:
    """Reciprocal Rank Fusion — combine vector and full-text search results."""
    scores: dict = {}
    for rank, id_ in enumerate(vec_ids):
        scores[id_] = scores.get(id_, 0.0) + 1 / (k + rank + 1)
    for rank, id_ in enumerate(fts_ids):
        scores[id_] = scores.get(id_, 0.0) + 1 / (k + rank + 1)
    return sorted(scores, key=lambda x: scores[x], reverse=True)


async def retrieve_memories(
    tenant_id: str,
    agent_id: str,
    user_ref: str,
    query: str,
    limit: int = 5,
) -> List[dict]:
    if not client.is_connected():
        return []
    try:
        async def _vector_search() -> list[str]:
            if not _qdrant:
                return []
            vector = await _embed(query)
            if not vector:
                return []
            hits = await _qdrant.search(
                _COLLECTION,
                query_vector=vector,
                query_filter=Filter(must=[
                    FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
                    FieldCondition(key="agent_id",  match=MatchValue(value=agent_id)),
                ]),
                limit=limit * 2,
            )
            return [h.payload["pg_id"] for h in hits]

        async def _fts_search(session) -> list:
            fts_q = (
                select(AgentMemoriesORM.id, AgentMemoriesORM.content, AgentMemoriesORM.memory_type, AgentMemoriesORM.scope)
                .where(AgentMemoriesORM.tenant_id == UUID(tenant_id))
                .where(AgentMemoriesORM.agent_id  == UUID(agent_id))
                .where(AgentMemoriesORM.deleted_at.is_(None))
                .where(
                    func.to_tsvector("simple", AgentMemoriesORM.content).op("@@")(
                        func.plainto_tsquery("simple", query[:100])
                    )
                )
            )
            if user_ref and user_ref != "anonymous":
                fts_q = fts_q.where(
                    (AgentMemoriesORM.scope == "global") |
                    ((AgentMemoriesORM.scope == "user") & (AgentMemoriesORM.user_ref == user_ref))
                )
            rows = (await session.execute(fts_q.limit(limit * 2))).fetchall()
            return rows

        # Chạy song song Qdrant + PostgreSQL
        async with client.get_client() as session:
            vec_ids, fts_rows = await asyncio.gather(
                _vector_search(),
                _fts_search(session),
            )

            fts_ids   = [str(r.id) for r in fts_rows]
            rows_by_id = {str(r.id): r._asdict() for r in fts_rows}

            # Fetch PostgreSQL rows cho vec_ids chưa có trong fts
            missing = [id_ for id_ in vec_ids if id_ not in rows_by_id]
            if missing:
                extra = await session.execute(
                    select(AgentMemoriesORM.id, AgentMemoriesORM.content, AgentMemoriesORM.memory_type, AgentMemoriesORM.scope)
                    .where(AgentMemoriesORM.id.in_([UUID(i) for i in missing]))
                )
                for r in extra.fetchall():
                    rows_by_id[str(r.id)] = r._asdict()

            # RRF — merge và rank
            ranked_ids = _rrf(vec_ids, fts_ids)[:limit] if (vec_ids or fts_ids) else []
            memories   = [rows_by_id[id_] for id_ in ranked_ids if id_ in rows_by_id]
            logger.info("memory | retrieved %d (vec=%d fts=%d) for agent=%s",
                        len(memories), len(vec_ids), len(fts_ids), agent_id)
            return memories

    except Exception as e:
        logger.warning("memory | retrieve failed: %s", e)
        return []


async def apply_memory_policy(state: dict, agent_id: str, tenant_id: str, user_ref: str = "anonymous") -> None:
    if not client.is_connected():
        return
    try:
        async with client.get_client() as session:
            policies = (await session.execute(
                select(MemoryPolicyORM.action_type, MemoryPolicyORM.condition)
                .where(MemoryPolicyORM.agent_id == UUID(agent_id))
                .where(MemoryPolicyORM.enabled.is_(True))
            )).fetchall()

            for (action, condition) in policies:
                if action == "add":
                    response = state.get("response", "").strip()
                    query    = state.get("query",    "").strip()
                    if not response:
                        continue

                    content = await _extract_memory_content(query, response)
                    if not content:
                        continue  # LLM đánh giá không có gì đáng nhớ

                    # Dedup: skip nếu exact content đã tồn tại cho cùng agent/tenant/user
                    scope = (condition or {}).get("scope", "user")
                    dedup_q = (
                        select(AgentMemoriesORM.id)
                        .where(AgentMemoriesORM.tenant_id == UUID(tenant_id))
                        .where(AgentMemoriesORM.agent_id  == UUID(agent_id))
                        .where(AgentMemoriesORM.content   == content)
                        .where(AgentMemoriesORM.deleted_at.is_(None))
                    )
                    if scope == "user":
                        dedup_q = dedup_q.where(AgentMemoriesORM.user_ref == user_ref)
                    if (await session.execute(dedup_q.limit(1))).first():
                        logger.info("memory | duplicate skipped for agent=%s", agent_id)
                        continue

                    pg_id = uuid4()

                    memory = AgentMemoriesORM(
                        id=pg_id,
                        tenant_id=UUID(tenant_id),
                        agent_id=UUID(agent_id),
                        user_ref=user_ref if scope == "user" else None,
                        content=content,
                        memory_type="auto",
                        scope=scope,
                    )
                    session.add(memory)
                    await session.flush()

                    # Save vector to Qdrant
                    if _qdrant:
                        vector = await _embed(content)
                        if vector:
                            await _qdrant.upsert(
                                _COLLECTION,
                                points=[PointStruct(
                                    id=str(pg_id),
                                    vector=vector,
                                    payload={
                                        "tenant_id": tenant_id,
                                        "agent_id":  agent_id,
                                        "pg_id":     str(pg_id),
                                    },
                                )],
                            )

                    logger.info("memory | saved for agent=%s", agent_id)
                # TODO: update, delete, summarize

            await session.commit()
    except Exception as e:
        logger.warning("memory | apply_policy failed: %s", e)
