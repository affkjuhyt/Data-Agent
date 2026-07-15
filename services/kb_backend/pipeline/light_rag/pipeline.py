"""
LightRAG Retrieval Pipeline
============================
All step functions + orchestrator in one file.
Clients are injected — no module-level singletons.

Pipeline flow (steps in [] are env-gated):
  1.  embed_query      — model-service /embed
  2.  cache_lookup     — Qdrant semantic cache → early return on hit
  3.  canonicalize     — LLM via the model-service gateway (/generate, env-routed
                         local/remote) → {intent, intents, entities, relationships}
  3b. [alias_resolve]  — map query entities through the learned KBEntityLookup table
  4.  qdrant_search    — Pass 1: entity filter (any) on the chunk collection
  4b. [doc_narrow]     — top-m docs from the doc collection by intent → chunk filter
  5.  rank_entities    — score pass-1 entities by frequency × relevance
  6.  graph_expand     — Neo4j: top-N entities → related entity names (tenant-filtered)
  7.  qdrant_search    — Pass 2: expanded Neo4j entities; [typed_pass] on ent_<type>
  8.  rrf_merge        — fuse pass1 + pass2 + typed + doc lists
  9.  rerank           — cross-encoder on summaries (model-service /rerank)
  10. hydrate          — fetch active chunk text + source from Postgres
  11. cache_store      — cache the post-rerank block_ids for near-identical queries
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
import asyncio
import json
import logging
import math
import re
import time
import unicodedata
import uuid as _uuid

from neo4j import AsyncDriver
from qdrant_client import models as qmodels
from qdrant_client.async_qdrant_client import AsyncQdrantClient
from sqlalchemy import func, select as sa_select

from libs.database_connector.qdrant_connector import vector_search as qdrant_vector_search
from libs.database_connector.postgres_connector import PostgresClient
from libs.database_connector.model_connector import ModelServiceClient
from libs.basemodel.services_databaseconnector.qdrant_model import (
    MatchingPayload,
    MatchType,
    SearchRequest,
)
from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBDataORM,
    KBTextBlockORM,
    KBTextBlockVersionORM,
    KBTextTableORM,
)

from services.kb_backend.pipeline.light_rag import config as cfg

log = logging.getLogger(__name__)


def _sigmoid(x: float) -> float:
    """Map an unbounded cross-encoder logit to a (0, 1) relevance probability."""
    try:
        return 1.0 / (1.0 + math.exp(-float(x)))
    except (OverflowError, TypeError, ValueError):
        return 0.0


def _clamp01(x: Any) -> float:
    try:
        return max(0.0, min(1.0, float(x)))
    except (TypeError, ValueError):
        return 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# LLM access — all generation goes through the model-service gateway
# (ModelServiceClient.generate → /generate, env-routed local/remote). The old
# direct vLLM/llama helpers were removed when canonicalize moved to the gateway.
# ═══════════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 1 — Canonicalize Query
# ═══════════════════════════════════════════════════════════════════════════════

# The content-purpose vocabulary is single-sourced from config (cfg.CONTENT_INTENTS)
# so this prompt and the doc-narrowing filter can never drift. Wrapped to ~3 per line
# for prompt readability.
def _intent_vocab_block() -> str:
    labels = cfg.CONTENT_INTENTS
    lines = ["  " + ", ".join(labels[i:i + 3]) for i in range(0, len(labels), 3)]
    return "\n".join(lines)


_CANONICALIZE_SYSTEM = """\
Convert the user's query into a structured semantic format for knowledge retrieval.
Detect the language (English or Vietnamese) and keep names in that language.

Respond ONLY with valid JSON:
{{"intent": "<label>", "intents": ["<purpose>"], "entities": ["<e1>", "<e2>"], "relationships": [["<e1>", "<rel>", "<e2>"]]}}

"intent" is the query SHAPE (one free label, e.g. distance_query, causal_query).
"intents" is 1-3 content-PURPOSE labels describing what kind of document content
would answer this query. ALWAYS English snake_case. Choose from this fixed set
(these are the same labels documents are tagged with — they are the filter keys):
{vocab}

Examples:
Query: "Distance between NYU and Trento"
{{"intent":"distance_query","intents":["information_retrieval"],"entities":["NYU","Trento"],"relationships":[["NYU","geographic_distance","Trento"]]}}

Query: "Nghị định 13 quy định gì về bảo vệ dữ liệu cá nhân?"
{{"intent":"regulation_query","intents":["regulation_compliance","data_definition"],"entities":["Nghị định 13"],"relationships":[]}}""".format(
    vocab=_intent_vocab_block()
)


def _parse_json_obj(raw: str) -> dict[str, Any]:
    """Strip ```json fences and parse; {} on failure."""
    if not raw:
        return {}
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1]
        if s.startswith("json"):
            s = s[4:]
        s = s.rsplit("```", 1)[0].strip()
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError as exc:
        log.warning("llm | JSON parse failed: %r — %s", raw[:200], exc)
        return {}


async def canonicalize(model: ModelServiceClient, query: str) -> dict[str, Any]:
    """Query understanding via the model-service LLM gateway — the pipeline does not
    know or care which LLM backend (local / Colab) actually serves the request."""
    try:
        content = await model.generate(
            prompt=query, system_prompt=_CANONICALIZE_SYSTEM,
            json_mode=True, max_tokens=256, temperature=0.0,
        )
    except Exception as exc:
        log.warning("canonicalize | failed (%s) — continuing without LLM step", exc)
        return {"intent": "general", "entities": [], "relationships": []}
    result = _parse_json_obj(content)
    entities = result.get("entities", [])
    intents = result.get("intents", [])
    return {
        "intent": str(result.get("intent", "general")),
        # Content-purpose labels used to narrow candidate documents in step 1.
        "intents": [str(i) for i in (intents if isinstance(intents, list) else []) if str(i).strip()],
        "entities": [str(e) for e in (entities if isinstance(entities, list) else [])],
        "relationships": result.get("relationships", []),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 3 — Cache Lookup
# ═══════════════════════════════════════════════════════════════════════════════

def _cache_filter(tenant_id: str, knowledge_base: str | None, unexpired: bool) -> qmodels.Filter:
    must = [
        qmodels.FieldCondition(key="tenant_id", match=qmodels.MatchValue(value=tenant_id)),
        qmodels.FieldCondition(key="is_deleted", match=qmodels.MatchValue(value=False)),
    ]
    if knowledge_base:
        must.append(qmodels.FieldCondition(key="knowledge_base", match=qmodels.MatchValue(value=knowledge_base)))
    if unexpired:
        # Treat entries past their 24h TTL as misses (Qdrant has no native TTL).
        must.append(qmodels.FieldCondition(key="expires_at", range=qmodels.Range(gte=time.time())))
    return qmodels.Filter(must=must)


async def cache_lookup(
        qdrant: AsyncQdrantClient,
        collection: str,
        query_vector: list[float],
        tenant_id: str,
        threshold: float,
        knowledge_base: str | None = None,
) -> dict[str, Any] | None:
    try:
        if not await qdrant.collection_exists(collection):
            return None
        res = await qdrant.query_points(
            collection_name=collection,
            query=query_vector,
            limit=1,
            query_filter=_cache_filter(tenant_id, knowledge_base, unexpired=True),
            with_payload=True,
        )
        pts = res.points
        if pts and pts[0].score >= threshold:
            log.info("cache | HIT score=%.3f", pts[0].score)
            return pts[0].payload or {}
    except Exception as exc:
        log.warning("cache | lookup failed: %s", exc)
    return None


async def cache_store(
        qdrant: AsyncQdrantClient,
        collection: str,
        query_vector: list[float],
        tenant_id: str,
        knowledge_base: str | None,
        results: list[dict[str, Any]],
) -> None:
    """Cache the post-rerank *retrieval decision* keyed by the query embedding.

    We store only the ordered block_ids + scores (the chunks the pipeline chose) — NOT
    their text — so a near-identical future query skips the whole pipeline and just
    re-hydrates those blocks fresh from Postgres (content can't go stale this way).
    """
    block_refs = [
        {"block_id": r["source"]["block_id"], "score": float(r.get("chunk_score") or 0.0)}
        for r in results
        if (r.get("source") or {}).get("block_id")
    ]
    if not block_refs:
        return
    now = time.time()
    payload = {
        "tenant_id": tenant_id,
        "knowledge_base": knowledge_base or "",
        "block_refs": block_refs,
        "created_at": now,
        "expires_at": now + cfg.CACHE_TTL_SECONDS,
        "is_deleted": False,
    }
    try:
        # The cache collection is created lazily on first write (vector size = query dim).
        if not await qdrant.collection_exists(collection):
            await qdrant.create_collection(
                collection_name=collection,
                vectors_config=qmodels.VectorParams(
                    size=len(query_vector), distance=qmodels.Distance.COSINE,
                ),
            )
        await qdrant.upsert(
            collection_name=collection,
            points=[qmodels.PointStruct(id=str(_uuid.uuid4()), vector=query_vector, payload=payload)],
        )
        log.info("cache | stored %d block refs", len(block_refs))
    except Exception as exc:
        log.warning("cache | store failed: %s", exc)


def _canon(name: str) -> str:
    """Canonical entity form (lowercase + underscores) — matches how the ingest pipeline
    stores entity names in Qdrant payloads and Neo4j nodes. The query's entities come from
    the LLM in original case ("RAG"), so they must be canonicalized ("rag") to match — a
    non-empty Qdrant `should` clause is exclusionary, so a case mismatch drops every point.

    Unicode-preserving, mirroring entity_registry.to_canonical: Vietnamese diacritics are
    lexical ("Sài Gòn" → "sài_gòn", never "s_i_g_n") — an ASCII-only fold here would make
    every Vietnamese query entity miss every stored canonical name.
    """
    s = unicodedata.normalize("NFC", str(name)).casefold().strip()
    s = re.sub(r"[\s\-/\\]+", "_", s)
    s = re.sub(r"[^\w]", "", s)
    return re.sub(r"_+", "_", s).strip("_")


# Closed entity-type vocabulary — must match the ent_* payload fields the
# ingestion back-stamp writes on chunk/doc points.
ENTITY_TYPE_FIELDS = frozenset({
    "person", "organization", "location", "law_document",
    "system_product", "event", "time_period", "concept",
})

# Question-shape → expected answer entity types. Deterministic heuristics on the
# query text (Vietnamese + English) plus the LLM intent label. Used as SOFT
# ranking/expansion signals only.
_TYPE_HINTS: list[tuple[re.Pattern[str], list[str]]] = [
    (re.compile(r"\b(ai|người nào|của ai|do ai|who|whom)\b", re.IGNORECASE), ["person", "organization"]),
    (re.compile(r"\b(ở đâu|tại đâu|nơi nào|nước nào|quốc gia nào|thành phố nào|where)\b", re.IGNORECASE), ["location"]),
    (re.compile(r"\b(khi nào|năm nào|năm bao nhiêu|thời gian nào|thế kỷ nào|triều đại nào|when)\b", re.IGNORECASE),
     ["time_period", "event"]),
    (re.compile(r"\b(luật|nghị định|thông tư|hiệp định|hiệp ước|điều ước)\b", re.IGNORECASE), ["law_document"]),
    (re.compile(r"\b(chiến tranh|trận|chiến dịch|hội nghị|sự kiện)\b", re.IGNORECASE), ["event"]),
    (re.compile(r"\b(tổ chức nào|công ty nào|cơ quan nào)\b", re.IGNORECASE), ["organization"]),
]

_INTENT_TYPE_HINTS: dict[str, list[str]] = {
    "person": ["person"], "who": ["person"],
    "location": ["location"], "where": ["location"], "place": ["location"],
    "time": ["time_period", "event"], "date": ["time_period", "event"],
    "year": ["time_period", "event"], "event": ["event"],
    "law": ["law_document"], "regulation": ["law_document"],
    "organization": ["organization"],
}


def preferred_entity_types(query: str, intent: str = "") -> list[str]:
    """Expected answer entity types for the question, deduplicated, possibly empty."""
    out: list[str] = []
    for pat, types in _TYPE_HINTS:
        if pat.search(query):
            out.extend(types)
    il = (intent or "").casefold()
    for key, types in _INTENT_TYPE_HINTS.items():
        if key in il:
            out.extend(types)
    return list(dict.fromkeys(out))


def _entities_from_relationships(relationships: list[Any]) -> list[str]:
    """Pull the endpoint entity names out of canonical relationships [[e1, rel, e2], ...].

    These are entities the query explicitly relates, so they make good extra retrieval/
    graph-seed signals even if the flat entity list missed one.
    """
    out: list[str] = []
    for rel in relationships or []:
        if isinstance(rel, (list, tuple)) and len(rel) >= 3:
            for e in (rel[0], rel[2]):
                if isinstance(e, str) and e.strip():
                    out.append(e.strip())
    return out


async def purge_expired_cache(qdrant: AsyncQdrantClient, collection: str) -> int:
    """Delete all cache entries whose 24h TTL has passed (across every tenant).

    The TTL filter already hides expired entries at lookup; this reclaims their storage.
    Returns the number of entries removed.
    """
    try:
        if not await qdrant.collection_exists(collection):
            return 0
        flt = qmodels.Filter(must=[
            qmodels.FieldCondition(key="expires_at", range=qmodels.Range(lt=time.time())),
        ])
        count = await qdrant.count(collection_name=collection, count_filter=flt, exact=True)
        n = getattr(count, "count", 0)
        if n:
            await qdrant.delete(
                collection_name=collection,
                points_selector=qmodels.FilterSelector(filter=flt),
            )
            log.info("cache | swept %d expired entries", n)
        return n
    except Exception as exc:
        log.warning("cache | sweep failed: %s", exc)
        return 0


async def cache_sweep_loop(
        qdrant: AsyncQdrantClient,
        collection: str = cfg.QDRANT_CACHE_COLLECTION,
        interval: int = cfg.CACHE_SWEEP_INTERVAL,
) -> None:
    """Background loop (like the connector health-check loops) that periodically reclaims
    expired query-cache entries. Started from the backend lifespan; never raises."""
    log.info("cache | sweep loop started (interval=%ds, ttl=%ds)", interval, cfg.CACHE_TTL_SECONDS)
    while True:
        await asyncio.sleep(interval)
        try:
            await purge_expired_cache(qdrant, collection)
        except Exception as exc:
            log.warning("cache | sweep loop error: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 5 & 7 — Qdrant Semantic Search
# ═══════════════════════════════════════════════════════════════════════════════

async def qdrant_search(
        qdrant: AsyncQdrantClient,
        collection: str,
        query_vector: list[float],
        tenant_id: str,
        limit: int,
        entities: list[str] | None = None,
        knowledge_base: str | None = None,
        data_ids: list[str] | None = None,
        typed_field: tuple[str, list[str]] | None = None,
        typed_entity_groups: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    # tenant_id + knowledge_base are applied as hard AND filters inside vector_search;
    # every matching_payload below is an OR (match_type=any) over the candidates:
    #   entities             — untyped names against the generic entities field
    #   typed_entity_groups  — {type: [names]} → one condition per ent_<type> field,
    #                          so the filter looks WHERE the entity actually lives
    #                          in the payload ("Việt Minh" → ent_organization)
    #   data_ids             — doc-narrowing step 2 (selected documents)
    #   typed_field          — single answer-type condition ("ai" → ent_person)
    matching: list[MatchingPayload] = []
    if entities:
        matching.append(MatchingPayload(field="entities", values=entities))
    if typed_entity_groups:
        for etype, names in typed_entity_groups.items():
            if names:
                matching.append(MatchingPayload(field=f"ent_{etype}", values=names))
    if data_ids:
        matching.append(MatchingPayload(field="data_id", values=data_ids))
    if typed_field:
        matching.append(MatchingPayload(field=typed_field[0], values=typed_field[1]))

    req = SearchRequest(
        tenant_id=tenant_id,
        collection_name=collection,
        query_vector=query_vector,
        limit=limit,
        knowledge_base=knowledge_base,
        matching_payload=matching or None,
        match_type=MatchType.any,
    )
    try:
        resp = await qdrant_vector_search(qdrant, req)
        if resp.code != 200 or not resp.data:
            return []
        return [
            {
                "point_id": str(pt.id),
                "score": pt.score,
                "payload": (pt.payload or {}),
            }
            for pt in resp.data
        ]
    except Exception as exc:
        log.warning("qdrant_search | failed: %s", exc)
        return []


async def doc_narrow_ids(
        qdrant: AsyncQdrantClient,
        query_vector: list[float],
        tenant_id: str,
        knowledge_base: str | None = None,
        intents: list[str] | None = None,
) -> list[str]:
    """Step 1 of 2-step retrieval: top-m documents from the doc-summary collection.
    Returns their data_ids (top-m, not top-1 — committing to a single document is
    the hard-decision failure mode; m branches stay alive).

    Documents are narrowed by INTENT (the doc point carries a content-purpose
    `intents` list; entities live on the chunk points, not the doc point). When
    the query yields intents they become an OR payload filter over the doc
    `intents` field; a non-matching filter would exclude every document, so an
    empty intent-filtered result falls back to an unfiltered vector search."""

    async def _search(matching: list[MatchingPayload] | None) -> list[str]:
        req = SearchRequest(
            tenant_id=tenant_id,
            collection_name=cfg.QDRANT_DOC_COLLECTION,
            query_vector=query_vector,
            limit=cfg.DOC_PASS_TOP_M,
            knowledge_base=knowledge_base,
            matching_payload=matching,
            match_type=MatchType.any,
        )
        resp = await qdrant_vector_search(qdrant, req)
        if resp.code != 200 or not resp.data:
            return []
        return [str((pt.payload or {}).get("data_id") or pt.id) for pt in resp.data]

    try:
        matching = [MatchingPayload(field="intents", values=intents)] if intents else None
        ids = await _search(matching)
        if matching and not ids:
            log.info("doc_narrow | intent-filtered empty — unfiltered fallback")
            ids = await _search(None)
        return ids
    except Exception as exc:
        log.warning("doc_narrow | failed: %s", exc)
        return []


async def resolve_query_aliases(
        postgres: PostgresClient,
        tenant_id: str,
        entities: list[str],
) -> list[str]:
    """Resolve query entities through the learned KBEntityLookup alias table so a
    Vietnamese synonym in the query matches the stored canonical entity
    ("sài_gòn" → "thành_phố_hồ_chí_minh"). Unknown entities pass through unchanged.
    Tenant-scoped rows win over global (tenant_id NULL) seed aliases."""
    if not entities:
        return entities
    from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
        KBEntityLookupORM,
    )
    try:
        tid = _uuid.UUID(tenant_id)
    except (ValueError, AttributeError):
        tid = None
    try:
        async with postgres.get_client() as session:
            rows = (await session.execute(
                sa_select(
                    KBEntityLookupORM.alias_name,
                    KBEntityLookupORM.canonical_name,
                    KBEntityLookupORM.tenant_id,
                )
                .where(KBEntityLookupORM.alias_name.in_(entities))
                .where(KBEntityLookupORM.is_deleted.is_(False))
            )).all()
    except Exception as exc:
        log.warning("alias_resolve | failed: %s", exc)
        return entities
    mapping: dict[str, str] = {}
    for alias, canonical, row_tid in rows:
        if row_tid == tid:
            mapping[alias] = canonical
        elif row_tid is None:
            mapping.setdefault(alias, canonical)
    resolved = list(dict.fromkeys(mapping.get(e, e) for e in entities))
    if mapping:
        log.info("alias_resolve | %d/%d query entities resolved via alias table",
                 len(mapping), len(entities))
    return resolved


# ═══════════════════════════════════════════════════════════════════════════════
# STEP — Lexical (BM25 / full-text) retrieval — the sparse branch of hybrid search
# ═══════════════════════════════════════════════════════════════════════════════

async def lexical_search(
        postgres: PostgresClient,
        query: str,
        tenant_id: str,
        limit: int,
        knowledge_base: str | None = None,
        ts_config: str = "simple",
) -> list[dict[str, Any]]:
    """BM25-style lexical retrieval over the active chunk text via Postgres full-text
    search — the sparse branch of hybrid retrieval. Recovers exact keyword / proper-
    noun matches that dense embeddings under-rank ("Nghị định 13", a part number, a
    person's name) — the strongest single 2025 recommendation for keyword queries.

    Returns hits shaped exactly like `qdrant_search` (point_id = block_id, payload
    carries block_id + a text snippet for the reranker) so they slot straight into
    the RRF fusion; ts_rank_cd is the lexical relevance but RRF only consumes rank
    position. Tenant- (and knowledge_base-, when given) scoped via the KBData join —
    the same isolation the vector passes enforce, independently.
    """
    if not query.strip():
        return []
    try:
        tenant_uuid = _uuid.UUID(tenant_id)
    except (ValueError, AttributeError):
        return []
    tsquery = func.websearch_to_tsquery(ts_config, query)
    tsvector = func.to_tsvector(ts_config, KBTextBlockVersionORM.content)
    rank = func.ts_rank_cd(tsvector, tsquery)
    stmt = (
        sa_select(KBTextBlockORM.block_id, rank.label("score"), KBTextBlockVersionORM.content)
        .join(KBTextBlockVersionORM, KBTextBlockVersionORM.block_id == KBTextBlockORM.block_id)
        .join(KBDataORM, KBDataORM.data_id == KBTextBlockORM.owner_id)
        .where(tsvector.op("@@")(tsquery))
        .where(KBTextBlockVersionORM.is_active.is_(True))
        .where(KBTextBlockVersionORM.is_deleted.is_(False))
        .where(KBDataORM.tenant_id == tenant_uuid)
        .where(KBDataORM.is_deleted.is_(False))
        .order_by(rank.desc())
        .limit(limit)
    )
    if knowledge_base:
        try:
            stmt = stmt.where(KBDataORM.knowledge_base_id == _uuid.UUID(knowledge_base))
        except ValueError:
            pass  # non-UUID kb tag → skip the defense-in-depth filter
    try:
        async with postgres.get_client() as session:
            rows = (await session.execute(stmt)).all()
    except Exception as exc:
        log.warning("lexical_search | failed: %s", exc)
        return []
    hits = [
        {
            "point_id": str(block_id),
            "score": float(score or 0.0),
            "payload": {"block_id": str(block_id), "summary": (content or "")[:500]},
        }
        for block_id, score, content in rows
    ]
    log.info("lexical_search | %d lexical hits", len(hits))
    return hits


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 4 — Entity Ranking + Neo4j Graph Expansion
# ═══════════════════════════════════════════════════════════════════════════════

def _rank_entities(pass1_results: list[list[dict[str, Any]]], top_n: int) -> list[str]:
    """Score entities extracted from Qdrant pass1 results by frequency × relevance score.

    Entities that appear in many high-scoring results are ranked highest and
    used as graph expansion seeds — limiting starting nodes avoids an
    exponentially growing neighbourhood set.
    """
    freq: dict[str, float] = {}
    for hits in pass1_results:
        for h in hits:
            score = float(h.get("score") or 1.0)
            for ent in (h.get("payload") or {}).get("entities", []):
                if isinstance(ent, str) and ent.strip():
                    freq[ent] = freq.get(ent, 0.0) + score
    return sorted(freq, key=lambda e: freq[e], reverse=True)[:top_n]


async def graph_expand(
        driver: AsyncDriver,
        entity_names: list[str],
        tenant_id: str,
        max_hops: int,
        max_neighbours: int,
        query_vec: list[float],
        knowledge_base: str | None = None,
        preferred_types: list[str] | None = None,
) -> list[str]:
    """Find neighbour entity names ranked by Neo4j vector similarity to the query.

    All seeds are expanded in ONE batched query (UNWIND) instead of one round-trip per
    seed. Traversal is restricted to the :RELATES edges gold creates, scoped to the
    tenant (and knowledge_base when given). vector.similarity.cosine() (Neo4j 5.x
    built-in) scores neighbours inside Cypher so no vectors are pulled back to Python;
    entities without a stored vector are excluded via the IS NOT NULL guard.
    """
    if not entity_names:
        return []

    kb_clause = " AND s.knowledge_base = $kb AND nb.knowledge_base = $kb" if knowledge_base else ""
    # One global ranking across all seeds; budget scales with the number of seeds so
    # batching doesn't shrink recall versus the old per-seed limit.
    lim = max_neighbours * len(entity_names)
    # Answer-type boost: neighbours whose entity_type matches the question shape
    # rank higher (soft — added to cosine, never a hard filter).
    cypher = (
        f"UNWIND $names AS sname "
        f"MATCH (s:Entity {{canonical_name: sname, tenant_id: $tid}})"
        f"-[:RELATES*1..{max_hops}]-(nb:Entity) "
        f"WHERE nb.canonical_name <> sname AND nb.tenant_id = $tid AND nb.vector IS NOT NULL"
        f"{kb_clause} "
        f"WITH DISTINCT nb, vector.similarity.cosine(nb.vector, $qvec) "
        f"  + (CASE WHEN nb.entity_type IN $ptypes THEN $tboost ELSE 0.0 END) AS score "
        f"ORDER BY score DESC "
        f"LIMIT $lim "
        f"RETURN nb.canonical_name AS name"
    )

    seen: set[str] = {e.lower() for e in entity_names}
    expanded: list[str] = []
    try:
        async with driver.session() as session:
            result = await session.run(
                cypher, names=entity_names, tid=tenant_id,
                qvec=query_vec, lim=lim, kb=knowledge_base,
                ptypes=preferred_types or [], tboost=cfg.TYPE_BOOST,
            )
            records = await result.data()
        for r in records:
            nb_name = r.get("name")
            if isinstance(nb_name, str) and nb_name.strip() and nb_name.lower() not in seen:
                seen.add(nb_name.lower())
                expanded.append(nb_name)
    except Exception as exc:
        log.warning("graph_expand | failed: %s", exc)

    log.info("graph_expand | %d seeds → %d neighbours", len(entity_names), len(expanded))
    return expanded


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 8 — RRF Merge
# ═══════════════════════════════════════════════════════════════════════════════

def rrf_merge(ranked_lists: list[list[dict[str, Any]]], top_n: int) -> list[dict[str, Any]]:
    scores: dict[str, float] = {}
    meta: dict[str, dict[str, Any]] = {}
    for result_list in ranked_lists:
        for rank, hit in enumerate(result_list, start=1):
            pid = hit.get("point_id")
            if not pid:
                continue
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (cfg.RRF_K + rank)
            meta.setdefault(pid, hit)
    sorted_ids = sorted(scores, key=lambda x: scores[x], reverse=True)
    merged = [
        {**meta[pid], "rrf_score": scores[pid], "rrf_rank": i}
        for i, pid in enumerate(sorted_ids[:top_n], start=1)
    ]
    log.info("rrf | %d lists → %d unique → top %d", len(ranked_lists), len(scores), len(merged))
    return merged


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 9 — Reranking (cross-encoder/ms-marco-MiniLM-L-6-v2 via model-service)
# Scores summaries stored in Qdrant — actual text is hydrated in step 11.
# ═══════════════════════════════════════════════════════════════════════════════

async def rerank(
        model: ModelServiceClient,
        query: str,
        hits: list[dict[str, Any]],
        top_n: int,
) -> list[dict[str, Any]]:
    if not hits:
        return []
    documents = [(h.get("payload") or {}).get("summary", "") for h in hits]
    non_empty = [(i, d) for i, d in enumerate(documents) if d.strip()]
    if not non_empty:
        return hits[:top_n]
    indices, docs = zip(*non_empty)
    try:
        ranked = await model.rerank(query, list(docs), min(top_n, len(docs)))
        return [
            {
                **hits[indices[r["index"]]],
                # Cross-encoder returns an unbounded logit — squash to a (0,1) score.
                "rerank_score": _sigmoid(r.get("relevance_score", 0.0)),
                "rerank_rank": i,
            }
            for i, r in enumerate(ranked[:top_n], start=1)
        ]
    except Exception as exc:
        log.warning("rerank | failed: %s — using rrf order", exc)
        return hits[:top_n]


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 10 — Postgres Hydration
# Fetches the *active* KBTextBlockVersion text + KBData source info for each
# block_id returned by Qdrant.  block_id is the 1-to-1 key between Qdrant and
# the KBTextBlock table in Postgres.
# ═══════════════════════════════════════════════════════════════════════════════

async def _fetch_chunks_postgres(
        postgres: PostgresClient,
        block_ids: list[str],
        tenant_id: str,
        knowledge_base: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Return a dict keyed by block_id (str) with the active version's text + source info.

    One batched query (block_id IN …) joins KBTextBlock → active KBTextBlockVersion → KBData,
    scoped to the tenant (and the knowledge base, as defense-in-depth, when provided).
    """
    if not block_ids:
        return {}
    rows: dict[str, dict[str, Any]] = {}
    try:
        uuids = [_uuid.UUID(b) for b in block_ids]
        stmt = (
            sa_select(
                KBTextBlockORM.block_id,
                KBTextBlockORM.block_index,
                KBTextBlockVersionORM.content,
                KBTextBlockVersionORM.version_number,
                KBDataORM.data_id,
                KBDataORM.name,
                KBDataORM.source_type,
                KBDataORM.current_tier,
            )
            .join(KBTextBlockVersionORM, KBTextBlockVersionORM.block_id == KBTextBlockORM.block_id)
            .join(KBDataORM, KBDataORM.data_id == KBTextBlockORM.owner_id)
            .where(KBTextBlockORM.block_id.in_(uuids))
            .where(KBTextBlockVersionORM.is_active.is_(True))
            .where(KBTextBlockVersionORM.is_deleted.is_(False))
            .where(KBDataORM.tenant_id == _uuid.UUID(tenant_id))
            .where(KBDataORM.is_deleted.is_(False))
        )
        if knowledge_base:
            try:
                stmt = stmt.where(KBDataORM.knowledge_base_id == _uuid.UUID(knowledge_base))
            except ValueError:
                pass  # non-UUID kb tag → skip the defense-in-depth filter
        async with postgres.get_client() as session:
            result = await session.execute(stmt)
            for r in result.all():
                rows[str(r.block_id)] = {
                    "block_id": str(r.block_id),
                    "block_index": r.block_index,
                    "content": r.content,
                    "version_number": r.version_number,
                    "doc_id": str(r.data_id),
                    "doc_name": r.name,
                    "source_type": r.source_type,
                    "current_tier": r.current_tier,
                }
    except Exception as exc:
        log.warning("_fetch_chunks_postgres | failed: %s", exc)
    return rows


async def _fetch_tables_postgres(
        postgres: PostgresClient,
        block_ids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """Return block_id (str) → list of {table_name: table_description} for the block's
    *active* version. Chunks that carry a table (table_involved) have one; the rest none.

    A single batched query (block_id IN …) joins KBTextTable to the active
    KBTextBlockVersion — tables are stored against the version that produced them.
    """
    if not block_ids:
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    try:
        uuids = [_uuid.UUID(b) for b in block_ids]
        async with postgres.get_client() as session:
            rows = await session.execute(
                sa_select(
                    KBTextBlockVersionORM.block_id,
                    KBTextTableORM.table_name,
                    KBTextTableORM.description,
                )
                .join(KBTextTableORM, KBTextTableORM.version_id == KBTextBlockVersionORM.version_id)
                .where(KBTextBlockVersionORM.block_id.in_(uuids))
                .where(KBTextBlockVersionORM.is_active.is_(True))
                .where(KBTextBlockVersionORM.is_deleted.is_(False))
            )
            for block_id, table_name, description in rows.all():
                result.setdefault(str(block_id), []).append({str(table_name): description or ""})
    except Exception as exc:
        log.warning("_fetch_tables_postgres | failed: %s", exc)
    return result


def _build_result_list(
        final: list[dict[str, Any]],
        pg_rows: dict[str, dict[str, Any]],
        pg_tables: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Merge rerank scores with actual chunk text, Postgres source info, and any tables.

    Each result is a chunk dict: the chunk fields plus a `table` list of
    {table_name: table_description} entries (empty for non-table chunks).
    """
    enriched = []
    for hit in final:
        bid = str((hit.get("payload") or {}).get("block_id") or "")
        pg = pg_rows.get(bid) or {}
        enriched.append({
            "chunk_rank": hit.get("rerank_rank", 0),
            # Always a 0–1 relevance score (sigmoid of the cross-encoder logit; rrf fallback
            # is already a small positive). Clamped as a final guard.
            "chunk_score": _clamp01(hit.get("rerank_score", hit.get("rrf_score", 0.0))),
            # Actual chunk text from active Postgres version; fall back to Qdrant summary
            "chunk_context": pg.get("content") or (hit.get("payload") or {}).get("summary", ""),
            # Tables attached to this chunk: [{table_name: table_description}, ...]
            "table": pg_tables.get(bid, []),
            "source": {
                "block_id": bid,
                "block_index": pg.get("block_index"),
                "doc_id": str(pg.get("doc_id") or ""),
                "doc_name": str(pg.get("doc_name") or ""),
                "source_type": str(pg.get("source_type") or ""),
                "tier": str(pg.get("current_tier") or ""),
            },
        })
    return enriched


# ═══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════

class LightRAGPipeline:
    """Stateless pipeline — all DB clients injected per call.

    Both entry points share ONE implementation, `_run_events` (an async generator
    that yields a progress dict per step; the final event is always
    {"step": "done", ...} or {"step": "error", ...}):
      • run_stream() forwards those events verbatim to the SSE endpoint;
      • run() drains them and returns the final result payload.
    Merging the two removed the earlier run()/run_stream() duplication — and with it
    the drift where the streaming path silently dropped the front-layer
    entities/intent/entity_types query-enhancement and the CANON_OVERLAP path.
    """

    # ── small, single-purpose helpers shared by the generator ─────────────────

    @staticmethod
    async def _cache_hit_results(
            postgres: PostgresClient, hit: dict[str, Any], tenant_id: str,
            knowledge_base: str | None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Hydrate the block_ids a semantic-cache hit points at, fresh from Postgres.
        Returns (results, hydrated_block_count)."""
        block_refs = hit.get("block_refs") or []
        block_ids = [r["block_id"] for r in block_refs if r.get("block_id")]
        pg_rows, pg_tables = await asyncio.gather(
            _fetch_chunks_postgres(postgres, block_ids, tenant_id, knowledge_base),
            _fetch_tables_postgres(postgres, block_ids),
        )
        cached_final = [
            {"payload": {"block_id": r["block_id"]},
             "rerank_rank": i, "rerank_score": r.get("score", 0.0)}
            for i, r in enumerate(block_refs, start=1) if r.get("block_id")
        ]
        return _build_result_list(cached_final, pg_rows, pg_tables), len(pg_rows)

    @staticmethod
    def _derive_query_entities(canonical: dict[str, Any]) -> list[str]:
        """Canonicalized, de-duplicated entity list from a canonicalize result — the
        flat entities plus the endpoints of any relationships."""
        return list(dict.fromkeys(
            _canon(e)
            for e in [*canonical.get("entities", []),
                      *_entities_from_relationships(canonical.get("relationships", []))]
            if e and str(e).strip()
        ))

    @staticmethod
    async def _typed_pass_lists(
            qdrant: AsyncQdrantClient, collection: str, query_vec: list[float],
            tenant_id: str, knowledge_base: str | None,
            query_entities: list[str], canon_entity_types: dict[str, str],
            neo4j_entities: list[str], ptypes: list[str],
    ) -> list[list[dict[str, Any]]]:
        """Type-aware pass: search each entity in its OWN ent_<type> payload field.
        Front-layer typed entities are grouped by their declared type; the answer-type
        hints (ptypes) add extra typed searches over all candidate names. Returns one
        ranked list per typed search (possibly empty). Bounds from config."""
        if not cfg.TYPE_AWARE:
            return []
        searches = []
        groups: dict[str, list[str]] = {}
        for name in query_entities:
            t = canon_entity_types.get(name)
            if t:
                groups.setdefault(t, []).append(name)
        for t, names in list(groups.items())[:cfg.MAX_TYPED_GROUPS]:
            searches.append(qdrant_search(
                qdrant, collection, query_vec, tenant_id,
                limit=cfg.QDRANT_PASS2_LIMIT, knowledge_base=knowledge_base,
                typed_field=(f"ent_{t}", names),
            ))
        typed_names = list(dict.fromkeys([*neo4j_entities, *query_entities]))
        if typed_names:
            for t in ptypes[:cfg.MAX_PREFERRED_TYPES]:
                if t not in groups:
                    searches.append(qdrant_search(
                        qdrant, collection, query_vec, tenant_id,
                        limit=cfg.QDRANT_PASS2_LIMIT, knowledge_base=knowledge_base,
                        typed_field=(f"ent_{t}", typed_names),
                    ))
        if not searches:
            return []
        return list(await asyncio.gather(*searches))

    @staticmethod
    def _split_pass1_filter(
            query_entities: list[str], canon_entity_types: dict[str, str],
    ) -> tuple[list[str], dict[str, list[str]]]:
        """Route front-layer query entities to the right pass-1 payload field:
        typed names → their ent_<type> group (when TYPE_AWARE), the rest → the
        generic `entities` field. Returns (untyped_names, {type: [names]})."""
        untyped: list[str] = []
        groups: dict[str, list[str]] = {}
        for n in query_entities:
            t = canon_entity_types.get(n)
            if cfg.TYPE_AWARE and t:
                groups.setdefault(t, []).append(n)
            else:
                untyped.append(n)
        return untyped, groups

    @staticmethod
    def _graph_seeds(pass1: list[dict[str, Any]], query_entities: list[str]) -> list[str]:
        """Graph-expansion seeds: top entities from pass-1 (frequency×score) UNION
        the query's own entities, capped at NEO4J_TOP_ENTITIES + GRAPH_SEED_MARGIN."""
        return list(dict.fromkeys([
            *_rank_entities([pass1], cfg.NEO4J_TOP_ENTITIES),
            *query_entities,
        ]))[:cfg.NEO4J_TOP_ENTITIES + cfg.GRAPH_SEED_MARGIN]

    # ── the one true pipeline ─────────────────────────────────────────────────

    async def _run_events(
            self,
            query: str,
            tenant_id: str,
            qdrant: AsyncQdrantClient,
            neo4j: AsyncDriver,
            model: ModelServiceClient,
            postgres: PostgresClient,
            collection: str = cfg.QDRANT_COLLECTION,
            knowledge_base: str | None = None,
            entities: list[str] | None = None,
            intent: str | None = None,
            entity_types: dict[str, str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Async generator yielding one dict per pipeline step — the single
        implementation behind both run() and run_stream(). Each dict has at least
        {"step": "<name>", "ms": float}; the final event is {"step": "done", ...}
        or {"step": "error", ...}.

        `entities` / `intent` / `entity_types` are the front-layer query-enhancement
        INSERT fields: when entities are provided the internal canonicalize LLM call
        is skipped and they filter pass-1 (MatchAny, unfiltered fallback) + seed the
        graph; entity_types (name → person/location/…) point each entity at its own
        ent_<type> payload field. Both entry points expose them, so the streaming
        endpoint no longer loses the enhancement.
        """
        timings: dict[str, float] = {}
        _t0 = time.perf_counter()

        def _ms(start: float) -> float:
            return round((time.perf_counter() - start) * 1000, 1)

        try:
            # ── 1. Embed query — needed for cache lookup and vector search ──────
            _t = time.perf_counter()
            query_vecs = await model.embed([query])
            query_vec = query_vecs[0] if query_vecs else []
            timings["embed_query_ms"] = _ms(_t)
            yield {"step": "embed_query", "dim": len(query_vec), "ms": timings["embed_query_ms"]}

            # ── 2. Cache lookup BEFORE the expensive canonicalize LLM call ──────
            hit = None
            if query_vec:
                _t = time.perf_counter()
                hit = await cache_lookup(
                    qdrant, cfg.QDRANT_CACHE_COLLECTION,
                    query_vec, tenant_id, cfg.CACHE_THRESHOLD,
                    knowledge_base=knowledge_base,
                )
                timings["cache_lookup_ms"] = _ms(_t)
                yield {"step": "cache_lookup", "hit": bool(hit), "ms": timings["cache_lookup_ms"]}

            if hit:
                _t = time.perf_counter()
                results, hydrated = await self._cache_hit_results(
                    postgres, hit, tenant_id, knowledge_base)
                timings["hydrate_ms"] = _ms(_t)
                yield {"step": "cache_hit", "block_count": hydrated,
                       "ms": timings["hydrate_ms"]}
                timings["total_ms"] = round((time.perf_counter() - _t0) * 1000, 1)
                yield {
                    "step": "done", "source": "cache",
                    "canonical": {"intent": "cached", "entities": [], "relationships": []},
                    "results": results, "timings": timings,
                }
                return

            # ── 3. Query understanding ──────────────────────────────────────────
            #   Front layer gave entities → NO internal LLM call; they filter pass 1
            #   and seed the graph. Otherwise → canonicalize; with CANON_OVERLAP it
            #   overlaps pass-1/doc-narrowing (which only need the embedding).
            canon_task = None
            pass1_filter: list[str] | None = None
            canon_entity_types: dict[str, str] = {}
            _t_canon = time.perf_counter()
            if entities:
                canonical: dict[str, Any] = {"intent": intent or "general",
                             "entities": list(entities), "relationships": []}
                query_entities = list(dict.fromkeys(
                    _canon(e) for e in entities if e and str(e).strip()))
                for name, etype in (entity_types or {}).items():
                    cn, t = _canon(name), str(etype).strip().lower()
                    if cn and t in ENTITY_TYPE_FIELDS:
                        canon_entity_types[cn] = t
                if cfg.QUERY_ALIAS_RESOLVE and query_entities:
                    _t = time.perf_counter()
                    query_entities = await resolve_query_aliases(postgres, tenant_id, query_entities)
                    timings["alias_resolve_ms"] = _ms(_t)
                    # A name rewritten by alias resolution keeps no type claim — the
                    # type was asserted about the original surface form.
                    canon_entity_types = {
                        n: t for n, t in canon_entity_types.items() if n in set(query_entities)}
                pass1_filter = query_entities or None
                yield {"step": "canonicalize", "intent": canonical["intent"],
                       "entities": query_entities, "relationships": [],
                       "source": "front_layer", "ms": 0.0}
            else:
                query_entities = []
                canon_task = asyncio.create_task(canonicalize(model, query))
                if not cfg.CANON_OVERLAP:
                    canonical = await canon_task
                    canon_task = None
                    timings["canonicalize_ms"] = _ms(_t_canon)
                    yield {"step": "canonicalize", "intent": canonical.get("intent", ""),
                           "entities": canonical.get("entities", []),
                           "relationships": canonical.get("relationships", []),
                           "ms": timings["canonicalize_ms"]}

            # ── 4. Qdrant pass 1 — front-layer entities point the filter at the
            #      right payload fields (typed → ent_<type>, untyped → entities);
            #      unfiltered fallback when nothing matches. ───────────────────────
            _t = time.perf_counter()
            pass1_untyped, pass1_groups = self._split_pass1_filter(
                query_entities if pass1_filter else [], canon_entity_types)
            pass1 = await qdrant_search(
                qdrant, collection, query_vec, tenant_id,
                limit=cfg.QDRANT_PASS1_LIMIT,
                entities=pass1_untyped or None,
                typed_entity_groups=pass1_groups or None,
                knowledge_base=knowledge_base,
            )
            if pass1_filter and not pass1:
                log.info("run | entity-filtered pass1 empty — unfiltered fallback")
                pass1 = await qdrant_search(
                    qdrant, collection, query_vec, tenant_id,
                    limit=cfg.QDRANT_PASS1_LIMIT, knowledge_base=knowledge_base,
                )
            timings["qdrant_pass1_ms"] = _ms(_t)
            yield {"step": "qdrant_pass1", "hits": len(pass1), "ms": timings["qdrant_pass1_ms"]}

            # 3-late (internal-canonicalize path only): join the overlapped LLM call,
            # then derive + alias-resolve the query entities for graph seeding.
            if canon_task is not None:
                canonical = await canon_task
                timings["canonicalize_ms"] = _ms(_t_canon)  # wall time incl. overlap
                yield {"step": "canonicalize", "intent": canonical.get("intent", ""),
                       "entities": canonical.get("entities", []),
                       "relationships": canonical.get("relationships", []),
                       "ms": timings["canonicalize_ms"]}
            if not entities:
                query_entities = self._derive_query_entities(canonical)
                if cfg.QUERY_ALIAS_RESOLVE and query_entities:
                    _t = time.perf_counter()
                    query_entities = await resolve_query_aliases(postgres, tenant_id, query_entities)
                    timings["alias_resolve_ms"] = _ms(_t)

            # ── 4b. 2-step document narrowing (env-gated DOC_NARROWING) ──────────
            pass_doc: list[dict[str, Any]] = []
            if cfg.DOC_NARROWING:
                _t = time.perf_counter()
                narrow_ids = await doc_narrow_ids(
                    qdrant, query_vec, tenant_id, knowledge_base,
                    intents=canonical.get("intents"))
                if narrow_ids:
                    pass_doc = await qdrant_search(
                        qdrant, collection, query_vec, tenant_id,
                        limit=cfg.DOC_PASS_CHUNK_LIMIT, knowledge_base=knowledge_base,
                        data_ids=narrow_ids)
                timings["doc_narrow_ms"] = _ms(_t)
                yield {"step": "doc_narrow", "docs": len(narrow_ids), "hits": len(pass_doc),
                       "ms": timings["doc_narrow_ms"]}

            # ── 4c. Lexical (BM25) pass — the sparse branch of hybrid retrieval
            #      (env-gated HYBRID_LEXICAL). Recovers exact keyword / proper-noun
            #      matches; joins the RRF fusion as another ranked list. ────────────
            pass_lex: list[dict[str, Any]] = []
            if cfg.HYBRID_LEXICAL:
                _t = time.perf_counter()
                pass_lex = await lexical_search(
                    postgres, query, tenant_id, cfg.LEXICAL_LIMIT,
                    knowledge_base=knowledge_base, ts_config=cfg.LEXICAL_TS_CONFIG)
                timings["lexical_ms"] = _ms(_t)
                yield {"step": "lexical", "hits": len(pass_lex), "ms": timings["lexical_ms"]}

            # ── 5. Graph-expansion seeds + answer-type hints ────────────────────
            _t = time.perf_counter()
            top_entities = self._graph_seeds(pass1, query_entities)
            ptypes = preferred_entity_types(query, canonical.get("intent", "")) if cfg.TYPE_AWARE else []
            timings["rank_entities_ms"] = _ms(_t)
            log.info("run | graph seeds: %r preferred_types: %r", top_entities, ptypes)
            yield {"step": "rank_entities", "seeds": top_entities,
                   "preferred_types": ptypes, "ms": timings["rank_entities_ms"]}

            # ── 6. Neo4j graph expansion ────────────────────────────────────────
            _t = time.perf_counter()
            neo4j_entities: list[str] = []
            if top_entities:
                try:
                    neo4j_entities = await graph_expand(
                        neo4j, top_entities, tenant_id,
                        cfg.NEO4J_MAX_HOPS, cfg.NEO4J_MAX_NEIGHBOURS,
                        query_vec, knowledge_base=knowledge_base, preferred_types=ptypes)
                except Exception as exc:
                    log.warning("run | graph_expand failed: %s", exc)
            timings["graph_expand_ms"] = _ms(_t)
            yield {"step": "graph_expand", "seeds": len(top_entities),
                   "expanded": len(neo4j_entities),
                   "neo4j_entities": neo4j_entities[:cfg.GRAPH_PREVIEW_LIMIT],
                   "ms": timings["graph_expand_ms"]}

            # ── 7. Qdrant pass 2 — expanded Neo4j entities ──────────────────────
            _t = time.perf_counter()
            pass2: list[dict[str, Any]] = []
            if neo4j_entities:
                pass2 = await qdrant_search(
                    qdrant, collection, query_vec, tenant_id,
                    limit=cfg.QDRANT_PASS2_LIMIT, entities=neo4j_entities,
                    knowledge_base=knowledge_base)
            timings["qdrant_pass2_ms"] = _ms(_t)
            yield {"step": "qdrant_pass2", "hits": len(pass2), "ms": timings["qdrant_pass2_ms"]}

            # ── 7b. Typed pass (env-gated TYPE_AWARE) ───────────────────────────
            _t = time.perf_counter()
            typed_lists = await self._typed_pass_lists(
                qdrant, collection, query_vec, tenant_id, knowledge_base,
                query_entities, canon_entity_types, neo4j_entities, ptypes)
            timings["typed_pass_ms"] = _ms(_t)
            if typed_lists:
                yield {"step": "typed_pass", "types": ptypes[:cfg.MAX_PREFERRED_TYPES],
                       "hits": sum(len(l) for l in typed_lists), "ms": timings["typed_pass_ms"]}

            # ── 8. RRF merge — dense (pass1+pass2+typed+doc) + sparse (lexical) ──
            _t = time.perf_counter()
            rrf_top = rrf_merge([pass1, pass2, *typed_lists, pass_doc, pass_lex], top_n=cfg.RRF_TOP_N)
            timings["rrf_merge_ms"] = _ms(_t)
            yield {"step": "rrf_merge", "unique": len(rrf_top), "ms": timings["rrf_merge_ms"]}

            # ── 9. Rerank by cross-encoder on stored summaries ──────────────────
            _t = time.perf_counter()
            final = await rerank(model, query, rrf_top, cfg.RERANK_TOP_N)
            timings["rerank_ms"] = _ms(_t)
            yield {"step": "rerank", "top_n": len(final), "ms": timings["rerank_ms"]}

            # ── 10. Hydrate active chunk text/source + tables from Postgres ─────
            block_ids = [
                str((h.get("payload") or {}).get("block_id") or "")
                for h in final if (h.get("payload") or {}).get("block_id")
            ]
            _t = time.perf_counter()
            pg_rows, pg_tables = await asyncio.gather(
                _fetch_chunks_postgres(postgres, block_ids, tenant_id, knowledge_base),
                _fetch_tables_postgres(postgres, block_ids),
            )
            results = _build_result_list(final, pg_rows, pg_tables)
            timings["hydrate_ms"] = _ms(_t)
            yield {"step": "hydrate", "block_count": len(pg_rows), "ms": timings["hydrate_ms"]}

            # ── 11. Cache the post-rerank decision (block_ids + scores) ─────────
            _t = time.perf_counter()
            if query_vec and results:
                await cache_store(
                    qdrant, cfg.QDRANT_CACHE_COLLECTION,
                    query_vec, tenant_id, knowledge_base, results)
            timings["cache_store_ms"] = _ms(_t)
            yield {"step": "cache_store", "stored": bool(query_vec and results),
                   "ms": timings["cache_store_ms"]}

            timings["total_ms"] = round((time.perf_counter() - _t0) * 1000, 1)
            log.info("run | query=%r kb=%s rrf=%d hydrated=%d total=%.0fms",
                     query[:60], knowledge_base, len(rrf_top), len(results), timings["total_ms"])
            yield {"step": "done", "source": "pipeline",
                   "canonical": canonical, "results": results, "timings": timings}

        except Exception as exc:
            log.exception("_run_events | unhandled error: %s", exc)
            yield {"step": "error", "message": str(exc)[:300]}

    async def run_stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        """SSE entry point — forwards every progress event from the shared
        generator, front-layer query-enhancement fields (entities/intent/
        entity_types) included."""
        async for event in self._run_events(*args, **kwargs):
            yield event

    async def run(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Blocking entry point — drains the shared generator and returns the final
        result payload {source, canonical, results, timings}. Accepts the same
        arguments as _run_events (front-layer entities/intent/entity_types included).
        Raises RuntimeError if the pipeline ended in an error event (the router maps
        it to a 500), preserving the old fail-fast contract."""
        final: dict[str, Any] = {}
        async for event in self._run_events(*args, **kwargs):
            step = event.get("step")
            if step == "error":
                raise RuntimeError(event.get("message", "pipeline error"))
            if step == "done":
                final = event
        return {
            "source": final.get("source", "pipeline"),
            "canonical": final.get(
                "canonical", {"intent": "general", "entities": [], "relationships": []}),
            "results": final.get("results", []),
            "timings": final.get("timings", {}),
        }
