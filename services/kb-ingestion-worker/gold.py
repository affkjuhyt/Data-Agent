
from __future__ import annotations

import asyncio
import json
import re
import time as _time
import unicodedata
import uuid
from collections import Counter
from dataclasses import dataclass
import logging
from typing import Any, Awaitable, Callable, NamedTuple

from qdrant_client import models as qmodels
from qdrant_client.async_qdrant_client import AsyncQdrantClient
from sqlalchemy import (
    cast as sa_cast,
    delete as sa_delete,
    func as sa_func,
    text as sa_text,
    update as sa_update,
)
from sqlalchemy.dialects.postgresql import JSONB

from libs.basemodel.services_databaseconnector.postgres_model import (
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
    KBConflictBatchInsert,
    KBConflictInsert,
    KBLifecycleHistoryInsert,
    Tier,
)
from sqlalchemy import select as sa_select
from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBDataORM,
    KBTextBlockORM,
    KBTextBlockVersionORM,
    KBTextTableORM,
    KBQdrantCollectionORM,
    KBConflictBatchORM,
    KBConflictORM,
)
from libs.basemodel.services_databaseconnector.qdrant_model import (
    SearchRequest,
)
from libs.database_connector.model_connector import ModelServiceClient
from libs.shared.pii_detector import mask as pii_mask, mask_deep
from libs.database_connector.mongo_connector import MongoClient
from libs.database_connector.postgres_connector import PostgresClient
from libs.database_connector.qdrant_connector import (
    vector_search as qdrant_vector_search,
)

from utils import config as cfg
from utils import conflict_store
from utils import entity_registry
from utils import llm_client
from utils.entity_types import entities_by_type_fields, to_entity_type as _to_entity_type
from utils.graph_store import GraphStore
from utils.log_writer import IngestionLogger
from utils.pipeline_events import NULL_EMITTER, PipelineEventEmitter
from utils.prompts import GOLD_SYSTEM_PROMPTS as SYSTEM_PROMPTS
from utils.stage_chain import StageChain

log = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────────────────────

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
_JSON_OBJ = re.compile(r"\{[\s\S]*\}", re.DOTALL)

DEFAULT_KNOWLEDGE_BASE = "General Knowledge Base"

# The criteria one chunk is scored on against a candidate, and how the strongest of
# them maps onto the conflict types/severities the review UI shows.
_PIVOT_CRITERIA: tuple[str, ...] = (
    "semantic_ambiguity_score", "semantic_multilingual_score",
    "temporal_time_score", "temporal_version_score",
    "factual_entities_score", "factual_relationships_score", "factual_quantity_score",
    "reasoning_logic_score", "constraint_policy_score", "missing_information_score",
    "duplicate_score", "irrelevant_score",
)

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


# ── Models ────────────────────────────────────────────────────────────────────

class QuestionPivots(NamedTuple):
    """Stage-1 result: a question the chunk answers and the pivots leading to it."""
    question: str
    intent: str
    entities: list[str]
    pivots: list[str]


class QuestionPipelineResult(NamedTuple):
    """Stage 2+3 result for ONE question — the unit of question-level concurrency."""
    question_pivots: QuestionPivots
    candidate_hits: list
    qualifying: list[tuple[Any, dict]]


@dataclass(frozen=True, slots=True)
class ChunkEntities:
    """One chunk's entities after registry canonicalization, keyed by canonical name."""
    canonicals: list[str]
    type_by_canonical: dict[str, str]
    description_by_canonical: dict[str, str]
    display_by_canonical: dict[str, str]
    raw_to_canonical: dict[str, str]  # every surface form + alias (folded) → canonical

    def graph_nodes(self) -> list[dict]:
        """The entity payload GraphStore.add_chunk expects."""
        return [
            {"canonical": canonical,
             "display": self.display_by_canonical.get(canonical, canonical),
             "description": self.description_by_canonical.get(canonical, ""),
             "type": self.type_by_canonical.get(canonical, "concept")}
            for canonical in self.canonicals
        ]

    def qdrant_type_fields(self) -> dict:
        """The per-type fields a chunk's Qdrant payload carries."""
        return entities_by_type_fields(self.type_by_canonical, self.canonicals) if self.canonicals else {}


# ── Shared helpers ────────────────────────────────────────────────────────────

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
) -> tuple[dict[str, Any], str, int]:
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
            current_user = f"{user}\n\nNote: {last_hint}."

    raise ValueError(f"LLM failed to return valid JSON after {max_retries} attempts: {last_hint}")


async def _retry_while_empty(produce_result, max_attempts: int) -> list:
    """Call produce_result up to max_attempts times, returning the first non-empty
    list. A valid-but-empty response isn't a JSON error, so _llm_generate_json's
    own retry never fires for it — this closes that gap for list-shaped results."""
    result: list = []
    for attempt in range(max_attempts):
        result = await produce_result()
        if result:
            return result
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
    except Exception:
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
    parsed_entities: list[dict[str, Any]] = []
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
        entity["aliases"] = [alias for alias in entity["aliases"] if _fold(alias) not in all_folded_names]
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
            max_tokens=1024,
            system_type="doc-reduce",
        )
        summary = str(result.get("summary") or "").strip()
        if summary:
            return summary
    except Exception:
        pass
    return " ".join(texts)[: cfg.DOC_SUMMARY_BATCH_CHARS]


async def _reduce_summaries(
        summaries: list[str],
        emitter: PipelineEventEmitter = NULL_EMITTER,
) -> str:
    """Recursively reduce chunk summaries (in document order) into one document summary.

    Depth is hard-capped, then truncated: a small model can return output that does not
    shrink, so an unbounded "still too long → recurse" loop would never terminate.
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
        reduced = [await _reduce_batch(batch) for batch in batches]
        # No shrink → another pass won't help; stop and truncate below.
        if sum(len(text) for text in reduced) >= total and len(reduced) == len(texts):
            texts = reduced
            break
        texts = reduced
        depth += 1

    doc_summary = " ".join(texts)
    if len(doc_summary) > cfg.DOC_SUMMARY_TARGET_CHARS:
        doc_summary = doc_summary[: cfg.DOC_SUMMARY_TARGET_CHARS]
    return doc_summary


# ── Resolvers (collection model / knowledge base / document name) ─────────────
async def _get_collection_embed_model_id(
        postgres: PostgresClient,
        collection_name: str,
) -> str:
    """The embedding_model_id a Qdrant collection is built with, for the model service.

    Falls back to cfg.EMBED_MODEL_ID only when the collection has no model set; a
    Postgres failure raises, since guessing puts incomparable vectors in the collection.
    """
    async with postgres.get_client() as session:
        row = await session.scalar(
            sa_select(KBQdrantCollectionORM.embedding_model_id)
            .where(KBQdrantCollectionORM.collection_name == collection_name)
            .limit(1)
        )
    return str(row) if row else cfg.EMBED_MODEL_ID


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
    except Exception:
        pass
    return "", DEFAULT_KNOWLEDGE_BASE


# ── Re-promotion cleanup ──────────────────────────────────────────────────────

async def _clear_open_conflicts(
        postgres: PostgresClient, database, tenant_id: str, data_id: str, stage_record: StageChain,
) -> None:
    """Drop this document's unresolved conflicts and their payloads from both stores.

    Ids come from Postgres, the index, so a failure part-way leaves them findable and
    the retry finishes the job. Resolved conflicts stay — they are the audit trail.
    Deletions have no rollback: they're tracked on the stage_record but cannot be restored.
    """
    async with postgres.get_client() as session:
        rows = (await session.execute(
            sa_select(KBConflictORM.conflict_id, KBConflictORM.batch_id)
            .where(KBConflictORM.data_id == uuid.UUID(data_id))
            .where(KBConflictORM.status != ConflictStatus.RESOLVED.value)
        )).all()
    if not rows:
        return

    conflict_ids = [str(row.conflict_id) for row in rows]
    emptied_batch_ids = [str(batch_id) for batch_id in {row.batch_id for row in rows} if batch_id]
    await conflict_store.drop_payloads(database, tenant_id, conflict_ids)
    stage_record.push(f"stale_conflict_payloads_dropped_{data_id}")

    async with postgres.get_client() as session:
        try:
            await session.execute(
                sa_delete(KBConflictORM)
                .where(KBConflictORM.conflict_id.in_([uuid.UUID(cid) for cid in conflict_ids]))
            )
            # A batch this emptied has nothing left to review.
            await session.execute(
                sa_delete(KBConflictBatchORM)
                .where(KBConflictBatchORM.batch_id.in_([uuid.UUID(bid) for bid in emptied_batch_ids]))
                .where(~sa_select(KBConflictORM.conflict_id)
                       .where(KBConflictORM.batch_id == KBConflictBatchORM.batch_id)
                       .exists())
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    stage_record.push(f"stale_conflict_rows_dropped_{data_id}")

    # Only the batches Postgres just emptied lose their working document.
    async with postgres.get_client() as session:
        surviving = set((await session.execute(
            sa_select(KBConflictBatchORM.batch_id)
            .where(KBConflictBatchORM.batch_id.in_([uuid.UUID(bid) for bid in emptied_batch_ids]))
        )).scalars().all())
    await conflict_store.drop_batches(
        database, [bid for bid in emptied_batch_ids if uuid.UUID(bid) not in surviving],
    )
    stage_record.push(f"stale_conflict_batches_dropped_{data_id}")


async def _cleanup_previous_promotion(
        postgres: PostgresClient,
        qdrant: AsyncQdrantClient,
        graph: GraphStore,
        database,
        tenant_id: str,
        data_id: str,
        stage_record: StageChain,
        knowledge_base: str = "",
) -> None:
    """Erase any previous gold state for this document so a retry never duplicates it.

    Idempotent (deleting what is absent is not a failure). Raises on a store failure:
    promoting on top of state this could not erase is the duplication it exists to stop.
    Every delete is tracked on the stage_record as it happens; none of them can be undone.
    """
    # Chunk points by data_id payload filter.
    await qdrant.delete(
        collection_name=cfg.QDRANT_COLLECTION,
        points_selector=qmodels.FilterSelector(filter=qmodels.Filter(must=[
            qmodels.FieldCondition(key="data_id", match=qmodels.MatchValue(value=data_id)),
        ])),
    )
    stage_record.push(f"stale_chunk_points_dropped_{data_id}")

    # Doc-summary point (point_id == data_id).
    await qdrant.delete(
        collection_name=cfg.QDRANT_DOC_COLLECTION,
        points_selector=qmodels.PointIdsList(points=[data_id]),
    )
    stage_record.push(f"stale_doc_point_dropped_{data_id}")

    await graph.remove_document(tenant_id=tenant_id, knowledge_base=knowledge_base, data_id=data_id)
    stage_record.push(f"stale_graph_document_removed_{data_id}")

    # Soft-delete previous text blocks so the rebuilt set is the only live one.
    async with postgres.get_client() as session:
        try:
            await session.execute(
                sa_update(KBTextBlockORM)
                .where(KBTextBlockORM.owner_id == uuid.UUID(data_id))
                .where(KBTextBlockORM.is_deleted.is_(False))
                .values(is_deleted=True)
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    stage_record.push(f"stale_text_blocks_soft_deleted_{data_id}")

    # The conflicts this document raised last run — re-detecting them would add the
    # same incoming chunk to its batch a second time.
    await _clear_open_conflicts(postgres, database, tenant_id, data_id, stage_record)


# ── Atomic per-chunk commit (Postgres + Qdrant — Neo4j is document-level now) ──

async def _rollback_committed_chunk(postgres: PostgresClient, qdrant: AsyncQdrantClient, block_id: str) -> None:
    try:
        await qdrant.delete(
            collection_name=cfg.QDRANT_COLLECTION,
            points_selector=qmodels.PointIdsList(points=[block_id]),
        )
    except Exception:
        pass
    async with postgres.get_client() as session:
        try:
            await session.execute(sa_delete(KBTextBlockORM).where(KBTextBlockORM.block_id == uuid.UUID(block_id)))
            await session.commit()
        except Exception:
            await session.rollback()


async def _commit_chunk_atomic(
    postgres: PostgresClient,
    qdrant: AsyncQdrantClient,
    tenant_id: str,
    data_id: str,
    block_index: int,
    content: str,
    table_involved: bool,
    table: dict[str, Any] | None,
    payload_meta: dict[str, Any],
    embedding: list[float],
    summary: str,
    approved_by: str,
    embed_model_id: str,
    stage_record: StageChain,
    knowledge_base: str = "",
    entities: list[str] | None = None,
    entity_type_fields: dict[str, Any] | None = None,
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
                stage_record.push(
                    f"chunk_committed_{block_index}",
                    rollback_func=_rollback_committed_chunk,
                    rollback_args=(postgres, qdrant, block_id),
                )

                await emitter.emit("gold.store.timing", {
                    "index": block_index,
                    "postgres_ms": postgres_ms,
                    "qdrant_ms": qdrant_ms,
                })

                return block_id

            except Exception as exc:
                await session.rollback()

                rollback_note = ""
                if qdrant_upserted and block_id:
                    try:
                        await qdrant.delete(
                            collection_name=cfg.QDRANT_COLLECTION,
                            points_selector=qmodels.PointIdsList(points=[block_id]),
                        )
                    except Exception as rollback_exc:
                        rollback_note = f"; Qdrant rollback also failed: {rollback_exc}"

                raise RuntimeError(
                    f"Atomic commit failed block_index={block_index}: {exc}{rollback_note}"
                ) from exc

    except RuntimeError as error:
        raise error
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
    raw_entities: list[dict[str, Any]],
    *,
    knowledge_base: str = "",
    embed_model_id: str = "",
) -> ChunkEntities:
    """Filter (blacklist/dedup) → registry-canonicalize one chunk's silver entities.

    Empty only when the chunk genuinely has no entities: a registry failure raises,
    since committing a block with no entities looks identical to one that has none.
    """
    raw_entities_parsed = _to_entity_list(raw_entities or [])
    if not raw_entities_parsed:
        return ChunkEntities([], {}, {}, {}, {})
    canonicals, details = await entity_registry.normalize_entities_detailed(
        qdrant, postgres, model, tenant_id, raw_entities_parsed,
        verify_fn=verify_merge_pair,
        knowledge_base=knowledge_base, embed_model_id=embed_model_id,
    )

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
    return ChunkEntities(
        canonicals=canonicals,
        type_by_canonical=type_by_canonical,
        description_by_canonical=description_by_canonical,
        display_by_canonical=display_by_canonical,
        raw_to_canonical=raw_to_canonical,
    )


def remap_chunk_relationships(
    relationships: list[dict[str, Any]] | None, raw_to_canonical: dict[str, str]
) -> list[dict[str, Any]]:
    """Remap a chunk's silver relationships (chunk-local names) onto canonical
    entities. Drops edges whose endpoints didn't canonicalize or are self-loops."""
    remapped: list[dict[str, Any]] = []
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


# ── Conflict pipeline ─────────────────────────────────────────────────────────

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
    chunk answers, each with its own intent, entities, and reasoning-pivot stage_record."""

    async def _attempt() -> list[QuestionPivots]:
        try:
            result, _, _ = await _llm_generate_json(
                SYSTEM_PROMPTS["pivot-question"], chunk_text, max_tokens=768,
                system_type="pivot-question",
            )
        except Exception:
            return []
        return _parse_question_pivots_entries(result.get("questions") or [])

    return await _retry_while_empty(_attempt, max_attempts=cfg.CONFLICT_SEMANTIC_RETRY_ATTEMPTS)


async def _retrieve_candidate_chunks(*, qdrant, tenant_id, knowledge_base, embedding: list[float]) -> list:
    """Stage 2: existing chunks nearest the incoming one that clear the similarity floor.

    Probed with the incoming chunk's own vector, never a question vector (query→passage
    peaks ~0.55, under the chunk-to-chunk floor) and never gated on entities (one company
    canonicalizes as both `helios` and `công_ty_helios`). Stage 3 is the semantic gate.
    """
    search_response = await qdrant_vector_search(qdrant, SearchRequest(
        tenant_id=tenant_id,
        collection_name=cfg.QDRANT_COLLECTION,
        query_vector=embedding,
        limit=cfg.CONFLICT_TOP_K_DOCS,
        knowledge_base=knowledge_base or None,
    ))
    hits = search_response.data if search_response.code == 200 and search_response.data else []
    return [hit for hit in hits if hit.score >= cfg.CONFLICT_SIMILARITY_THRESHOLD]


def _hit_summary(hit: Any) -> str:
    """Existing-chunk summary text carried in a Qdrant search hit's payload."""
    return (hit.payload or {}).get("summary", "")


async def _extract_chunk_pivots(question: str, chunk_text: str) -> list[str]:
    """Stage 3, first CoT prompt (retried while empty): the reasoning-pivot stage_record
    a candidate chunk offers toward the fixed question, in the stage-1 triplet
    structure."""
    user = f"Câu hỏi: {question}\n\nĐoạn văn bản: {chunk_text}"

    async def _attempt() -> list[str]:
        try:
            result, _, _ = await _llm_generate_json(
                SYSTEM_PROMPTS["pivot-chunk"], user, max_tokens=256, system_type="pivot-chunk",
            )
        except Exception:
            return []
        return [pivot for pivot in (result.get("pivots") or []) if isinstance(pivot, str) and pivot.strip()]

    return await _retry_while_empty(_attempt, max_attempts=cfg.CONFLICT_SEMANTIC_RETRY_ATTEMPTS)


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
    except Exception:
        return {criterion: (1.0 if criterion == "irrelevant_score" else 0.0) for criterion in _PIVOT_CRITERIA}


async def _score_candidates(
        question: str, incoming_pivots: list[str], candidate_hits: list,
) -> list[tuple[Any, dict]]:
    """Stage 3 (CoT): score every candidate concurrently, dropping the irrelevant ones."""
    document_semaphore = asyncio.Semaphore(cfg.CONFLICT_DOCUMENT_CONCURRENCY)

    async def _score_bounded(hit: Any) -> dict:
        async with document_semaphore:
            existing_pivots = await _extract_chunk_pivots(question, _hit_summary(hit))
            return await _compare_pivot_sets(question, incoming_pivots, existing_pivots)

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


async def detect_pairwise_conflict(existing_content: str, incoming_content: str) -> tuple[dict, str] | None:
    """Batch-merge re-check: does incoming still conflict with the merged working
    content? Returns (scores, dominant_criterion) for the first qualifying question,
    or None when the two are compatible (safe to merge without human review)."""
    question_pivots_list = await _derive_question_pivots(
        incoming_content[:cfg.CONFLICT_QUESTION_SOURCE_CHAR_LIMIT]
    )
    for question_pivots in question_pivots_list:
        existing_pivots = await _extract_chunk_pivots(question_pivots.question, existing_content)
        scores = await _compare_pivot_sets(question_pivots.question, question_pivots.pivots, existing_pivots)
        if scores["irrelevant_score"] >= cfg.CONFLICT_IRRELEVANT_THRESHOLD:
            continue
        dominant_criterion = _dominant_conflict_criterion(scores)
        if scores[dominant_criterion] >= cfg.CONFLICT_MERGE_RECHECK_THRESHOLD:
            return scores, dominant_criterion
    return None


async def decide_keep_table(merged_content: str) -> bool:
    """Merge finalize: ask the LLM whether merged content that involved a table still
    presents that table. Conservative on failure — keep the table (True)."""
    try:
        result, _, _ = await _llm_generate_json(
            SYSTEM_PROMPTS["table-merge"], merged_content[:cfg.CONFLICT_QUESTION_SOURCE_CHAR_LIMIT],
            max_tokens=16, system_type="table-merge",
        )
        return bool(result.get("keep_table", True))
    except Exception:
        return True


async def _run_question_pipelines(
        question_pivots_list: list[QuestionPivots], candidate_hits: list,
) -> list[QuestionPipelineResult]:
    """Stage 3 for every question against the chunk's shared candidate set,
    bounded by CONFLICT_QUESTION_CONCURRENCY."""
    question_semaphore = asyncio.Semaphore(cfg.CONFLICT_QUESTION_CONCURRENCY)

    async def _score_bounded(question_pivots: QuestionPivots) -> QuestionPipelineResult:
        async with question_semaphore:
            qualifying = await _score_candidates(
                question_pivots.question, question_pivots.pivots, candidate_hits,
            )
            return QuestionPipelineResult(question_pivots, candidate_hits, qualifying)

    return list(await asyncio.gather(
        *(_score_bounded(question_pivots) for question_pivots in question_pivots_list)
    ))


async def _find_open_block_batch(postgres, tenant_id, existing_block_id) -> str | None:
    """Return the id of the open batch anchored on this block, or None."""
    async with postgres.get_client() as session:
        batch_id = await session.scalar(
            sa_select(KBConflictBatchORM.batch_id)
            .where(KBConflictBatchORM.tenant_id == uuid.UUID(tenant_id))
            .where(KBConflictBatchORM.existing_block_id == uuid.UUID(existing_block_id))
            .where(KBConflictBatchORM.status != ConflictStatus.RESOLVED.value)
            .where(KBConflictBatchORM.is_deleted.is_(False))
            .limit(1)
        )
    return str(batch_id) if batch_id else None


async def fetch_active_block_version(postgres, block_id: str) -> tuple[str, int] | None:
    """Return (content, version_number) of a block's active version, or None."""
    async with postgres.get_client() as session:
        result = await session.execute(
            sa_select(KBTextBlockVersionORM)
            .join(KBTextBlockORM, KBTextBlockVersionORM.block_id == KBTextBlockORM.block_id)
            .where(KBTextBlockORM.block_id == uuid.UUID(block_id))
            .where(KBTextBlockVersionORM.is_active.is_(True))
            .where(KBTextBlockVersionORM.is_deleted.is_(False))
            .limit(1)
        )
        row = result.scalars().first()
    return (row.content, row.version_number) if row is not None else None


async def _find_or_create_block_batch(
        postgres, database, *, tenant_id, knowledge_base, batch_title, existing_block_id, existing_summary,
        stage_record: StageChain,
) -> str:
    """Return the open batch anchored on this block, creating it on the first conflict.

    Documents promote concurrently, so two chunks can reach the insert for the same
    block at once; idx_kbconflictbatch_open_block makes the loser fail and re-read the
    winner's batch rather than opening a second one. A freshly created batch has no
    rollback registered: a concurrent chunk may join it before this one fails.
    """
    batch_id = await _find_open_block_batch(postgres, tenant_id, existing_block_id)
    if batch_id is not None:
        return batch_id

    batch_response = await postgres.insert(KBConflictBatchInsert(
        tenant_id=tenant_id, batch_title=batch_title, existing_block_id=existing_block_id,
        status=ConflictStatus.PENDING,
    ))
    if batch_response.code != 200:
        batch_id = await _find_open_block_batch(postgres, tenant_id, existing_block_id)
        if batch_id is None:
            raise RuntimeError(
                f"conflict batch insert returned code {batch_response.code} for {batch_title!r}"
            )
        return batch_id
    batch_id = batch_response.data["batch_id"]
    stage_record.push(f"conflict_batch_created_{batch_id}")

    # Seed the working text with the block's current content — Mongo is the display
    # source until the batch resolves and Postgres gets the final version.
    active_version = await fetch_active_block_version(postgres, existing_block_id)
    await conflict_store.create_resolution_batch(
        database, batch_id=batch_id, tenant_id=tenant_id, knowledge_base=knowledge_base,
        existing_summary=existing_summary,
        existing_content=active_version[0] if active_version else None,
    )
    stage_record.push(f"conflict_batch_working_text_seeded_{batch_id}")
    return batch_id


def _build_conflict_payload(
        *, conflict_id, batch_id, tenant_id, data_id, block_index, approved_by, content, summary,
        embedding, table_involved, table, embed_model_id, chunk, best_scores,
) -> dict:
    """The incoming chunk the resolver replays, keyed by (tenant_id, conflict_id)."""
    return {
        "conflict_id": conflict_id,
        "tenant_id": tenant_id,
        "batch_id": batch_id,
        "data_id": data_id,
        "block_index": block_index,
        "approved_by": approved_by,
        "content": content,
        "summary": summary,
        "embedding": embedding,
        "table_involved": table_involved,
        "table": table,
        "embed_model_id": embed_model_id,
        "entities": chunk.get("entities") or [],
        "relationships": chunk.get("relationships") or [],
        # Not read by code — the only record of why the LLM scored this conflict as it did.
        "conflict_criteria_scores": best_scores,
    }


async def _delete_conflict_row(postgres, conflict_id: str) -> None:
    """Drop a conflict index row whose payload never landed, or unwind a stage_record rollback.

    Swallows its own failure: a rollback that fails must not raise over whatever
    triggered it in the first place.
    """
    try:
        async with postgres.get_client() as session:
            try:
                await session.execute(
                    sa_delete(KBConflictORM).where(KBConflictORM.conflict_id == uuid.UUID(conflict_id))
                )
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    except Exception:
        pass


async def _rollback_conflict_record(postgres, database, tenant_id: str, conflict_id: str) -> None:
    try:
        await conflict_store.drop_payloads(database, tenant_id, [conflict_id])
    except Exception:
        pass
    await _delete_conflict_row(postgres, conflict_id)


async def _persist_conflict_record(
        *, postgres, database, conflict_batch_id, existing_block_id, tenant_id, data_id,
        conflict_type: ConflictType, severity: ConflictSeverity, dominant_criterion: str,
        question: str, best_scores: dict, payload_fields: dict, stage_record: StageChain,
) -> None:
    """Write the conflict's index row to Postgres and its chunk payload to Mongo.

    Raises on either store, deleting the index row if the payload fails: a conflict
    whose chunk is missing can be reviewed but never resolved.
    """
    insert_response = await postgres.insert(KBConflictInsert(
        tenant_id=tenant_id,
        data_id=data_id,
        conflict_type=conflict_type,
        severity=severity,
        batch_id=conflict_batch_id,
        status=ConflictStatus.PENDING,
        detailed_explanation=f"{dominant_criterion} ({best_scores[dominant_criterion]:.2f}) — {question}",
    ))
    if insert_response.code != 200:
        raise RuntimeError(f"conflict insert returned code {insert_response.code} block={existing_block_id}")
    conflict_id = insert_response.data["conflict_id"]

    try:
        await conflict_store.save_payload(database, _build_conflict_payload(
            conflict_id=conflict_id, batch_id=conflict_batch_id, tenant_id=tenant_id,
            data_id=data_id, best_scores=best_scores, **payload_fields,
        ))
    except Exception:
        await _delete_conflict_row(postgres, conflict_id)
        raise

    stage_record.push(
        f"conflict_recorded_{conflict_id}",
        rollback_func=_rollback_conflict_record,
        rollback_args=(postgres, database, tenant_id, conflict_id),
    )


async def _emit_conflict_timing(emitter, block_index, total_candidates, pipeline_started) -> None:
    """Emit the per-chunk conflict-pipeline duration event."""
    await emitter.emit("gold.conflict.timing", {
        "index": block_index,
        "candidates": total_candidates,
        "duration_ms": round((_time.monotonic() - pipeline_started) * 1000),
    })


def _strongest_conflict_per_existing_block(results: list) -> dict:
    """Collapse per-question winners to the single strongest conflict per existing
    block: existing_block_id → (best_hit, best_scores, dominant_criterion, question_pivots)."""
    strongest_by_block: dict = {}
    for result in results:
        if not result.qualifying:
            continue
        best_hit, best_scores, dominant_criterion = _select_strongest_conflict(result.qualifying)
        existing_block_id = str(best_hit.id)
        incumbent = strongest_by_block.get(existing_block_id)
        if incumbent is None or best_scores[dominant_criterion] > incumbent[1][incumbent[2]]:
            strongest_by_block[existing_block_id] = (
                best_hit, best_scores, dominant_criterion, result.question_pivots,
            )
    return strongest_by_block


async def _detect_and_record_conflict(
        *, postgres, qdrant, database, tenant_id, data_id, doc_name, knowledge_base,
        embed_model_id, approved_by, block_index, content, summary, table_involved,
        table, embedding, chunk, emitter, stage_record: StageChain,
) -> bool:
    """Reasoning-pivot conflict pipeline root; True when this chunk conflicts.

    Every existing block this chunk clashes with gets a batch — created on the first
    clash, joined by later chunks that clash with the same block.
    """
    pipeline_started = _time.monotonic()

    # Stage 2 — the existing chunks this one could clash with. Runs first so a chunk
    # with nothing similar in the KB costs one vector search and no LLM call.
    candidate_hits = await _retrieve_candidate_chunks(
        qdrant=qdrant, tenant_id=tenant_id, knowledge_base=knowledge_base, embedding=embedding,
    )
    await emitter.emit("gold.conflict.check", {
        "index": block_index,
        "candidates": len(candidate_hits),
        "threshold": cfg.CONFLICT_SIMILARITY_THRESHOLD,
        "top_score": round(max((hit.score for hit in candidate_hits), default=0.0), 4),
    })
    if not candidate_hits:
        await _emit_conflict_timing(emitter, block_index, 0, pipeline_started)
        return False

    # Stage 1 — every distinct question this chunk answers, each with its own pivots
    question_pivots_list = await _derive_question_pivots(
        summary or content[:cfg.CONFLICT_QUESTION_SOURCE_CHAR_LIMIT]
    )

    # Stage 3 — score the candidates per question, concurrently
    results = await _run_question_pipelines(question_pivots_list, candidate_hits)

    strongest_by_block = _strongest_conflict_per_existing_block(results)
    if not strongest_by_block:
        await _emit_conflict_timing(emitter, block_index, len(candidate_hits), pipeline_started)
        return False

    # Storing conflict — one batch per existing block, incoming chunk appended into it.
    # Any store failure raises: the caller must not commit a chunk whose conflict went
    # unrecorded, or the contradiction it was holding back silently enters the KB.
    for existing_block_id, (best_hit, best_scores, dominant_criterion, question_pivots) in strongest_by_block.items():
        conflict_type = _CRITERION_CONFLICT_TYPE[dominant_criterion]
        severity = _CRITERION_SEVERITY[dominant_criterion]
        existing_summary = _hit_summary(best_hit)
        batch_title = f"Gold pipeline · {doc_name or data_id} · block {existing_block_id[:8]}"
        conflict_batch_id = await _find_or_create_block_batch(
            postgres, database, tenant_id=tenant_id, knowledge_base=knowledge_base,
            batch_title=batch_title, existing_block_id=existing_block_id, existing_summary=existing_summary,
            stage_record=stage_record,
        )
        await _persist_conflict_record(
            postgres=postgres, database=database, conflict_batch_id=conflict_batch_id,
            existing_block_id=existing_block_id, tenant_id=tenant_id, data_id=data_id,
            conflict_type=conflict_type, severity=severity, dominant_criterion=dominant_criterion,
            question=question_pivots.question, best_scores=best_scores,
            payload_fields=dict(
                block_index=block_index, approved_by=approved_by, content=content, summary=summary,
                embedding=embedding, table_involved=table_involved, table=table,
                embed_model_id=embed_model_id, chunk=chunk,
            ),
            stage_record=stage_record,
        )
        await emitter.emit("gold.conflict.found", {
            "index": block_index,
            "conflict_type": conflict_type.value,
            "severity": severity.value,
            "dominant_criterion": dominant_criterion,
            "question": question_pivots.question,
            "existing_summary": existing_summary,
            "similarity": round(best_hit.score, 4),
        })

    await _emit_conflict_timing(emitter, block_index, len(candidate_hits), pipeline_started)
    return True


async def _add_chunk_to_graph(
    *, graph, tenant_id, knowledge_base, data_id, block_id, chunk, entities: ChunkEntities,
    embed_model_id, block_index, emitter, stage_record: StageChain,
) -> tuple[bool, str]:
    """Add a committed chunk's graph edges; returns (failed, error_message)."""
    try:
        await graph.add_chunk(
            tenant_id=tenant_id,
            knowledge_base=knowledge_base,
            data_id=data_id,
            block_id=block_id,
            entities=entities.graph_nodes(),
            relationships=remap_chunk_relationships(chunk.get("relationships"), entities.raw_to_canonical),
            embed_model_id=embed_model_id,
        )
        stage_record.push(
            f"chunk_graph_updated_{block_index}",
            rollback_func=graph.remove_chunk,
            rollback_kwargs={"tenant_id": tenant_id, "knowledge_base": knowledge_base, "block_id": block_id},
        )
        await emitter.emit("gold.chunk.entities", {"index": block_index, "entities": entities.canonicals})
        return False, ""
    except Exception as exc:
        await emitter.emit("gold.graph.failed", {"index": block_index, "error": str(exc)[:300]})
        return True, str(exc)[:300]


async def _process_chunk(
    *, chunk, postgres, qdrant, graph, model, tenant_id, data_id, doc_name, knowledge_base,
    embed_model_id, approved_by, database, emitter, stage_record: StageChain,
    pii_mask_types: list[str] | None = None,
) -> dict:
    block_index = chunk["block_index"]
    # Compliance: redact PII in the chunk IN PLACE before it is embedded,
    # summarised, conflict-checked, canonicalised or persisted — so no raw PII
    # reaches the model, Qdrant, Postgres or the knowledge graph. Mutating the
    # chunk dict means every downstream `chunk.get(...)` (table, entities,
    # relationships, the conflict snapshot, the graph writer) sees masked data.
    if pii_mask_types:
        masked_content, _content_pii = pii_mask(chunk["content"], pii_mask_types)
        chunk["content"] = masked_content
        if _content_pii:
            await emitter.emit("gold.pii.masked", {"index": block_index, "counts": _content_pii})
        for _field in ("table", "entities", "relationships"):
            if chunk.get(_field):
                chunk[_field], _ = mask_deep(chunk[_field], pii_mask_types)
    content = chunk["content"]
    summary = (chunk.get("summary") or "").strip() or _heuristic_summary(content)
    if pii_mask_types:
        summary, _ = pii_mask(summary, pii_mask_types)
        chunk["summary"] = summary
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
        postgres=postgres, qdrant=qdrant, database=database, tenant_id=tenant_id,
        data_id=data_id, doc_name=doc_name, knowledge_base=knowledge_base, embed_model_id=embed_model_id,
        approved_by=approved_by, block_index=block_index, content=content, summary=summary,
        table_involved=chunk.get("table_involved", False), table=chunk.get("table"),
        embedding=embedding, chunk=chunk, emitter=emitter, stage_record=stage_record,
    )
    if is_conflict:
        return {"outcome": "conflict"}

    entities = await canonicalize_chunk_entities(
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
            "entities": entities.canonicals,
            "embedding_model_id": embed_model_id,
            "knowledge_base": knowledge_base,
        },
        embedding=embedding,
        summary=summary,
        approved_by=approved_by,
        embed_model_id=embed_model_id,
        knowledge_base=knowledge_base,
        entities=entities.canonicals,
        entity_type_fields=entities.qdrant_type_fields(),
        emitter=emitter,
        stage_record=stage_record,
    )
    await emitter.emit("gold.commit.ok", {
        "index": block_index,
        "block_id": block_id,
        "embedding_model_id": embed_model_id,
    })

    graph_failed, graph_error = await _add_chunk_to_graph(
        graph=graph, tenant_id=tenant_id, knowledge_base=knowledge_base, data_id=data_id,
        block_id=block_id, chunk=chunk, entities=entities, embed_model_id=embed_model_id,
        block_index=block_index, emitter=emitter, stage_record=stage_record,
    )
    return {
        "outcome": "committed", "summary": summary, "intents": intents,
        "graph_failed": graph_failed, "graph_error": graph_error,
    }


async def _reduce_document_summary(committed_summaries, logger: IngestionLogger, emitter) -> str:
    await logger.progress(status="GOLD_REDUCE")
    async with emitter.step("gold.doc.reduce", summaries=len(committed_summaries)) as reduce_scope:
        doc_summary = await _reduce_summaries(committed_summaries, emitter)
        reduce_scope.set(summary=doc_summary, chars=len(doc_summary))
    return doc_summary


async def _write_doc_summary_point(
    *, qdrant, model, logger: IngestionLogger, data_id, tenant_id, knowledge_base, embed_model_id,
    doc_summary, top_intents, committed_count, emitter, stage_record: StageChain,
) -> None:
    await logger.progress(status="GOLD_GRAPH")
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
        stage_record.push(
            f"doc_summary_written_{data_id}",
            rollback_func=qdrant.delete,
            rollback_kwargs={
                "collection_name": cfg.QDRANT_DOC_COLLECTION,
                "points_selector": qmodels.PointIdsList(points=[data_id]),
            },
        )
        doc_scope.set(intents=top_intents)


async def _rollback_document_tier(postgres, data_id: str) -> None:
    async with postgres.get_client() as session:
        try:
            await session.execute(
                sa_update(KBDataORM)
                .where(KBDataORM.data_id == uuid.UUID(data_id))
                .values(current_tier=Tier.SILVER.value)
            )
            await session.commit()
        except Exception:
            await session.rollback()


async def _update_document_tier(postgres, data_id, top_intents, stage_record: StageChain) -> None:
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
    stage_record.push(
        f"document_tier_updated_{data_id}",
        rollback_func=_rollback_document_tier,
        rollback_args=(postgres, data_id),
    )


async def set_conflict_batch_status(postgres, conflict_batch_id, status_value: str) -> None:
    """Update a conflict batch's index-row status.

    Raises: this drives the batch state machine, and a silent failure strands the
    batch in whatever status it was claimed under, where nothing picks it up again.
    """
    if conflict_batch_id is None:
        return
    async with postgres.get_client() as session:
        try:
            await session.execute(
                sa_update(KBConflictBatchORM)
                .where(KBConflictBatchORM.batch_id == uuid.UUID(conflict_batch_id))
                .values(status=status_value)
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _record_gold_lifecycle(postgres, data_id, approved_by, counts, embed_model_id) -> None:
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
    except Exception:
        pass


async def _load_pii_policy(postgres: PostgresClient, tenant_id: str) -> list[str]:
    """Return the PII categories to redact for this tenant (empty list = masking
    off). The ``compliance_settings`` table is owned by auth-api and may not exist
    yet on a fresh stack, so any lookup error is treated as 'masking disabled'."""
    try:
        async with postgres.get_client() as session:
            result = await session.execute(
                sa_text(
                    "SELECT pii_masking, pii_types FROM compliance_settings WHERE tenant_id = :tid"
                ),
                {"tid": tenant_id},
            )
            row = result.mappings().first()
    except Exception as exc:
        log.warning("Gold: PII policy lookup failed tenant=%s: %s — masking disabled", tenant_id, exc)
        return []
    if not row or not row["pii_masking"]:
        return []
    raw = (row["pii_types"] or "").strip()
    return [seg for seg in (part.strip().upper() for part in raw.split(",")) if seg]


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
) -> dict[str, Any]:
    database = mongo.get_client()
    logger = IngestionLogger(db=database, data_id=data_id)
    stage_record = StageChain()

    await logger.logging("Gold stage started — extracting entities", status="GRAPH_EXTRACTING", stage="gold")
    try:
        embed_model_id = await _get_collection_embed_model_id(postgres, collection_name=cfg.QDRANT_COLLECTION)
        log.info("Gold: collection '%s' → embed_model_id=%s", cfg.QDRANT_COLLECTION, embed_model_id or "none")
        doc_name, knowledge_base = await _get_data_row(postgres, data_id)
        log.info("Gold: data_id=%s → knowledge_base=%s", data_id, knowledge_base or "none")

        # Compliance: resolve the tenant's PII-masking policy once (empty = off).
        pii_mask_types = await _load_pii_policy(postgres, tenant_id)
        if pii_mask_types:
            log.info("Gold: PII masking ENABLED tenant=%s types=%s", tenant_id, pii_mask_types)

        staging = await database["kb_silver_staging"].find_one({"data_id": data_id})
        if not staging:
            raise ValueError(f"No silver staging found for data_id={data_id}")
        chunks: list[dict] = staging.get("chunks", [])
        log.info("Gold: processing %d chunks data_id=%s", len(chunks), data_id)

        counts = {"committed": 0, "conflicted": 0, "failed": 0, "skipped": 0, "graph_failed": 0}
        committed_summaries: list[str] = []  # document order — feeds the reduce step
        intent_counter: Counter[str] = Counter()

        async with emitter.step("gold", total_chunks=len(chunks)) as gold_scope:
            # Drive the /progress % bar from Mongo: total now, processed bumped per chunk.
            await logger.progress(total=len(chunks), processed=0)
            # Erase any previous (possibly partial) promotion so retries never duplicate.
            await _cleanup_previous_promotion(
                postgres=postgres, qdrant=qdrant, graph=graph, database=database,
                tenant_id=tenant_id, data_id=data_id, knowledge_base=knowledge_base, stage_record=stage_record,
            )

            # Per-chunk work. conc=1 → strictly sequential (legacy path). conc>1 →
            # bounded gather so the per-chunk LLM+embed waits overlap and actually use
            # OLLAMA_NUM_PARALLEL. Aggregation below is identical either way; asyncio is
            # cooperative so the `counts`/`done` += mutations are atomic between awaits.
            conc = max(1, cfg.GOLD_CHUNK_CONCURRENCY)
            done = {"n": 0}

            async def _handle_chunk(chunk: dict[str, Any]) -> dict[str, Any] | None:
                block_index = chunk["block_index"]
                result: dict[str, Any] | None = None
                try:
                    async with emitter.step("gold.chunk", index=block_index, length=len(chunk["content"])):
                        result = await _process_chunk(
                            chunk=chunk, postgres=postgres, qdrant=qdrant, graph=graph, model=model,
                            tenant_id=tenant_id, data_id=data_id, doc_name=doc_name, knowledge_base=knowledge_base,
                            embed_model_id=embed_model_id, approved_by=approved_by, database=database,
                            emitter=emitter, stage_record=stage_record, pii_mask_types=pii_mask_types,
                        )
                except Exception as exc:
                    counts["failed"] += 1
                    await logger.logging(
                        f"Chunk {block_index} failed during gold promotion: {exc}", level="ERROR",
                    )
                else:
                    if result.get("graph_failed"):
                        await logger.logging(
                            f"Graph update failed for chunk {block_index}: {result.get('graph_error', '')}",
                            level="ERROR",
                        )
                done["n"] += 1
                await logger.progress(processed=done["n"])
                return result

            if conc <= 1:
                results = [await _handle_chunk(chunk) for chunk in chunks]
            else:
                sem = asyncio.Semaphore(conc)

                async def _guarded(chunk: dict[str, Any]) -> dict[str, Any] | None:
                    async with sem:
                        return await _handle_chunk(chunk)

                results = await asyncio.gather(*(_guarded(chunk) for chunk in chunks))

            for result in results:
                if result is None:
                    continue
                if result["outcome"] == "conflict":
                    counts["conflicted"] += 1
                elif result["outcome"] == "committed":
                    counts["committed"] += 1
                    committed_summaries.append(result["summary"])
                    intent_counter.update(result["intents"])
                    if result.get("graph_failed"):
                        counts["graph_failed"] += 1

            # All chunks processed — fill the per-chunk band before the doc-rollup phases.
            await logger.progress(processed=len(chunks))
            top_intents = [intent for intent, _ in intent_counter.most_common(5)]

            # Phases 2-3: document rollup > doc-summary point.
            if committed_summaries:
                doc_summary = await _reduce_document_summary(committed_summaries, logger, emitter)
                await _write_doc_summary_point(
                    qdrant=qdrant, model=model, logger=logger, data_id=data_id, tenant_id=tenant_id,
                    knowledge_base=knowledge_base, embed_model_id=embed_model_id, doc_summary=doc_summary,
                    top_intents=top_intents, committed_count=counts["committed"], emitter=emitter, stage_record=stage_record,
                )
            else:
                await logger.logging(
                    f"No committed chunks for data_id={data_id} — skipping document rollup "
                    f"(no doc point, document invisible to 2-step retrieval)", level="WARNING",
                )

            await _update_document_tier(postgres, data_id=data_id, top_intents=top_intents, stage_record=stage_record)
            await _record_gold_lifecycle(
                postgres, data_id=data_id, approved_by=approved_by, counts=counts, embed_model_id=embed_model_id,
            )

            gold_scope.set(
                committed=counts["committed"], conflicted=counts["conflicted"],
                skipped=counts["skipped"], failed=counts["failed"], graph_failed=counts["graph_failed"],
            )
    except Exception as exc:
        await logger.logging(f"Gold stage failed: {exc}", level="ERROR", status="FAILED")
        await stage_record.unwind()
        await emitter.emit("pipeline.error", {"stage": "gold", "error": str(exc)[:300]})
        raise

    await logger.logging(
        f"Gold complete — {counts['committed']} blocks committed, {counts['conflicted']} conflicts detected",
        status="PUBLISHED",
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
