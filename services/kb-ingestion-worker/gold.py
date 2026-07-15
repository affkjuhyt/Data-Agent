"""
Gold stage — document-level promotion.

promote_to_gold is decomposed into step functions; the emitter.step wrapper emits
each step's start/complete/error so no SSE event is written by hand in the flow.

Per chunk (_process_chunk):
  1. Embed the chunk text (silver already produced summary + intents).
  2. Conflict detect (_detect_and_record_conflict): nearest-neighbour search; if the
     LLM confirms a conflict, record it PENDING and skip the chunk.
  3. Atomic commit (_commit_chunk_atomic): Postgres block+version + Qdrant chunk
     point, committed together (Qdrant point rolled back if Postgres fails).
  4. Canonicalize the chunk's entities + add its bridges to the graph
     (GraphStore.add_chunk), keyed by block_id for provenance.

Per document:
  5. Reduce chunk summaries → ONE document summary (_reduce_document_summary).
  6. Upsert the doc-summary point LAST (_write_doc_summary_point) — a document absent
     from the doc collection is invisible to 2-step retrieval, so this is the
     transaction boundary.
  7. Flip KBData tier to GOLD (_update_document_tier).

A failure in steps 5–7 leaves committed chunks but no doc point and no GOLD tier;
re-promotion first cleans up the previous partial state, so retry is safe.
"""
from __future__ import annotations

import json
import logging
import re
import time as _time
import unicodedata
import uuid
from collections import Counter
from typing import Any

from qdrant_client import models as qmodels
from qdrant_client.async_qdrant_client import AsyncQdrantClient
from sqlalchemy import cast as sa_cast, func as sa_func, update as sa_update
from sqlalchemy.dialects.postgresql import JSONB

from basemodel.services_databaseconnector.postgres_model import (
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
    KBConflictBatchInsert,
    KBConflictInsert,
    KBLifecycleHistoryInsert,
    Tier,
)
from sqlalchemy import select as sa_select
from basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBDataORM,
    KBTextBlockORM,
    KBTextBlockVersionORM,
    KBTextTableORM,
    KBQdrantCollectionORM,
    KBConflictBatchORM,
)
from basemodel.services_databaseconnector.qdrant_model import (
    SearchRequest,
)
from services.database_connector.model_connector import ModelServiceClient
from services.database_connector.mongo_connector import MongoClient
from services.database_connector.postgres_connector import PostgresClient
from services.database_connector.qdrant_connector import (
    vector_search as qdrant_vector_search,
)

from utils import config as cfg
from utils import entity_registry
from utils import llm_client
from utils.entity_types import entities_by_type_fields, to_entity_type as _to_entity_type
from utils.graph_store import GraphStore
from utils.log_writer import set_chunk_progress, write_log
from utils.pipeline_events import NULL_EMITTER, PipelineEventEmitter
from utils.prompts import GOLD_SYSTEM_PROMPTS as SYSTEM_PROMPTS

log = logging.getLogger(__name__)

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
_JSON_OBJ   = re.compile(r"\{[\s\S]*\}", re.DOTALL)

# Words that are never meaningful knowledge-graph entities regardless of context.
# English + Vietnamese (documents are primarily Vietnamese). Compared against the
# NFC-casefolded entity name, so diacritics must be written exactly.
_GENERIC_BLACKLIST: frozenset[str] = frozenset({
})


def _fold(s: str) -> str:
    """NFC + casefold — the comparison form for blacklist / dedup checks.
    NFC matters for Vietnamese: composed vs decomposed diacritics must compare equal."""
    return unicodedata.normalize("NFC", s).casefold().strip()


# ── LLM helpers ───────────────────────────────────────────────────────────────
def _json_hint(raw: str, exc: json.JSONDecodeError) -> str:
    msg = str(exc).lower()
    if "unterminated" in msg or (exc.pos and exc.pos >= len(raw) - 5):
        return (
            f"your response was cut off because it was too long — return FEWER items: "
            f"at most {cfg.MAX_DOC_ENTITIES} entities and {cfg.MAX_DOC_RELATIONSHIPS} "
            f"relationships, each with a description under 12 words, so the JSON closes properly"
        )
    if "expecting" in msg or "invalid" in msg:
        return f"syntax error near position {exc.pos} — return only a valid JSON object, no extra text"
    return "response is not valid JSON — return only the JSON object with no markdown or extra text"


async def _llm_generate_json(
    system_prompt: str,
    user: str,
    max_tokens: int = 1024,
    temperature: float = 0.1,
    max_retries: int = 2,
    system_type: str = "",
) -> tuple[dict, str, int]:
    """Returns (parsed_dict, last_raw_response, attempts_used).

    `system_type` is a key of SYSTEM_PROMPTS and of utils.llm_schemas: it selects the
    JSON schema llm_client forwards to the model-service /generate gateway, which the
    served model applies as its response_format for constrained decoding.
    """
    current_user = user
    last_hint = ""

    for attempt in range(max_retries):
        raw = await llm_client.generate_json(
            current_user,
            system_prompt=system_prompt,
            system_type=system_type,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        log.debug("Gold LLM raw attempt %d (first 300): %r", attempt + 1, raw[:300])

        fence_m = _JSON_FENCE.search(raw)
        if fence_m:
            candidate = fence_m.group(1).strip()
        else:
            obj_m = _JSON_OBJ.search(raw)
            candidate = obj_m.group(0) if obj_m else raw.strip()

        candidate = candidate.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")

        try:
            return json.loads(candidate), raw, attempt + 1
        except json.JSONDecodeError as exc:
            last_hint = _json_hint(raw, exc)
            log.warning("Gold LLM JSON invalid (attempt %d/%d): %s", attempt + 1, max_retries, last_hint)
            current_user = f"{user}\n\nNote: {last_hint}."

    raise ValueError(f"LLM failed to return valid JSON after {max_retries} attempts: {last_hint}")


async def verify_merge_pair(name_a: str, desc_a: str, name_b: str, desc_b: str) -> bool:
    """LLM same-entity judgment for gray-band registry merges. Conservative on
    any failure: False (don't merge — a duplicate node is recoverable, a wrong
    merge corrupts the graph permanently)."""
    user = f'A: "{name_a}" — {desc_a or "(không có mô tả)"}\nB: "{name_b}" — {desc_b or "(không có mô tả)"}'
    try:
        result, _, _ = await _llm_generate_json(
            SYSTEM_PROMPTS["merge-verify"], user, max_tokens=16, system_type="merge-verify",
        )
        return bool(result.get("same", False))
    except Exception as exc:
        log.warning("Gold merge-verify: failed (%s) — NOT merging", exc)
        return False



def _to_score(v: Any, default: float = 0.5) -> float:
    """Parse a model-provided score into a clamped 0.0–1.0 float (default if missing)."""
    try:
        s = float(v)
    except (TypeError, ValueError):
        return default
    if s != s:  # NaN
        return default
    return max(0.0, min(1.0, s))


def _to_entity_list(raw_value: Any) -> list[dict[str, Any]]:
    """Parse the doc-entity LLM output: name + type + aliases + description + score.
    Surface forms keep their original diacritics; only comparison uses _fold."""
    if not isinstance(raw_value, list):
        return []
    parsed_entities = []
    seen_names: set[str] = set()
    for item in raw_value:
        if isinstance(item, dict):
            name        = str(item.get("name") or "").strip()
            entity_type = _to_entity_type(item.get("type"))
            description = str(item.get("description") or "").strip()
            score       = _to_score(item.get("score"))
            aliases = [
                alias.strip() for alias in (item.get("aliases") or [])
                if isinstance(alias, str) and alias.strip()
            ]
        elif isinstance(item, str):
            name, entity_type, description, score, aliases = item.strip(), "concept", "", 0.5, []
        else:
            continue
        if not name or len(name) < 2:
            continue
        if _fold(name) in _GENERIC_BLACKLIST:
            log.debug("Gold: filtered generic entity %r", name)
            continue
        # Aliases that are themselves generic words would poison the alias table.
        aliases = [
            alias for alias in aliases
            if _fold(alias) not in _GENERIC_BLACKLIST and _fold(alias) != _fold(name)
        ]
        folded_name = _fold(name)
        if folded_name in seen_names:
            continue
        seen_names.add(folded_name)
        parsed_entities.append({
            "name": name, "type": entity_type, "aliases": aliases,
            "description": description, "score": score,
        })

    all_folded_names = {_fold(entity["name"]) for entity in parsed_entities}
    for entity in parsed_entities:
        kept_aliases = [alias for alias in entity["aliases"] if _fold(alias) not in all_folded_names]
        if len(kept_aliases) != len(entity["aliases"]):
            log.info("Gold: dropped %d same-doc entity name(s) from aliases of %r",
                     len(entity["aliases"]) - len(kept_aliases), entity["name"])
        entity["aliases"] = kept_aliases
    return parsed_entities


# ── Step 2: map-reduce document summary ───────────────────────────────────────

def _batch_texts(texts: list[str], budget_chars: int) -> list[list[str]]:
    """Group texts into consecutive batches of at most budget_chars characters,
    preserving document order (summaries form a narrative — never shuffle)."""
    batches: list[list[str]] = []
    current: list[str] = []
    size = 0
    for t in texts:
        if current and size + len(t) > budget_chars:
            batches.append(current)
            current, size = [], 0
        current.append(t)
        size += len(t)
    if current:
        batches.append(current)
    return batches


async def _reduce_batch(texts: list[str]) -> str:
    """One LLM reduce call over a batch of summaries. Falls back to joining the
    inputs (truncated) if the model can't produce valid JSON."""
    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
    try:
        result, _, _ = await _llm_generate_json(
            SYSTEM_PROMPTS["doc-reduce"],
            numbered,
            # Vietnamese tokenizes at ~2-3 tokens per syllable — a 250-word summary
            # can exceed 512 tokens and truncate mid-JSON, so give it headroom.
            max_tokens=1024,
            system_type="doc-reduce",
        )
        summary = str(result.get("summary") or "").strip()
        if summary:
            return summary
    except Exception as exc:
        log.warning("Gold reduce: batch reduce failed (%s) — joining inputs", exc)
    return " ".join(texts)[: cfg.DOC_SUMMARY_BATCH_CHARS]


async def _reduce_summaries(
    summaries: list[str],
    emitter: PipelineEventEmitter = NULL_EMITTER,
) -> str:
    """Recursively reduce chunk summaries into one document summary.

    Each pass batches the current texts (document order) and reduces every batch
    with one LLM call. Depth is hard-capped: a small model can return output that
    doesn't shrink, and an unbounded "still too long → recurse" loop would never
    terminate. After the cap, hard-truncate.
    """
    texts = [s for s in summaries if s.strip()]
    if not texts:
        return ""

    depth = 0
    while depth < cfg.DOC_SUMMARY_MAX_DEPTH:
        total = sum(len(t) for t in texts)
        if len(texts) == 1 and total <= cfg.DOC_SUMMARY_TARGET_CHARS:
            break
        batches = _batch_texts(texts, cfg.DOC_SUMMARY_BATCH_CHARS)
        await emitter.emit("gold.doc.reduce.step", {
            "depth":   depth,
            "inputs":  len(texts),
            "batches": len(batches),
            "chars":   total,
        })
        reduced = [await _reduce_batch(b) for b in batches]
        # No shrink → another pass won't help; stop and truncate below.
        if sum(len(t) for t in reduced) >= total and len(reduced) == len(texts):
            texts = reduced
            break
        texts = reduced
        depth += 1

    doc_summary = " ".join(texts)
    if len(doc_summary) > cfg.DOC_SUMMARY_TARGET_CHARS:
        log.warning(
            "Gold reduce: summary still %d chars after depth %d — hard truncating to %d",
            len(doc_summary), depth, cfg.DOC_SUMMARY_TARGET_CHARS,
        )
        doc_summary = doc_summary[: cfg.DOC_SUMMARY_TARGET_CHARS]
    return doc_summary

# ── Resolvers (collection model / knowledge base / document name) ─────────────
async def _get_collection_embed_model_id(
    postgres: PostgresClient,
    collection_name: str,
) -> str:
    """Return the embedding_model_id UUID for a named Qdrant collection.

    The model service receives this UUID and resolves the right model itself.
    Falls back to cfg.EMBED_MODEL_ID if the collection has no model set or
    Postgres is unreachable.
    """
    try:
        async with postgres.get_client() as session:
            row = await session.scalar(
                sa_select(KBQdrantCollectionORM.embedding_model_id)
                .where(KBQdrantCollectionORM.collection_name == collection_name)
                .limit(1)
            )
        if row:
            return str(row)
    except Exception as exc:
        log.warning(
            "Gold: could not get embed model_id for collection '%s': %s — using fallback",
            collection_name, exc,
        )
    return cfg.EMBED_MODEL_ID

DEFAULT_KNOWLEDGE_BASE = "General Knowledge Base"


async def _get_data_row(postgres: PostgresClient, data_id: str) -> tuple[str, str]:
    """Return (document_name, knowledge_base) for the KBData document.
    An unscoped document (null knowledge_base_id) resolves to the default bucket
    DEFAULT_KNOWLEDGE_BASE. Name '' if the row is missing."""
    try:
        async with postgres.get_client() as session:
            row = (await session.execute(
                sa_select(KBDataORM.name, KBDataORM.knowledge_base_id)
                .where(KBDataORM.data_id == uuid.UUID(data_id))
                .limit(1)
            )).first()
        if row:
            return str(row[0] or ""), (str(row[1]) if row[1] else DEFAULT_KNOWLEDGE_BASE)
    except Exception as exc:
        log.warning("Gold: could not resolve KBData row for data_id=%s: %s", data_id, exc)
    return "", DEFAULT_KNOWLEDGE_BASE


# ── Re-promotion cleanup ──────────────────────────────────────────────────────

async def _cleanup_previous_promotion(
    postgres: PostgresClient,
    qdrant: AsyncQdrantClient,
    graph: GraphStore,
    tenant_id: str,
    data_id: str,
    knowledge_base: str = "",
) -> None:
    """Erase any previous (possibly partial) gold state for this document so a
    retry never duplicates chunk points, blocks, or graph edges. Idempotent."""
    # Chunk points by data_id payload filter.
    try:
        await qdrant.delete(
            collection_name=cfg.QDRANT_COLLECTION,
            points_selector=qmodels.FilterSelector(filter=qmodels.Filter(must=[
                qmodels.FieldCondition(key="data_id", match=qmodels.MatchValue(value=data_id)),
            ])),
        )
    except Exception as exc:
        log.warning("Gold cleanup: chunk point delete failed data_id=%s: %s", data_id, exc)

    # Doc-summary point (point_id == data_id).
    try:
        await qdrant.delete(
            collection_name=cfg.QDRANT_DOC_COLLECTION,
            points_selector=qmodels.PointIdsList(points=[data_id]),
        )
    except Exception as exc:
        log.warning("Gold cleanup: doc point delete failed data_id=%s: %s", data_id, exc)

    await graph.remove_document(tenant_id=tenant_id, knowledge_base=knowledge_base, data_id=data_id)

    # Soft-delete previous text blocks so the rebuilt set is the only live one.
    try:
        async with postgres.get_client() as session:
            await session.execute(
                sa_update(KBTextBlockORM)
                .where(KBTextBlockORM.owner_id == uuid.UUID(data_id))
                .where(KBTextBlockORM.is_deleted.is_(False))
                .values(is_deleted=True)
            )
            await session.commit()
    except Exception as exc:
        log.warning("Gold cleanup: block soft-delete failed data_id=%s: %s", data_id, exc)


# ── Atomic per-chunk commit (Postgres + Qdrant — Neo4j is document-level now) ──

async def _commit_chunk_atomic(
    postgres: PostgresClient,
    qdrant: AsyncQdrantClient,
    tenant_id: str,
    data_id: str,
    block_index: int,
    content: str,
    table_involved: bool,
    table: dict | None,
    payload_meta: dict,
    embedding: list[float],
    summary: str,
    approved_by: str,
    embed_model_id: str,
    knowledge_base: str = "",
    entities: list[str] | None = None,
    entity_type_fields: dict | None = None,
    emitter: PipelineEventEmitter = NULL_EMITTER,
) -> str:
    block_id: str | None = None
    qdrant_upserted = False

    try:
        async with postgres.get_client() as session:
            try:
                postgres_started = _time.monotonic()
                block_row = KBTextBlockORM(
                    owner_id=uuid.UUID(data_id),
                    block_index=block_index,
                )
                session.add(block_row)
                await session.flush()

                version_row = KBTextBlockVersionORM(
                    block_id=block_row.block_id,
                    version_number=1,
                    content=content,
                    created_by=approved_by,
                    table_involved=table_involved,
                    payload=payload_meta,
                    is_active=True,
                )
                session.add(version_row)
                # Flush BEFORE building the table row: version_id is a client-side uuid4
                # default that SQLAlchemy only materializes at flush time. Without this,
                # version_row.version_id is None and the KBTextTable PK/FK would be null.
                await session.flush()

                if table_involved and table:
                    table_row = KBTextTableORM(
                        version_id=version_row.version_id,
                        table_name=table.get("table_name", f"table_{block_index}"),
                        description=table.get("description"),
                        data=table.get("data"),
                    )
                    session.add(table_row)
                    await session.flush()

                block_id = str(block_row.block_id)
                postgres_ms = round((_time.monotonic() - postgres_started) * 1000)

                qdrant_started = _time.monotonic()
                await qdrant.upsert(
                    collection_name=cfg.QDRANT_COLLECTION,
                    points=[
                        qmodels.PointStruct(
                            id=block_id,
                            vector=embedding,
                            payload={
                                "tenant_id":          tenant_id,
                                "data_id":            data_id,
                                "block_id":           block_id,
                                "summary":            summary,
                                "entities":           entities or [],
                                **(entity_type_fields or {}),
                                "embedding_model_id": embed_model_id,
                                "knowledge_base":     knowledge_base,
                                "is_deleted":         False,
                            },
                        )
                    ],
                )
                qdrant_upserted = True
                qdrant_ms = round((_time.monotonic() - qdrant_started) * 1000)

                await session.commit()
                log.info("Gold: atomic commit OK block_index=%d block_id=%s", block_index, block_id)

                await emitter.emit("gold.store.timing", {
                    "index":       block_index,
                    "postgres_ms": postgres_ms,
                    "qdrant_ms":   qdrant_ms,
                })

                return block_id

            except Exception as exc:
                await session.rollback()

                if qdrant_upserted and block_id:
                    try:
                        await qdrant.delete(
                            collection_name=cfg.QDRANT_COLLECTION,
                            points_selector=qmodels.PointIdsList(points=[block_id]),
                        )
                    except Exception as rollback_exc:
                        log.error("Gold: Qdrant rollback FAILED block_id=%s: %s", block_id, rollback_exc)

                raise RuntimeError(
                    f"Atomic commit failed block_index={block_index}: {exc}"
                ) from exc

    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"Unexpected error during atomic commit block_index={block_index}: {exc}"
        ) from exc


# ── Per-chunk entity canonicalization (shared by the gold loop + conflict resolver) ──

async def canonicalize_chunk_entities(
    qdrant: AsyncQdrantClient,
    postgres: PostgresClient,
    model: ModelServiceClient,
    tenant_id: str,
    raw_entities: list[dict],
    *,
    knowledge_base: str = "",
    embed_model_id: str = "",
) -> tuple[list[str], dict[str, str], dict[str, str], dict[str, str], dict[str, str]]:
    """Filter (blacklist/dedup) → registry-canonicalize one chunk's silver entities.

    Returns (canonicals, type_by_canonical, description_by_canonical,
             display_by_canonical, raw_to_canonical):
      canonicals               — deduped canonical names for this chunk
      type_by_canonical        — canonical → entity type (first surface form wins)
      description_by_canonical — canonical → description
      display_by_canonical     — canonical → original surface form (diacritics intact)
      raw_to_canonical         — every surface form + alias (folded) → canonical, for
                                 remapping this chunk's relationships
    Empty maps on no entities or a registry failure (conservative — never raises)."""
    raw_entities_parsed = _to_entity_list(raw_entities or [])
    if not raw_entities_parsed:
        return [], {}, {}, {}, {}
    try:
        canonicals, details = await entity_registry.normalize_entities_detailed(
            qdrant, postgres, model, tenant_id, raw_entities_parsed,
            verify_fn=verify_merge_pair,
            knowledge_base=knowledge_base, embed_model_id=embed_model_id,
        )
    except Exception as exc:
        log.warning("Gold: chunk canonicalize failed: %s", exc)
        return [], {}, {}, {}, {}

    raw_to_canonical: dict[str, str] = {}
    display_by_canonical: dict[str, str] = {}
    for detail in details:
        canonical = detail["canonical"]
        for surface_form in [detail["raw"], *detail.get("aliases", [])]:
            raw_to_canonical[_fold(surface_form)] = canonical
        display_by_canonical.setdefault(canonical, detail["raw"])

    type_by_canonical: dict[str, str] = {}
    description_by_canonical: dict[str, str] = {}
    for raw_entity in raw_entities_parsed:
        canonical = raw_to_canonical.get(_fold(raw_entity["name"]))
        if not canonical:
            continue
        type_by_canonical.setdefault(canonical, raw_entity.get("type", "concept"))
        description_by_canonical.setdefault(canonical, raw_entity.get("description", ""))
    return canonicals, type_by_canonical, description_by_canonical, display_by_canonical, raw_to_canonical


def remap_chunk_relationships(
    relationships: list[dict], raw_to_canonical: dict[str, str]
) -> list[dict]:
    """Remap a chunk's silver relationships (chunk-local names) onto canonical
    entities. Drops edges whose endpoints didn't canonicalize or are self-loops."""
    remapped: list[dict] = []
    for relationship in (relationships or []):
        canonical_from = raw_to_canonical.get(_fold(str(relationship.get("from", ""))))
        canonical_to = raw_to_canonical.get(_fold(str(relationship.get("to", ""))))
        rel_type = str(relationship.get("type", "")).strip()
        if not (canonical_from and canonical_to and rel_type) or canonical_from == canonical_to:
            continue
        remapped.append({
            "from": canonical_from, "to": canonical_to,
            "type": rel_type, "score": _to_score(relationship.get("score")),
        })
    return remapped


# ── Legacy-staging compat ─────────────────────────────────────────────────────

def _heuristic_summary(content: str) -> str:
    """Staging docs produced before silver wrote summaries have no chunk summary.
    First sentence, truncated — re-run silver for proper LLM summaries."""
    first_sentence = re.split(r"[.!?…]", content, maxsplit=1)[0].strip()
    return " ".join((first_sentence or content).split()[:40])


# ── Main stage function ────────────────────────────────────────────────────────
async def _detect_conflict(existing_summary: str, incoming_summary: str) -> tuple[dict, str]:
    """Returns (conflict_result_dict, raw_llm_answer)."""
    user = (
        f"Existing chunk summary:\n{existing_summary}\n\n"
        f"Incoming chunk summary:\n{incoming_summary}"
    )
    try:
        result, raw, _ = await _llm_generate_json(
            SYSTEM_PROMPTS["conflict"], user, max_tokens=128, system_type="conflict",
        )
        return result, raw
    except Exception as exc:
        log.warning("Gold: conflict detection failed (%s) — treating as no conflict", exc)
        return {"has_conflict": False}, f"ERROR: {exc}"

async def _detect_and_record_conflict(
    *, postgres, qdrant, tenant_id, data_id, knowledge_base, embed_model_id, approved_by,
    block_index, content, summary, intents, table_involved, table, embedding, chunk,
    ensure_conflict_batch, emitter,
) -> bool:
    """Search the chunk's nearest neighbour; if it clears the similarity floor and the
    LLM confirms a conflict, record it (PENDING) and return True. A failed conflict
    insert is logged and treated as "no conflict" so the chunk is never lost."""
    conflict_started = _time.monotonic()
    search_response = await qdrant_vector_search(qdrant, SearchRequest(
        tenant_id=tenant_id,
        collection_name=cfg.QDRANT_COLLECTION,
        query_vector=embedding,
        limit=1,
        knowledge_base=knowledge_base or None,
    ))
    hits = search_response.data if search_response.code == 200 and search_response.data else []
    top_score = max((hit.score for hit in hits), default=0.0)
    await emitter.emit("gold.conflict.check", {
        "index":      block_index,
        "candidates": len(hits),
        "threshold":  cfg.CONFLICT_SIMILARITY_THRESHOLD,
        "top_score":  round(top_score, 4),
    })

    conflict_recorded = False
    for hit in hits:
        if hit.score < cfg.CONFLICT_SIMILARITY_THRESHOLD:
            continue
        existing_summary = (hit.payload or {}).get("summary", "")
        conflict_result, conflict_raw = await _detect_conflict(existing_summary, summary)
        if not conflict_result.get("has_conflict"):
            break
        raw_type = conflict_result.get("conflict_type", ConflictType.CONTENT_CONFLICT.value)
        raw_severity = conflict_result.get("severity", ConflictSeverity.MEDIUM.value)
        try:
            conflict_type_enum = ConflictType(raw_type)
        except ValueError:
            conflict_type_enum = ConflictType.CONTENT_CONFLICT
        try:
            severity_enum = ConflictSeverity(raw_severity)
        except ValueError:
            severity_enum = ConflictSeverity.MEDIUM
        try:
            insert_response = await postgres.insert(KBConflictInsert(
                tenant_id=tenant_id,
                conflict_type=conflict_type_enum,
                severity=severity_enum,
                batch_id=await ensure_conflict_batch(),
                status=ConflictStatus.PENDING,
                detailed_explanation=conflict_result.get("explanation", ""),
                existing_snapshot={"block_id": str(hit.id), "summary": existing_summary},
                incoming_snapshot={
                    "block_index":    block_index,
                    "data_id":        data_id,
                    "tenant_id":      tenant_id,
                    "approved_by":    approved_by,
                    "content":        content,
                    "summary":        summary,
                    "embedding":      embedding,
                    "intents":        intents,
                    "table_involved": table_involved,
                    "table":          table,
                    "embed_model_id": embed_model_id,
                    "knowledge_base": knowledge_base,
                    "entities":       chunk.get("entities") or [],
                    "relationships":  chunk.get("relationships") or [],
                },
            ))
            if insert_response.code != 200:
                raise RuntimeError(f"conflict insert returned code {insert_response.code}")
        except Exception as exc:
            log.error("Gold: conflict insert failed block_index=%d data_id=%s: %s", block_index, data_id, exc)
            break  # don't lose the chunk — fall through and commit it normally
        conflict_recorded = True
        await emitter.emit("gold.conflict.found", {
            "index":            block_index,
            "conflict_type":    raw_type,
            "severity":         raw_severity,
            "explanation":      conflict_result.get("explanation", ""),
            "existing_summary": existing_summary,
            "similarity":       round(hit.score, 4),
            "raw_answer":       conflict_raw[:2000],
        })
        log.info("Gold: conflict detected block_index=%d type=%s severity=%s",
                 block_index, raw_type, raw_severity)
        break

    await emitter.emit("gold.conflict.timing", {
        "index":       block_index,
        "candidates":  len(hits),
        "duration_ms": round((_time.monotonic() - conflict_started) * 1000),
    })
    return conflict_recorded


async def _add_chunk_to_graph(
    *, graph, tenant_id, knowledge_base, data_id, block_id, chunk, canonicals,
    type_by_canonical, description_by_canonical, display_by_canonical, raw_to_canonical,
    embed_model_id, database, block_index, emitter,
) -> bool:
    try:
        await graph.add_chunk(
            tenant_id=tenant_id,
            knowledge_base=knowledge_base,
            data_id=data_id,
            block_id=block_id,
            entities=[
                {"canonical": canonical, "display": display_by_canonical.get(canonical, canonical),
                 "description": description_by_canonical.get(canonical, ""),
                 "type": type_by_canonical.get(canonical, "concept")}
                for canonical in canonicals
            ],
            relationships=remap_chunk_relationships(chunk.get("relationships"), raw_to_canonical),
            embed_model_id=embed_model_id,
        )
        await emitter.emit("gold.chunk.entities", {"index": block_index, "entities": canonicals})
        return False
    except Exception as exc:
        log.error("Gold: graph update failed block_index=%d data_id=%s: %s", block_index, data_id, exc)
        await write_log(database, data_id, "GRAPH_EXTRACTING",
                        f"Graph update failed for chunk {block_index}: {exc}", level="ERROR")
        await emitter.emit("gold.graph.failed", {"index": block_index, "error": str(exc)[:300]})
        return True


async def _process_chunk(
    *, chunk, postgres, qdrant, graph, model, tenant_id, data_id, knowledge_base,
    embed_model_id, approved_by, database, ensure_conflict_batch, emitter,
) -> dict:
    block_index = chunk["block_index"]
    content = chunk["content"]
    summary = (chunk.get("summary") or "").strip() or _heuristic_summary(content)
    intents = [
        intent for intent in (chunk.get("intents") or []) if isinstance(intent, str) and intent.strip()
    ] or ["document_content"]

    embed_started = _time.monotonic()
    embedding = (await model.embed([content[:4000]], model_id=embed_model_id))[0]

    await emitter.emit("gold.embed.complete", {
        "index":       block_index,
        "dim":         len(embedding),
        "duration_ms": round((_time.monotonic() - embed_started) * 1000),
    })

    is_conflict = await _detect_and_record_conflict(
        postgres=postgres, qdrant=qdrant, tenant_id=tenant_id, data_id=data_id,
        knowledge_base=knowledge_base, embed_model_id=embed_model_id, approved_by=approved_by,
        block_index=block_index, content=content, summary=summary, intents=intents,
        table_involved=chunk.get("table_involved", False), table=chunk.get("table"),
        embedding=embedding, chunk=chunk, ensure_conflict_batch=ensure_conflict_batch, emitter=emitter,
    )
    if is_conflict:
        return {"outcome": "conflict"}

    (canonicals, type_by_canonical, description_by_canonical,
     display_by_canonical, raw_to_canonical) = await canonicalize_chunk_entities(
        qdrant, postgres, model, tenant_id, chunk.get("entities") or [],
        knowledge_base=knowledge_base, embed_model_id=embed_model_id,
    )

    block_id = await _commit_chunk_atomic(
        postgres=postgres,
        qdrant=qdrant,
        tenant_id=tenant_id,
        data_id=data_id,
        block_index=block_index,
        content=content,
        table_involved=chunk.get("table_involved", False),
        table=chunk.get("table"),
        payload_meta={
            "summary":            summary,
            "entities":           canonicals,
            "embedding_model_id": embed_model_id,
            "knowledge_base":     knowledge_base,
        },
        embedding=embedding,
        summary=summary,
        approved_by=approved_by,
        embed_model_id=embed_model_id,
        knowledge_base=knowledge_base,
        entities=canonicals,
        entity_type_fields=entities_by_type_fields(type_by_canonical, canonicals),
        emitter=emitter,
    )
    await emitter.emit("gold.commit.ok", {
        "index":              block_index,
        "block_id":           block_id,
        "embedding_model_id": embed_model_id,
    })

    graph_failed = await _add_chunk_to_graph(
        graph=graph, tenant_id=tenant_id, knowledge_base=knowledge_base, data_id=data_id,
        block_id=block_id, chunk=chunk, canonicals=canonicals, type_by_canonical=type_by_canonical,
        description_by_canonical=description_by_canonical, display_by_canonical=display_by_canonical,
        raw_to_canonical=raw_to_canonical, embed_model_id=embed_model_id, database=database,
        block_index=block_index, emitter=emitter,
    )
    return {"outcome": "committed", "summary": summary, "intents": intents, "graph_failed": graph_failed}


async def _reduce_document_summary(committed_summaries, database, data_id, emitter) -> str:
    await set_chunk_progress(database, data_id, status="GOLD_REDUCE")
    async with emitter.step("gold.doc.reduce", summaries=len(committed_summaries)) as reduce_scope:
        doc_summary = await _reduce_summaries(committed_summaries, emitter)
        reduce_scope.set(summary=doc_summary, chars=len(doc_summary))
    return doc_summary


async def _write_doc_summary_point(
    *, qdrant, model, database, data_id, tenant_id, knowledge_base, embed_model_id,
    doc_summary, top_intents, committed_count, emitter,
) -> None:
    await set_chunk_progress(database, data_id, status="GOLD_GRAPH")
    async with emitter.step("gold.doc.commit", data_id=data_id) as doc_scope:
        doc_embedding = (await model.embed([doc_summary], model_id=embed_model_id))[0]
        await qdrant.upsert(
            collection_name=cfg.QDRANT_DOC_COLLECTION,
            points=[qmodels.PointStruct(
                id=data_id,
                vector=doc_embedding,
                payload={
                    "tenant_id":          tenant_id,
                    "data_id":            data_id,
                    "summary":            doc_summary,
                    "intents":            top_intents,
                    "chunk_count":        committed_count,
                    "embedding_model_id": embed_model_id,
                    "knowledge_base":     knowledge_base,
                    "is_deleted":         False,
                },
            )],
        )
        doc_scope.set(intents=top_intents)


async def _update_document_tier(postgres, database, data_id, top_intents) -> None:
    try:
        async with postgres.get_client() as session:
            try:
                await session.execute(
                    sa_update(KBDataORM)
                    .where(KBDataORM.data_id == uuid.UUID(data_id))
                    .values(
                        current_tier=Tier.GOLD.value,
                        doc_metadata=sa_func.coalesce(
                            KBDataORM.doc_metadata, sa_cast({}, JSONB)
                        ).op("||")(sa_cast({"intents": top_intents}, JSONB)),
                    )
                )
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    except Exception as exc:
        await write_log(database, data_id, "FAILED", f"Gold tier update failed: {exc}", level="ERROR")
        raise


async def _reconcile_conflict_batch(postgres, conflict_batch_id, pending_conflict_count) -> None:
    """PENDING only while real conflicts await human resolution; otherwise RESOLVED."""
    if conflict_batch_id is None:
        return
    final_status = (
        ConflictStatus.PENDING.value if pending_conflict_count > 0 else ConflictStatus.RESOLVED.value
    )
    try:
        async with postgres.get_client() as session:
            try:
                await session.execute(
                    sa_update(KBConflictBatchORM)
                    .where(KBConflictBatchORM.batch_id == uuid.UUID(conflict_batch_id))
                    .values(status=final_status)
                )
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    except Exception as exc:
        log.warning("Gold: batch status reconcile failed batch_id=%s: %s", conflict_batch_id, exc)


async def _record_gold_lifecycle(postgres, database, data_id, approved_by, counts, embed_model_id) -> None:
    """Audit row — best-effort, never fails the stage."""
    try:
        await postgres.insert(KBLifecycleHistoryInsert(
            data_id=data_id,
            to_tier=Tier.GOLD,
            from_tier=Tier.SILVER,
            approved_by=approved_by,
            notes=(
                f"Promoted to gold: {counts['committed']} committed, "
                f"{counts['conflicted']} conflicts, {counts['skipped']} skipped, "
                f"{counts['failed']} failed, {counts['graph_failed']} graph-failed"
            ),
            pipeline_metadata={
                "stage":               "gold",
                "chunks_committed":    counts["committed"],
                "chunks_conflicted":   counts["conflicted"],
                "chunks_skipped":      counts["skipped"],
                "chunks_failed":       counts["failed"],
                "chunks_graph_failed": counts["graph_failed"],
                "embed_model_id":      embed_model_id,
            },
        ))
    except Exception as exc:
        log.warning("Gold: lifecycle history insert failed data_id=%s: %s", data_id, exc)
        await write_log(database, data_id, "PUBLISHED",f"Gold lifecycle history not recorded: {exc}", level="WARNING")


async def promote_to_gold(
    postgres: PostgresClient,
    qdrant: AsyncQdrantClient,
    graph: GraphStore,
    mongo: MongoClient,
    model: ModelServiceClient,
    data_id: str,
    tenant_id: str,
    approved_by: str,
    emitter: PipelineEventEmitter = NULL_EMITTER,
) -> dict:
    embed_model_id = await _get_collection_embed_model_id(postgres, cfg.QDRANT_COLLECTION)
    log.info("Gold: collection '%s' → embed_model_id=%s", cfg.QDRANT_COLLECTION, embed_model_id or "none")

    doc_name, knowledge_base = await _get_data_row(postgres, data_id)
    log.info("Gold: data_id=%s → knowledge_base=%s", data_id, knowledge_base or "none")

    database = mongo.get_client()
    staging = await database["kb_silver_staging"].find_one({"data_id": data_id})
    if not staging:
        raise ValueError(f"No silver staging found for data_id={data_id}")

    chunks: list[dict] = staging.get("chunks", [])
    log.info("Gold: processing %d chunks data_id=%s", len(chunks), data_id)

    conflict_batch_id: str | None = None

    async def _ensure_conflict_batch() -> str | None:
        nonlocal conflict_batch_id
        if conflict_batch_id is not None:
            return conflict_batch_id
        try:
            batch_response = await postgres.insert(KBConflictBatchInsert(
                tenant_id=tenant_id,
                batch_title=f"Gold pipeline · {doc_name or data_id}",
                status=ConflictStatus.PENDING,
            ))
            if batch_response.code == 200:
                conflict_batch_id = batch_response.data["batch_id"]
            else:
                log.warning("Gold: conflict batch creation failed data_id=%s code=%s", data_id, batch_response.code)
        except Exception as exc:
            log.error("Gold: conflict batch insert failed data_id=%s: %s", data_id, exc)
        return conflict_batch_id

    counts = {"committed": 0, "conflicted": 0, "pending": 0, "failed": 0, "skipped": 0, "graph_failed": 0}
    committed_summaries: list[str] = []   # document order — feeds the reduce step
    intent_counter: Counter[str] = Counter()

    async with emitter.step("gold", total_chunks=len(chunks)) as gold_scope:
        # Drive the /progress % bar from Mongo: total now, processed bumped per chunk.
        await set_chunk_progress(database, data_id, total=len(chunks), processed=0)
        # Erase any previous (possibly partial) promotion so retries never duplicate.
        await _cleanup_previous_promotion(postgres, qdrant, graph, tenant_id, data_id, knowledge_base)

        for chunk_index, chunk in enumerate(chunks):
            await set_chunk_progress(database, data_id, processed=chunk_index)  # chunks completed so far
            block_index = chunk["block_index"]
            try:
                async with emitter.step("gold.chunk", index=block_index, length=len(chunk["content"])):
                    result = await _process_chunk(
                        chunk=chunk, postgres=postgres, qdrant=qdrant, graph=graph, model=model,
                        tenant_id=tenant_id, data_id=data_id, knowledge_base=knowledge_base,
                        embed_model_id=embed_model_id, approved_by=approved_by, database=database,
                        ensure_conflict_batch=_ensure_conflict_batch, emitter=emitter,
                    )
            except Exception as exc:
                counts["failed"] += 1
                log.error("Gold: chunk failed block_index=%d data_id=%s: %s", block_index, data_id, exc)
                await write_log(database, data_id, "GRAPH_EXTRACTING",
                                f"Chunk {block_index} failed during gold promotion: {exc}", level="ERROR")
                continue

            if result["outcome"] == "conflict":
                counts["conflicted"] += 1
                counts["pending"] += 1
            elif result["outcome"] == "committed":
                counts["committed"] += 1
                committed_summaries.append(result["summary"])
                intent_counter.update(result["intents"])
                if result.get("graph_failed"):
                    counts["graph_failed"] += 1

        # All chunks processed — fill the per-chunk band before the doc-rollup phases.
        await set_chunk_progress(database, data_id, processed=len(chunks))
        top_intents = [intent for intent, _ in intent_counter.most_common(5)]

        # Phases 2-3: document rollup → doc-summary point.
        if committed_summaries:
            doc_summary = await _reduce_document_summary(committed_summaries, database, data_id, emitter)
            await _write_doc_summary_point(
                qdrant=qdrant, model=model, database=database, data_id=data_id, tenant_id=tenant_id,
                knowledge_base=knowledge_base, embed_model_id=embed_model_id, doc_summary=doc_summary,
                top_intents=top_intents, committed_count=counts["committed"], emitter=emitter,
            )
        else:
            log.warning(
                "Gold: no committed chunks for data_id=%s — skipping document rollup "
                "(no doc point, document invisible to 2-step retrieval)", data_id,
            )

        await _update_document_tier(postgres, database, data_id, top_intents)
        await _reconcile_conflict_batch(postgres, conflict_batch_id, counts["pending"])
        await _record_gold_lifecycle(postgres, database, data_id, approved_by, counts, embed_model_id)

        gold_scope.set(
            committed=counts["committed"], conflicted=counts["conflicted"],
            skipped=counts["skipped"], failed=counts["failed"], graph_failed=counts["graph_failed"],
        )

    log.info(
        "Gold complete data_id=%s committed=%d conflicted=%d skipped=%d failed=%d graph_failed=%d",
        data_id, counts["committed"], counts["conflicted"], counts["skipped"],
        counts["failed"], counts["graph_failed"],
    )
    await emitter.emit("pipeline.done", {})

    return {
        "data_id":    data_id,
        "committed":  counts["committed"],
        "conflicted": counts["conflicted"],
        "skipped":    counts["skipped"],
        "failed":     counts["failed"],
        "layer":      Tier.GOLD.value,
    }
