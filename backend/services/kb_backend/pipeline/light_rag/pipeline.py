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

from services.kb_backend.operations.access import visible_role_ids
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
        "intents": [str(i) for i in (intents if isinstance(intents, list) else []) if str(i).strip()],
        "entities": [str(e) for e in (entities if isinstance(entities, list) else [])],
        "relationships": result.get("relationships", []),
    }


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
    s = unicodedata.normalize("NFC", str(name)).casefold().strip()
    s = re.sub(r"[\s\-/\\]+", "_", s)
    s = re.sub(r"[^\w]", "", s)
    return re.sub(r"_+", "_", s).strip("_")


ENTITY_TYPE_FIELDS = frozenset({
    "person", "organization", "location", "law_document",
    "system_product", "event", "time_period", "concept",
})

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
    out: list[str] = []
    for rel in relationships or []:
        if isinstance(rel, (list, tuple)) and len(rel) >= 3:
            for e in (rel[0], rel[2]):
                if isinstance(e, str) and e.strip():
                    out.append(e.strip())
    return out


async def purge_expired_cache(qdrant: AsyncQdrantClient, collection: str) -> int:
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
        qdrant_connector: Any,
        collection: str = cfg.QDRANT_CACHE_COLLECTION,
        interval: int = cfg.CACHE_SWEEP_INTERVAL,
) -> None:
    """Takes the connector, not a client: a reconnect replaces the client."""
    log.info("cache | sweep loop started (interval=%ds, ttl=%ds)", interval, cfg.CACHE_TTL_SECONDS)
    while True:
        await asyncio.sleep(interval)
        try:
            await purge_expired_cache(qdrant_connector.get_client(), collection)
        except Exception as exc:
            log.warning("cache | sweep loop error: %s", exc)


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


async def lexical_search(
        postgres: PostgresClient,
        query: str,
        tenant_id: str,
        limit: int,
        knowledge_base: str | None = None,
        ts_config: str = "simple",
) -> list[dict[str, Any]]:
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


def _rank_entities(pass1_results: list[list[dict[str, Any]]], top_n: int) -> list[str]:
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
    if not entity_names:
        return []

    kb_clause = " AND s.knowledge_base = $kb AND nb.knowledge_base = $kb" if knowledge_base else ""
    lim = max_neighbours * len(entity_names)
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


async def rrf_fuse(ranked_lists: list[list[dict[str, Any]]], top_n: int) -> list[dict[str, Any]]:
    return rrf_merge(ranked_lists, top_n)


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
                "rerank_score": _sigmoid(r.get("relevance_score", 0.0)),
                "rerank_rank": i,
            }
            for i, r in enumerate(ranked[:top_n], start=1)
        ]
    except Exception as exc:
        log.warning("rerank | failed: %s — using rrf order", exc)
        return hits[:top_n]


async def _fetch_chunks_postgres(
        postgres: PostgresClient,
        block_ids: list[str],
        tenant_id: str,
        knowledge_base: str | None = None,
        role_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Chunk text for the retrieved block ids, scoped to what the caller may see.

    `role_id` applies the same document-level hierarchy that
    operations/access.py enforces on the document APIs
    (PLATFORM_ADMIN > AI_ENGINEER > EXECUTIVE > BUSINESS_OPERATOR). Retrieval
    used to skip it entirely: the hierarchy existed, the document listing
    honoured it, and this path — the one every agent answer is built from —
    joined KBData without ever looking at role_id. A business operator's
    question could return chunks from a document only a platform admin was
    allowed to open.

    This is the right choke point because BOTH retrieval paths pass through it:
    a fresh search and a semantic-cache hit (_cache_hit_results) both resolve
    block ids to text here, so a cache entry written for a privileged caller is
    re-filtered for whoever reads it rather than replayed wholesale.
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
        if role_id:
            # Same visibility rule as access.document_role_filter, expressed
            # against the ORM rather than the WhereFilter model the document
            # APIs use. Imported from there so the two cannot drift: a change to
            # the hierarchy has to move both paths at once.
            #
            # visible_role_ids returns the role unchanged when it is not one of
            # the four ranked ids — which for an unmapped role is a NAME, not a
            # UUID. Converting that would raise inside the try below and be
            # swallowed into "fetch failed", which happens to fail closed but
            # reads like a pipeline fault. Made explicit instead: an id that
            # cannot name a document tier sees no documents.
            visible: list[_uuid.UUID] = []
            for rid in visible_role_ids(role_id):
                try:
                    visible.append(_uuid.UUID(rid))
                except (ValueError, AttributeError, TypeError):
                    log.warning(
                        "_fetch_chunks_postgres | role_id %r is not a known document tier — "
                        "no documents are visible to it", rid,
                    )
            if not visible:
                return {}
            stmt = stmt.where(KBDataORM.role_id.in_(visible))
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
        role_filtered: bool = False,
) -> list[dict[str, Any]]:
    """Assemble the response, dropping anything the caller may not see.

    `role_filtered` says the Postgres fetch above applied a role scope, which
    makes a MISSING row meaningful rather than incidental: the block belongs to
    a document above the caller's tier. Those hits are dropped here.

    Without that, filtering the fetch alone would have leaked anyway — the
    content line below falls back to the Qdrant payload's `summary` when there
    is no Postgres row, and that summary is derived from the very document the
    filter just refused. A vector index is not access-controlled storage; only
    the relational join knows who may read what.
    """
    enriched = []
    for hit in final:
        bid = str((hit.get("payload") or {}).get("block_id") or "")
        pg = pg_rows.get(bid) or {}
        if role_filtered and not pg:
            continue
        enriched.append({
            "chunk_rank": hit.get("rerank_rank", 0),
            "chunk_score": _clamp01(hit.get("rerank_score", hit.get("rrf_score", 0.0))),
            "chunk_context": pg.get("content") or (hit.get("payload") or {}).get("summary", ""),
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


class LightRAGPipeline:
    @staticmethod
    async def _cache_hit_results(
            postgres: PostgresClient, hit: dict[str, Any], tenant_id: str,
            knowledge_base: str | None, role_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        block_refs = hit.get("block_refs") or []
        block_ids = [r["block_id"] for r in block_refs if r.get("block_id")]
        pg_rows, pg_tables = await asyncio.gather(
            _fetch_chunks_postgres(postgres, block_ids, tenant_id, knowledge_base, role_id),
            _fetch_tables_postgres(postgres, block_ids),
        )
        cached_final = [
            {"payload": {"block_id": r["block_id"]},
             "rerank_rank": i, "rerank_score": r.get("score", 0.0)}
            for i, r in enumerate(block_refs, start=1) if r.get("block_id")
        ]
        return _build_result_list(
            cached_final, pg_rows, pg_tables, role_filtered=bool(role_id),
        ), len(pg_rows)

    @staticmethod
    def _derive_query_entities(canonical: dict[str, Any]) -> list[str]:
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
        return list(dict.fromkeys([
            *_rank_entities([pass1], cfg.NEO4J_TOP_ENTITIES),
            *query_entities,
        ]))[:cfg.NEO4J_TOP_ENTITIES + cfg.GRAPH_SEED_MARGIN]

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
            role_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        timings: dict[str, float] = {}
        _t0 = time.perf_counter()

        def _ms(start: float) -> float:
            return round((time.perf_counter() - start) * 1000, 1)

        degraded: list[str] = []

        def _over_budget() -> bool:
            return cfg.DEGRADE_BUDGET_MS > 0 and _ms(_t0) > cfg.DEGRADE_BUDGET_MS

        try:
            _t = time.perf_counter()
            query_vecs = await model.embed([query])
            query_vec = query_vecs[0] if query_vecs else []
            timings["embed_query_ms"] = _ms(_t)
            yield {"step": "embed_query", "dim": len(query_vec), "ms": timings["embed_query_ms"]}

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
                    postgres, hit, tenant_id, knowledge_base, role_id)
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

            if canon_task is not None:
                canonical = await canon_task
                timings["canonicalize_ms"] = _ms(_t_canon)
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

            pass_lex: list[dict[str, Any]] = []
            if cfg.HYBRID_LEXICAL:
                _t = time.perf_counter()
                pass_lex = await lexical_search(
                    postgres, query, tenant_id, cfg.LEXICAL_LIMIT,
                    knowledge_base=knowledge_base, ts_config=cfg.LEXICAL_TS_CONFIG)
                timings["lexical_ms"] = _ms(_t)
                yield {"step": "lexical", "hits": len(pass_lex), "ms": timings["lexical_ms"]}

            _t = time.perf_counter()
            top_entities = self._graph_seeds(pass1, query_entities)
            ptypes = preferred_entity_types(query, canonical.get("intent", "")) if cfg.TYPE_AWARE else []
            timings["rank_entities_ms"] = _ms(_t)
            log.info("run | graph seeds: %r preferred_types: %r", top_entities, ptypes)
            yield {"step": "rank_entities", "seeds": top_entities,
                   "preferred_types": ptypes, "ms": timings["rank_entities_ms"]}

            _t = time.perf_counter()
            neo4j_entities: list[str] = []
            if top_entities and not _over_budget():
                try:
                    neo4j_entities = await graph_expand(
                        neo4j, top_entities, tenant_id,
                        cfg.NEO4J_MAX_HOPS, cfg.NEO4J_MAX_NEIGHBOURS,
                        query_vec, knowledge_base=knowledge_base, preferred_types=ptypes)
                except Exception as exc:
                    log.warning("run | graph_expand failed: %s", exc)
            elif top_entities:
                degraded.append("graph_expand")  # over budget → skip Neo4j
            timings["graph_expand_ms"] = _ms(_t)
            yield {"step": "graph_expand", "seeds": len(top_entities),
                   "expanded": len(neo4j_entities), "degraded": "graph_expand" in degraded,
                   "neo4j_entities": neo4j_entities[:cfg.GRAPH_PREVIEW_LIMIT],
                   "ms": timings["graph_expand_ms"]}

            _t = time.perf_counter()
            pass2: list[dict[str, Any]] = []
            if neo4j_entities:
                pass2 = await qdrant_search(
                    qdrant, collection, query_vec, tenant_id,
                    limit=cfg.QDRANT_PASS2_LIMIT, entities=neo4j_entities,
                    knowledge_base=knowledge_base)
            timings["qdrant_pass2_ms"] = _ms(_t)
            yield {"step": "qdrant_pass2", "hits": len(pass2), "ms": timings["qdrant_pass2_ms"]}

            _t = time.perf_counter()
            typed_lists = await self._typed_pass_lists(
                qdrant, collection, query_vec, tenant_id, knowledge_base,
                query_entities, canon_entity_types, neo4j_entities, ptypes)
            timings["typed_pass_ms"] = _ms(_t)
            if typed_lists:
                yield {"step": "typed_pass", "types": ptypes[:cfg.MAX_PREFERRED_TYPES],
                       "hits": sum(len(l) for l in typed_lists), "ms": timings["typed_pass_ms"]}

            _t = time.perf_counter()
            rrf_top = await rrf_fuse([pass1, pass2, *typed_lists, pass_doc, pass_lex], top_n=cfg.RRF_TOP_N)
            timings["rrf_merge_ms"] = _ms(_t)
            yield {"step": "rrf_merge", "unique": len(rrf_top), "ms": timings["rrf_merge_ms"]}

            _t = time.perf_counter()
            if _over_budget():
                final = rrf_top[:cfg.RERANK_TOP_N]  # skip cross-encoder; keep RRF order
                degraded.append("rerank")
            else:
                final = await rerank(model, query, rrf_top, cfg.RERANK_TOP_N)
            timings["rerank_ms"] = _ms(_t)
            yield {"step": "rerank", "top_n": len(final), "degraded": "rerank" in degraded,
                   "ms": timings["rerank_ms"]}

            block_ids = [
                str((h.get("payload") or {}).get("block_id") or "")
                for h in final if (h.get("payload") or {}).get("block_id")
            ]
            _t = time.perf_counter()
            pg_rows, pg_tables = await asyncio.gather(
                _fetch_chunks_postgres(postgres, block_ids, tenant_id, knowledge_base, role_id),
                _fetch_tables_postgres(postgres, block_ids),
            )
            results = _build_result_list(final, pg_rows, pg_tables, role_filtered=bool(role_id))
            timings["hydrate_ms"] = _ms(_t)
            yield {"step": "hydrate", "block_count": len(pg_rows), "ms": timings["hydrate_ms"]}

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
            yield {"step": "done", "source": "pipeline", "degraded": degraded,
                   "canonical": canonical, "results": results, "timings": timings}

        except Exception as exc:
            log.exception("_run_events | unhandled error: %s", exc)
            yield {"step": "error", "message": str(exc)[:300]}


    async def run(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
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
            "degraded": final.get("degraded", []),
        }
