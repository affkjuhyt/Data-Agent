"""Business logic for Qdrant collection management and semantic search."""
import logging
import uuid
import httpx
from fastapi import HTTPException
from sqlalchemy import update as sa_update, select

from basemodel.services_databaseconnector.postgres_model import ReadJoinRequest
from basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import KBQdrantCollectionORM
from basemodel.services_databaseconnector.qdrant_model import SearchRequest, MatchType
from services.database_connector.qdrant_connector import vector_search as qdrant_vector_search
from services.backend.UI_model.response import to_string

log = logging.getLogger(__name__)


def _to_qcollection(row: dict) -> dict:
    return {
        "id":              to_string(row.get("collection_id")),
        "name":            row.get("collection_name", ""),
        "points":          row.get("points_count", 0),
        "active":          bool(row.get("is_active", False)),
        "dimensions":      row.get("vector_dimension") or 0,
        "distance":        row.get("similarity_metric", "cosine"),
        "indexed":         row.get("points_count", 0),
        "embedding_model": to_string(row.get("embedding_model_id", "")),
    }


async def _live_collections(qdrant) -> list:
    try:
        result = await qdrant.get_client().get_collections()
        return [
            {
                "id":         c.name,
                "name":       c.name,
                "points":     0,
                "active":     True,
                "dimensions": 0,
                "distance":   "cosine",
                "indexed":    0,
            }
            for c in result.collections
        ]
    except Exception as e:
        log.warning("qdrant_ops: live collection fallback failed: %s", e)
        return []


async def list_collections(postgres, qdrant, tenant_id: str | None) -> list:
    resp = await postgres.read(ReadJoinRequest(
        tenant_id=tenant_id,
        joins_table=["KBQdrantConnection", "KBQdrantCollection"],
        limit=100,
    ))
    if resp.code != 200:
        return await _live_collections(qdrant)
    return [_to_qcollection(r) for r in (resp.data or [])]


async def toggle_collection(postgres, collection_id: str, is_active: bool) -> dict:
    collection_uuid = uuid.UUID(collection_id) if isinstance(collection_id, str) else collection_id
    async with postgres.get_client() as session:
        await session.execute(
            sa_update(KBQdrantCollectionORM)
            .where(KBQdrantCollectionORM.collection_id == collection_uuid)
            .values(is_active=is_active)
        )
        await session.commit()
    return {"collection_id": collection_id, "active": is_active}


async def _resolve_collection_info(postgres, collection_id: str) -> tuple[str, str | None]:
    """Return (collection_name, embed_model_id) for a given collection UUID.

    collection_name: real Qdrant string name used to address the collection.
    embed_model_id: UUID string from KBQdrantCollection.embedding_model_id, or None.
    Falls back to (collection_id, None) if Postgres is unreachable.
    """
    try:
        col_uuid = uuid.UUID(collection_id)
        async with postgres.get_client() as session:
            row = await session.execute(
                select(
                    KBQdrantCollectionORM.collection_name,
                    KBQdrantCollectionORM.embedding_model_id,
                )
                .where(KBQdrantCollectionORM.collection_id == col_uuid)
            )
            result = row.one_or_none()
        if result:
            model_id = str(result.embedding_model_id) if result.embedding_model_id else None
            return result.collection_name, model_id
    except Exception as e:
        log.warning("qdrant_ops: could not resolve collection info for %s: %s", collection_id, e)
    return collection_id, None


async def semantic_search(
    qdrant,
    postgres,
    collection_id: str,
    query: str,
    tenant_id: str,
    limit: int,
    model_service_url: str,
) -> list:
    collection_name, embed_model_id = await _resolve_collection_info(postgres, collection_id)

    embed_payload: dict = {"texts": [query]}
    if embed_model_id:
        embed_payload["model_id"] = embed_model_id

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            emb_resp = await client.post(
                f"{model_service_url}/embed",
                json=embed_payload,
            )
            emb_resp.raise_for_status()
            vector = emb_resp.json()["embeddings"][0]
    except Exception as e:
        log.error("qdrant_ops: embedding failed for collection %s: %s", collection_id, e)
        raise HTTPException(status_code=502, detail="Embedding service unavailable")

    search_resp = await qdrant_vector_search(qdrant.get_client(), SearchRequest(
        collection_name=collection_name,
        tenant_id=tenant_id,
        query_vector=vector,
        matching_payload=[],
        match_type=MatchType.must,
        limit=limit,
    ))
    if search_resp.code != 200:
        log.error("qdrant_ops: vector search failed: %s", search_resp.error)
        raise HTTPException(status_code=500, detail="Vector search failed")

    return [
        {
            "point_id": str(getattr(hit, "id", "")),
            "score":    round(getattr(hit, "score", 0.0), 4),
            "summary":  (getattr(hit, "payload", {}) or {}).get("summary", ""),
            "entities": (getattr(hit, "payload", {}) or {}).get("entities", []),
            "intent":   (getattr(hit, "payload", {}) or {}).get("intents", []),
        }
        for hit in (search_resp.data or [])
    ]
