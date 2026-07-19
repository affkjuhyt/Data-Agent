"""Schema Catalog retrieval (sandbox_schema_docs.docx §II) — the agent-facing
read side of the schema-crawler-worker. Same Qdrant init/search shape as
workflow_memory.py, separate collection ("schema_catalog", written only by
the crawler worker, never by workflow-runtime) so this module is read-only.

doc §2.9 calls this "contextual schema retrieval": embed
(user question + agent's reasoning about what data it needs), vector-search
filtered by tenant (+ optional schema_name), return top-K table payloads —
each already containing columns/row_count/join_hints, ready to render
straight into the SQL-generation prompt without a second DB round-trip.
"""
import logging
import os
from typing import Any, List, Optional, cast

import httpx
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, VectorParams, Filter, FieldCondition, MatchValue

logger = logging.getLogger(__name__)

_LITELLM_BASE  = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
_LITELLM_KEY   = os.environ.get("LITELLM_API_KEY",  "sk-dev")
_QDRANT_URL    = os.environ.get("QDRANT_URL",        "http://localhost:6333")
_EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "1024"))
_COLLECTION    = "schema_catalog"
_SCORE_THRESHOLD = 0.5  # doc §2.5b — schema discovery favors recall over precision

_qdrant: Optional[AsyncQdrantClient] = None


async def init_schema_catalog_qdrant() -> None:
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


async def close_schema_catalog_qdrant() -> None:
    global _qdrant
    if _qdrant:
        await _qdrant.close()
        _qdrant = None
        logger.info("qdrant | '%s' closed", _COLLECTION)


async def _embed(text: str) -> Optional[list[Any]]:
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.post(
                f"{_LITELLM_BASE}/v1/embeddings",
                headers={"Authorization": f"Bearer {_LITELLM_KEY}"},
                json={"model": "embedder", "input": text},
            )
            resp.raise_for_status()
            return cast(list[Any], resp.json()["data"][0]["embedding"])
    except Exception as e:
        logger.warning("schema_catalog | embed failed: %s", e)
        return None


async def retrieve_schema_catalog(
    tenant_id: str,
    query: str,
    schema_name: Optional[str] = None,
    limit: int = 5,
) -> List[dict[str, Any]]:
    """Hybrid (vector + metadata filter) lookup. Returns Qdrant payloads
    directly — they already carry everything format_catalog_context() needs
    (text_description, columns, row_count, join_hints), unlike
    workflow_memory's retrieve_workflow_memory() which re-reads Postgres for
    the full row; the Schema Catalog payload IS the full row by design (doc
    §2.5 — "payload chứa đầy đủ metadata cần thiết để agent sử dụng ngay")."""
    if not _qdrant:
        return []
    try:
        vector = await _embed(query)
        if not vector:
            return []

        must = [FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))]
        if schema_name:
            must.append(FieldCondition(key="schema_name", match=MatchValue(value=schema_name)))

        hits = await _qdrant.search(
            _COLLECTION,
            query_vector=vector,
            query_filter=Filter(must=must),
            score_threshold=_SCORE_THRESHOLD,
            limit=limit,
        )
        return [h.payload for h in hits]
    except Exception as e:
        logger.warning("schema_catalog | retrieve failed: %s", e)
        return []


def format_catalog_context(tables: List[dict[str, Any]]) -> str:
    """Renders Schema Catalog payloads as abbreviated DDL + business
    description for the SQL-generation prompt — same shape of output as
    workflow_memory.format_schema_context(), different source."""
    if not tables:
        return ""
    blocks = []
    for t in tables:
        col_lines = "\n".join(f"  {c}" for c in t.get("columns", []))
        join_hints = t.get("join_hints") or []
        hint_line = f"\n-- Join hints: {', '.join(join_hints)}" if join_hints else ""
        blocks.append(
            f"-- {t.get('text_description', '')}{hint_line}\n"
            f"TABLE {t.get('schema_name', '')}.{t.get('table_name', '')} (\n{col_lines}\n)"
        )
    return "\n\n".join(blocks)
