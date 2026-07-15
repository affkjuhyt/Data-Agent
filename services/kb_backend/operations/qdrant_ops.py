"""Business logic for Qdrant collection management and semantic search."""
import logging
import uuid
from fastapi import HTTPException
from qdrant_client import models
from sqlalchemy import update as sa_update, select

from libs.basemodel.services_databaseconnector.postgres_model import ReadJoinRequest
from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import KBQdrantCollectionORM
from libs.basemodel.services_databaseconnector.qdrant_model import SearchRequest, MatchType
from libs.database_connector.qdrant_connector import vector_search as qdrant_vector_search
from services.kb_backend.schemas import to_string

log = logging.getLogger(__name__)


def _vector_params(info):
    """Pull (dimension, distance) from a CollectionInfo, handling both the
    single-vector and named-vectors configs."""
    vectors = info.config.params.vectors
    if hasattr(vectors, "size"):
        return vectors.size, str(vectors.distance).split(".")[-1].lower()
    if isinstance(vectors, dict) and vectors:
        first = next(iter(vectors.values()))
        return getattr(first, "size", 0), str(getattr(first, "distance", "cosine")).split(".")[-1].lower()
    return 0, "cosine"


async def _tenant_point_count(client, name: str, tenant_id: str) -> int:
    """Count points in `name` that belong to this tenant (excluding soft-deleted).
    Collections without a tenant_id payload (e.g. caches) simply return 0."""
    try:
        result = await client.count(
            collection_name=name,
            count_filter=models.Filter(must=[
                models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id)),
            ]),
            exact=True,
        )
        return result.count
    except Exception as e:
        log.warning("qdrant_ops: tenant count failed for %s: %s", name, e)
        return 0


async def list_collections(postgres, qdrant, tenant_id: str | None) -> list:
    """List the REAL collections present in Qdrant. Point counts are scoped to the
    caller's tenant; `total_points` is the all-tenant total for context. Active
    state is overlaid from the Postgres registry when a name matches."""
    client = qdrant.get_client()
    try:
        live = (await client.get_collections()).collections
    except Exception as e:
        log.error("qdrant_ops: cannot list live collections: %s", e)
        return []

    # Overlay registry metadata (active flag, embedding model) by collection name.
    registry: dict[str, dict] = {}
    try:
        resp = await postgres.read(ReadJoinRequest(
            tenant_id=tenant_id,
            joins_table=["KBQdrantConnection", "KBQdrantCollection"],
            limit=200,
        ))
        if resp.code == 200:
            registry = {r.get("collection_name"): r for r in (resp.data or [])}
    except Exception as e:
        log.warning("qdrant_ops: registry overlay unavailable: %s", e)

    out: list[dict] = []
    for c in live:
        name = c.name
        dim, distance, total, indexed_pct = 0, "cosine", 0, 100
        try:
            info = await client.get_collection(name)
            dim, distance = _vector_params(info)
            total = info.points_count or 0
            iv = info.indexed_vectors_count or 0
            indexed_pct = round(iv / total * 100) if total else 100
        except Exception as e:
            log.warning("qdrant_ops: get_collection(%s) failed: %s", name, e)

        tenant_points = await _tenant_point_count(client, name, tenant_id) if tenant_id else 0
        reg = registry.get(name)
        out.append({
            "id":              name,                 # the real Qdrant identifier
            "name":            name,
            "points":          tenant_points,        # scoped to this tenant
            "total_points":    total,                # all tenants (context)
            "active":          bool(reg.get("is_active", True)) if reg else True,
            "dimensions":      dim,
            "distance":        distance,
            "indexed":         indexed_pct,
            "embedding_model": to_string(reg.get("embedding_model_id", "")) if reg else "",
        })
    return out


async def toggle_collection(postgres, collection_id: str, is_active: bool) -> dict:
    # collection_id is now the real Qdrant collection NAME (live listing). Match the
    # registry row by UUID when it parses as one, otherwise by name. No matching
    # row is a no-op — the UI still reflects the toggled state.
    stmt = sa_update(KBQdrantCollectionORM).values(is_active=is_active)
    try:
        stmt = stmt.where(KBQdrantCollectionORM.collection_id == uuid.UUID(str(collection_id)))
    except (ValueError, AttributeError, TypeError):
        stmt = stmt.where(KBQdrantCollectionORM.collection_name == collection_id)
    async with postgres.get_client() as session:
        await session.execute(stmt)
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
    model,
    collection_id: str,
    query: str,
    tenant_id: str,
    limit: int,
) -> list:
    collection_name, embed_model_id = await _resolve_collection_info(postgres, collection_id)

    # Reuse the pooled, health-gated ModelServiceClient (injected via svc()) rather
    # than opening a throwaway httpx connection per request. embed() targets the
    # same /embed endpoint with the same payload shape.
    try:
        vectors = await model.embed([query], embed_model_id)
        vector = vectors[0]
    except Exception as e:
        log.error("qdrant_ops: embedding failed for collection %s: %s", collection_id, e)
        raise HTTPException(status_code=502, detail="Embedding service unavailable")

    try:
        search_resp = await qdrant_vector_search(qdrant.get_client(), SearchRequest(
            collection_name=collection_name,
            tenant_id=tenant_id,
            query_vector=vector,
            matching_payload=[],
            match_type=MatchType.must,
            limit=limit,
        ))
    except Exception as e:
        # Most common cause locally: the model-service embeds at a different
        # dimension than the collection was built with (e.g. 384 vs 1024).
        msg = str(e)
        if "dimension" in msg.lower():
            detail = (f"Query embedding is {len(vector)}-dim but collection "
                      f"'{collection_name}' expects a different dimension. "
                      f"Configure the model-service embedding model to match.")
        else:
            detail = f"Vector search failed: {msg}"
        log.error("qdrant_ops: vector search error on %s: %s", collection_name, e)
        raise HTTPException(status_code=422, detail=detail)
    if search_resp.code != 200:
        log.error("qdrant_ops: vector search failed: %s", search_resp.error)
        raise HTTPException(status_code=422, detail="Vector search failed")

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
