
from __future__ import annotations

import asyncio
import json
import logging
import re
import time as _time
import unicodedata
import uuid
from collections import Counter
from typing import Any, NamedTuple

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
    MatchingPayload,
    MatchType,
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
_JSON_OBJ = re.compile(r"\{[\s\S]*\}", re.DOTALL)


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


async def _retry_while_empty(produce_result, max_attempts: int, log_context: str) -> list:
    """Call produce_result up to max_attempts times, returning the first non-empty
    list. A valid-but-empty response isn't a JSON error, so _llm_generate_json's
    own retry never fires for it — this closes that gap for list-shaped results."""
    result: list = []
    for attempt in range(max_attempts):
        result = await produce_result()
        if result:
            return result
        log.warning("Gold: %s returned empty (attempt %d/%d)", log_context, attempt + 1, max_attempts)
    return result


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
            name = str(item.get("name") or "").strip()
            entity_type = _to_entity_type(item.get("type"))
            description = str(item.get("description") or "").strip()
            score = _to_score(item.get("score"))
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
        # Aliases that are themselves generic words would poison the alias table.
        aliases = [
            alias for alias in aliases
            if _fold(alias) not in _fold(alias) != _fold(name)
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
            "depth": depth,
            "inputs": len(texts),
            "batches": len(batches),
            "chars": total,
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
                                "tenant_id": tenant_id,
                                "data_id": data_id,
                                "block_id": block_id,
                                "summary": summary,
                                "entities": entities or [],
                                **(entity_type_fields or {}),
                                "embedding_model_id": embed_model_id,
                                "knowledge_base": knowledge_base,
                                "is_deleted": False,
                            },
                        )
                    ],
                )
                qdrant_upserted = True
                qdrant_ms = round((_time.monotonic() - qdrant_started) * 1000)

                await session.commit()
                log.info("Gold: atomic commit OK block_index=%d block_id=%s", block_index, block_id)

                await emitter.emit("gold.store.timing", {
                    "index": block_index,
                    "postgres_ms": postgres_ms,
                    "qdrant_ms": qdrant_ms,
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

# The twelve criteria stage 3 scores each candidate on (definitions live in the
# stage-3b system prompt, utils.prompts._PIVOT_COMPARE_SYSTEM_VI).
_PIVOT_CRITERIA: tuple[str, ...] = (
    "semantic_ambiguity_score", "semantic_multilingual_score",
    "temporal_time_score", "temporal_version_score",
    "factual_entities_score", "factual_relationships_score", "factual_quantity_score",
    "reasoning_logic_score", "constraint_policy_score", "missing_information_score",
    "duplicate_score", "irrelevant_score",
)

# Maps a dominant criterion to the closest existing ConflictType/ConflictSeverity
# so the new pipeline stays compatible with the conflict-review DB schema and UI.
_CRITERION_CONFLICT_TYPE: dict[str, ConflictType] = {
    "duplicate_score": ConflictType.CONTENT_DUPLICATE,
    "temporal_time_score": ConflictType.CONTENT_UPDATE,
    "temporal_version_score": ConflictType.CONTENT_UPDATE,
    "semantic_ambiguity_score": ConflictType.CONTENT_CONFLICT,
    "semantic_multilingual_score": ConflictType.CONTENT_CONFLICT,
    "missing_information_score": ConflictType.CONTENT_CONFLICT,
    "constraint_policy_score": ConflictType.CONTENT_CONFLICT,
    "factual_entities_score": ConflictType.CONTENT_CONTRADICTION,
    "factual_relationships_score": ConflictType.CONTENT_CONTRADICTION,
    "factual_quantity_score": ConflictType.CONTENT_CONTRADICTION,
    "reasoning_logic_score": ConflictType.CONTENT_CONTRADICTION,
}
_CRITERION_SEVERITY: dict[str, ConflictSeverity] = {
    "duplicate_score": ConflictSeverity.LOW,
    "semantic_ambiguity_score": ConflictSeverity.LOW,
    "semantic_multilingual_score": ConflictSeverity.LOW,
    "temporal_time_score": ConflictSeverity.MEDIUM,
    "temporal_version_score": ConflictSeverity.MEDIUM,
    "missing_information_score": ConflictSeverity.MEDIUM,
    "constraint_policy_score": ConflictSeverity.MEDIUM,
    "factual_entities_score": ConflictSeverity.HIGH,
    "factual_relationships_score": ConflictSeverity.HIGH,
    "factual_quantity_score": ConflictSeverity.HIGH,
    "reasoning_logic_score": ConflictSeverity.HIGH,
}


class QuestionPivots(NamedTuple):
    """Stage-1 result: the question a chunk answers and the reasoning-pivot
    chain leading to it."""
    question: str
    intent: str
    entities: list[str]
    pivots: list[str]


def _parse_question_pivots_entry(entry: dict) -> QuestionPivots | None:
    """One "questions" array entry → QuestionPivots, or None if unusable."""
    question = str(entry.get("question") or "").strip()
    pivots = [pivot for pivot in (entry.get("pivots") or []) if isinstance(pivot, str) and pivot.strip()]
    if not question or not pivots:
        return None
    entities = [
        entity_name for entity_name in (entry.get("question_entities") or [])
        if isinstance(entity_name, str) and entity_name.strip()
    ]
    return QuestionPivots(
        question=question, intent=str(entry.get("question_intent") or "").strip(),
        entities=entities, pivots=pivots,
    )


def _parse_question_pivots_entries(entries: list) -> list[QuestionPivots]:
    """A stage-1 "questions" array → QuestionPivots, dropping unusable entries."""
    question_pivots_list: list[QuestionPivots] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        question_pivots = _parse_question_pivots_entry(entry)
        if question_pivots is not None:
            question_pivots_list.append(question_pivots)
    return question_pivots_list


async def _derive_question_pivots(chunk_text: str) -> list[QuestionPivots]:
    """Stage 1 (single prompt, retried while empty): every distinct question this
    chunk answers, each with its own intent, entities, and reasoning-pivot chain."""

    async def _attempt() -> list[QuestionPivots]:
        try:
            result, _, _ = await _llm_generate_json(
                SYSTEM_PROMPTS["pivot-question"], chunk_text, max_tokens=768,
                system_type="pivot-question",
            )
        except Exception as exc:
            log.warning("Gold: pivot-question extraction failed (%s)", exc)
            return []
        return _parse_question_pivots_entries(result.get("questions") or [])

    return await _retry_while_empty(_attempt, cfg.CONFLICT_SEMANTIC_RETRY_ATTEMPTS, "pivot-question extraction")


async def _retrieve_candidate_chunks(
        *, qdrant, postgres, model, tenant_id, knowledge_base, embed_model_id,
        question: str, question_intent: str, question_entities: list[str],
) -> list:
    """Stage 2 (no LLM call): embed "question | intent", filter to chunks tagged
    with an existing canonical entity from question_entities, and return up to
    CONFLICT_TOP_K_DOCS nearest chunks that clear the similarity floor.

    Qdrant chunk points carry CANONICAL entity names (see canonicalize_chunk_entities),
    not raw LLM surface forms, so question_entities is resolved through the same
    registry, read-only, before it can be used as a filter."""
    canonical_question_entities = await entity_registry.resolve_existing_entities(
        qdrant, postgres, model, tenant_id, question_entities,
    )
    query_vector = (await model.embed([f"{question} | {question_intent}"], model_id=embed_model_id))[0]
    matching_payload = (
        [MatchingPayload(field="entities", values=canonical_question_entities)]
        if canonical_question_entities else None
    )
    search_response = await qdrant_vector_search(qdrant, SearchRequest(
        tenant_id=tenant_id,
        collection_name=cfg.QDRANT_COLLECTION,
        query_vector=query_vector,
        limit=cfg.CONFLICT_TOP_K_DOCS,
        knowledge_base=knowledge_base or None,
        matching_payload=matching_payload,
        match_type=MatchType.any,
    ))
    hits = search_response.data if search_response.code == 200 and search_response.data else []
    return [hit for hit in hits if hit.score >= cfg.CONFLICT_SIMILARITY_THRESHOLD]


def _hit_summary(hit: Any) -> str:
    """Existing-chunk summary text carried in a Qdrant search hit's payload."""
    return (hit.payload or {}).get("summary", "")


async def _extract_chunk_pivots(question: str, chunk_text: str) -> list[str]:
    """Stage 3, first CoT prompt (retried while empty): the reasoning-pivot chain
    a candidate chunk offers toward the fixed question, in the stage-1 triplet
    structure."""
    user = f"Câu hỏi: {question}\n\nĐoạn văn bản: {chunk_text}"

    async def _attempt() -> list[str]:
        try:
            result, _, _ = await _llm_generate_json(
                SYSTEM_PROMPTS["pivot-chunk"], user, max_tokens=256, system_type="pivot-chunk",
            )
        except Exception as exc:
            log.warning("Gold: pivot-chunk extraction failed (%s)", exc)
            return []
        return [pivot for pivot in (result.get("pivots") or []) if isinstance(pivot, str) and pivot.strip()]

    return await _retry_while_empty(_attempt, cfg.CONFLICT_SEMANTIC_RETRY_ATTEMPTS, "pivot-chunk extraction")


async def _compare_pivot_sets(question: str, incoming_pivots: list[str], existing_pivots: list[str]) -> dict:
    """Stage 3, second CoT prompt: score the incoming vs. existing pivot chains
    across the twelve conflict criteria."""
    user = (
        f"Câu hỏi: {question}\n\n"
        f"Incoming pivots: {incoming_pivots}\n\n"
        f"Existing pivots: {existing_pivots}"
    )
    try:
        result, _, _ = await _llm_generate_json(
            SYSTEM_PROMPTS["pivot-compare"], user, max_tokens=256, system_type="pivot-compare",
        )
        return {criterion: _to_score(result.get(criterion), default=0.0) for criterion in _PIVOT_CRITERIA}
    except Exception as exc:
        log.warning("Gold: pivot comparison failed (%s) — treating as irrelevant", exc)
        return {criterion: (1.0 if criterion == "irrelevant_score" else 0.0) for criterion in _PIVOT_CRITERIA}


async def _score_one_candidate(question: str, incoming_pivots: list[str], hit: Any) -> dict:
    """Stage 3 (CoT) for ONE candidate: extract its pivots, then score against
    the incoming pivots across the twelve criteria."""
    existing_pivots = await _extract_chunk_pivots(question, _hit_summary(hit))
    return await _compare_pivot_sets(question, incoming_pivots, existing_pivots)


async def _score_candidates(
        question: str, incoming_pivots: list[str], candidate_hits: list,
) -> list[tuple[Any, dict]]:
    """Stage 3: score every candidate concurrently (bounded by
    CONFLICT_DOCUMENT_CONCURRENCY), then drop candidates below the relevance floor."""
    document_semaphore = asyncio.Semaphore(cfg.CONFLICT_DOCUMENT_CONCURRENCY)

    async def _score_bounded(hit: Any) -> dict:
        async with document_semaphore:
            return await _score_one_candidate(question, incoming_pivots, hit)

    scores_by_candidate = await asyncio.gather(*(_score_bounded(hit) for hit in candidate_hits))
    return [
        (hit, scores) for hit, scores in zip(candidate_hits, scores_by_candidate)
        if scores["irrelevant_score"] < cfg.CONFLICT_IRRELEVANT_THRESHOLD
    ]


def _normalize_criteria_matrix(criteria_by_candidate: list[dict]) -> list[dict]:
    """Normalize each criterion column across candidates: pij = xij / sum_i(xij).
    An all-zero column stays zero (no candidate stands out on that criterion)."""
    column_totals = {
        criterion: sum(row[criterion] for row in criteria_by_candidate) for criterion in _PIVOT_CRITERIA
    }
    return [
        {
            criterion: (row[criterion] / column_totals[criterion]) if column_totals[criterion] > 0 else 0.0
            for criterion in _PIVOT_CRITERIA
        }
        for row in criteria_by_candidate
    ]


def _dominant_conflict_criterion(raw_scores: dict) -> str:
    """The highest-scoring criterion for one candidate, excluding irrelevant_score."""
    return max(
        (criterion for criterion in _PIVOT_CRITERIA if criterion != "irrelevant_score"),
        key=lambda criterion: raw_scores[criterion],
    )


def _select_strongest_conflict(qualifying: list[tuple[Any, dict]]) -> tuple[Any, dict, str]:
    """Normalize criteria across qualifying candidates and return the one with the
    strongest share of its own dominant criterion: (hit, scores, criterion)."""
    normalized_matrix = _normalize_criteria_matrix([scores for _, scores in qualifying])
    best_index = max(
        range(len(qualifying)),
        key=lambda index: normalized_matrix[index][_dominant_conflict_criterion(qualifying[index][1])],
    )
    best_hit, best_scores = qualifying[best_index]
    return best_hit, best_scores, _dominant_conflict_criterion(best_scores)


class QuestionPipelineResult(NamedTuple):
    """Stage 2+3 result for ONE question — the unit of question-level concurrency."""
    question_pivots: QuestionPivots
    candidate_hits: list
    qualifying: list[tuple[Any, dict]]


async def _run_question_pipeline(
        question_pivots: QuestionPivots, *, qdrant, postgres, model, tenant_id, knowledge_base, embed_model_id,
) -> QuestionPipelineResult:
    """Stage 2 + stage 3 for ONE question: retrieve candidate chunks, then score them."""
    candidate_hits = await _retrieve_candidate_chunks(
        qdrant=qdrant, postgres=postgres, model=model, tenant_id=tenant_id,
        knowledge_base=knowledge_base, embed_model_id=embed_model_id,
        question=question_pivots.question, question_intent=question_pivots.intent,
        question_entities=question_pivots.entities,
    )
    qualifying = (
        await _score_candidates(question_pivots.question, question_pivots.pivots, candidate_hits)
        if candidate_hits else []
    )
    return QuestionPipelineResult(question_pivots, candidate_hits, qualifying)


async def _run_question_pipelines(
        question_pivots_list: list[QuestionPivots], *, qdrant, postgres, model, tenant_id,
        knowledge_base, embed_model_id,
) -> list[QuestionPipelineResult]:
    """Run stage 2+3 for every question concurrently, bounded by
    CONFLICT_QUESTION_CONCURRENCY."""
    question_semaphore = asyncio.Semaphore(cfg.CONFLICT_QUESTION_CONCURRENCY)

    async def _run_bounded(question_pivots: QuestionPivots) -> QuestionPipelineResult:
        async with question_semaphore:
            return await _run_question_pipeline(
                question_pivots, qdrant=qdrant, postgres=postgres, model=model, tenant_id=tenant_id,
                knowledge_base=knowledge_base, embed_model_id=embed_model_id,
            )

    return list(await asyncio.gather(*(_run_bounded(qp) for qp in question_pivots_list)))


async def _persist_conflict_record(
        *, postgres, ensure_conflict_batch, tenant_id, data_id, block_index, approved_by,
        content, summary, embedding, intents, table_involved, table, embed_model_id, knowledge_base, chunk,
        question_pivots: QuestionPivots, best_hit: Any, best_scores: dict,
        conflict_type: ConflictType, severity: ConflictSeverity, dominant_criterion: str,
) -> bool:
    """Insert the winning candidate as a PENDING conflict row. Never raises —
    returns False on any insert failure so the chunk falls through to a normal commit."""
    try:
        insert_response = await postgres.insert(KBConflictInsert(
            tenant_id=tenant_id,
            conflict_type=conflict_type,
            severity=severity,
            batch_id=await ensure_conflict_batch(),
            status=ConflictStatus.PENDING,
            detailed_explanation=(
                f"{dominant_criterion} ({best_scores[dominant_criterion]:.2f}) — {question_pivots.question}"
            ),
            existing_snapshot={"block_id": str(best_hit.id), "summary": _hit_summary(best_hit)},
            incoming_snapshot={
                "block_index": block_index,
                "data_id": data_id,
                "tenant_id": tenant_id,
                "approved_by": approved_by,
                "content": content,
                "summary": summary,
                "embedding": embedding,
                "intents": intents,
                "table_involved": table_involved,
                "table": table,
                "embed_model_id": embed_model_id,
                "knowledge_base": knowledge_base,
                "entities": chunk.get("entities") or [],
                "relationships": chunk.get("relationships") or [],
                "question": question_pivots.question,
                "question_intent": question_pivots.intent,
                "question_entities": question_pivots.entities,
                "incoming_pivots": question_pivots.pivots,
                "conflict_criteria_scores": best_scores,
            },
        ))
        if insert_response.code != 200:
            raise RuntimeError(f"conflict insert returned code {insert_response.code}")
    except Exception as exc:
        log.error("Gold: conflict insert failed block_index=%d data_id=%s: %s", block_index, data_id, exc)
        return False
    return True


async def _detect_and_record_conflict(
        *, postgres, qdrant, model, tenant_id, data_id, knowledge_base, embed_model_id, approved_by,
        block_index, content, summary, intents, table_involved, table, embedding, chunk,
        ensure_conflict_batch, emitter,
) -> bool:
    """Reasoning-pivot conflict pipeline root"""
    pipeline_started = _time.monotonic()

    # Stage 1 — every distinct question this chunk answers, each with its own pivots
    question_pivots_list = await _derive_question_pivots(
        summary or content[:cfg.CONFLICT_QUESTION_SOURCE_CHAR_LIMIT]
    )

    # Stage 2, 3 — concurrent per question
    results = await _run_question_pipelines(
        question_pivots_list, qdrant=qdrant, postgres=postgres, model=model, tenant_id=tenant_id,
        knowledge_base=knowledge_base, embed_model_id=embed_model_id,
    )
    total_candidates = sum(len(result.candidate_hits) for result in results)
    top_score = max((hit.score for result in results for hit in result.candidate_hits), default=0.0)
    await emitter.emit("gold.conflict.check", {
        "index": block_index,
        "candidates": total_candidates,
        "threshold": cfg.CONFLICT_SIMILARITY_THRESHOLD,
        "top_score": round(top_score, 4),
    })

    # Storing conflict
    conflict_recorded = False
    for result in results:
        if not result.qualifying:
            continue
        best_hit, best_scores, dominant_criterion = _select_strongest_conflict(result.qualifying)
        conflict_type = _CRITERION_CONFLICT_TYPE[dominant_criterion]
        severity = _CRITERION_SEVERITY[dominant_criterion]
        persisted = await _persist_conflict_record(
            postgres=postgres,
            ensure_conflict_batch=ensure_conflict_batch,
            tenant_id=tenant_id,
            data_id=data_id,
            block_index=block_index,
            approved_by=approved_by,
            content=content,
            summary=summary,
            embedding=embedding,
            intents=intents,
            table_involved=table_involved,
            table=table,
            embed_model_id=embed_model_id,
            knowledge_base=knowledge_base,
            chunk=chunk,
            question_pivots=result.question_pivots,
            best_hit=best_hit,
            best_scores=best_scores,
            conflict_type=conflict_type,
            severity=severity,
            dominant_criterion=dominant_criterion,
        )
        if not persisted:
            continue
        conflict_recorded = True
        await emitter.emit("gold.conflict.found", {
            "index": block_index,
            "conflict_type": conflict_type.value,
            "severity": severity.value,
            "dominant_criterion": dominant_criterion,
            "question": result.question_pivots.question,
            "existing_summary": _hit_summary(best_hit),
            "similarity": round(best_hit.score, 4),
        })
        log.info("Gold: conflict detected block_index=%d type=%s severity=%s criterion=%s",
                 block_index, conflict_type.value, severity.value, dominant_criterion)

    await emitter.emit("gold.conflict.timing", {
        "index": block_index,
        "candidates": total_candidates,
        "duration_ms": round((_time.monotonic() - pipeline_started) * 1000),
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
        await write_log(
            db = database,
            data_id= data_id,
            status = "GRAPH_EXTRACTING",
            message = f"Graph update failed for chunk {block_index}: {exc}",
            level="ERROR")
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
        "index": block_index,
        "dim": len(embedding),
        "duration_ms": round((_time.monotonic() - embed_started) * 1000),
    })

    is_conflict = await _detect_and_record_conflict(
        postgres=postgres, qdrant=qdrant, model=model, tenant_id=tenant_id, data_id=data_id,
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
            "summary": summary,
            "entities": canonicals,
            "embedding_model_id": embed_model_id,
            "knowledge_base": knowledge_base,
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
        "index": block_index,
        "block_id": block_id,
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
                    "tenant_id": tenant_id,
                    "data_id": data_id,
                    "summary": doc_summary,
                    "intents": top_intents,
                    "chunk_count": committed_count,
                    "embedding_model_id": embed_model_id,
                    "knowledge_base": knowledge_base,
                    "is_deleted": False,
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
                "stage": "gold",
                "chunks_committed": counts["committed"],
                "chunks_conflicted": counts["conflicted"],
                "chunks_skipped": counts["skipped"],
                "chunks_failed": counts["failed"],
                "chunks_graph_failed": counts["graph_failed"],
                "embed_model_id": embed_model_id,
            },
        ))
    except Exception as exc:
        log.warning("Gold: lifecycle history insert failed data_id=%s: %s", data_id, exc)
        await write_log(database, data_id, "PUBLISHED", f"Gold lifecycle history not recorded: {exc}", level="WARNING")


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
    committed_summaries: list[str] = []  # document order — feeds the reduce step
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
        "data_id": data_id,
        "committed": counts["committed"],
        "conflicted": counts["conflicted"],
        "skipped": counts["skipped"],
        "failed": counts["failed"],
        "layer": Tier.GOLD.value,
    }
