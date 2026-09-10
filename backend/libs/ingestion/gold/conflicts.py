from __future__ import annotations

import asyncio
import time as _time
import uuid
from typing import Any

from sqlalchemy import delete as sa_delete, select as sa_select

from libs.basemodel.services_databaseconnector.postgres_model import (
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
    KBConflictBatchInsert,
    KBConflictInsert,
)
from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBConflictBatchORM,
    KBConflictORM,
)
from libs.basemodel.services_databaseconnector.qdrant_model import SearchRequest
from libs.database_connector.qdrant_connector import vector_search as qdrant_vector_search

from libs.ingestion.gold.persistence import fetch_active_block_version
from libs.ingestion.gold.shared import (
    QuestionPipelineResult,
    QuestionPivots,
    _llm_generate_json,
    _retry_while_empty,
    _to_score,
)
from libs.ingestion import config as cfg
from libs.ingestion import conflict_store
from libs.ingestion.prompts import GOLD_SYSTEM_PROMPTS as SYSTEM_PROMPTS
from libs.ingestion.stage_chain import StageChain

_PIVOT_CRITERIA: tuple[str, ...] = (
    "semantic_ambiguity_score",
    "semantic_multilingual_score",
    "temporal_time_score",
    "temporal_version_score",
    "factual_entities_score",
    "factual_relationships_score",
    "factual_quantity_score",
    "reasoning_logic_score",
    "constraint_policy_score",
    "missing_information_score",
    "duplicate_score",
    "irrelevant_score",
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


def _parse_question_pivots_entry(entry: dict) -> QuestionPivots | None:
    question = str(entry.get("question") or "").strip()
    pivots = [pivot for pivot in (entry.get("pivots") or []) if isinstance(pivot, str) and pivot.strip()]
    if not question or not pivots:
        return None
    entities = [
        entity_name
        for entity_name in (entry.get("question_entities") or [])
        if isinstance(entity_name, str) and entity_name.strip()
    ]
    return QuestionPivots(
        question=question,
        intent=str(entry.get("question_intent") or "").strip(),
        entities=entities,
        pivots=pivots,
    )


def _parse_question_pivots_entries(entries: list) -> list[QuestionPivots]:
    pivots_list: list[QuestionPivots] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        parsed = _parse_question_pivots_entry(entry)
        if parsed is not None:
            pivots_list.append(parsed)
    return pivots_list


async def _derive_question_pivots(chunk_text: str) -> list[QuestionPivots]:
    async def _attempt() -> list[QuestionPivots]:
        try:
            result, _, _ = await _llm_generate_json(
                SYSTEM_PROMPTS["pivot-question"],
                chunk_text,
                max_tokens=768,
                system_type="pivot-question",
            )
        except Exception:
            return []
        return _parse_question_pivots_entries(result.get("questions") or [])

    return await _retry_while_empty(_attempt, max_attempts=cfg.CONFLICT_SEMANTIC_RETRY_ATTEMPTS)


async def _retrieve_candidate_chunks(*, qdrant, tenant_id, knowledge_base, embedding: list[float]) -> list:
    search_response = await qdrant_vector_search(
        qdrant,
        SearchRequest(
            tenant_id=tenant_id,
            collection_name=cfg.QDRANT_COLLECTION,
            query_vector=embedding,
            limit=cfg.CONFLICT_TOP_K_DOCS,
            knowledge_base=knowledge_base or None,
        ),
    )
    hits = search_response.data if search_response.code == 200 and search_response.data else []
    return [hit for hit in hits if hit.score >= cfg.CONFLICT_SIMILARITY_THRESHOLD]


def _hit_summary(hit: Any) -> str:
    return (hit.payload or {}).get("summary", "")


async def _extract_chunk_pivots(question: str, chunk_text: str) -> list[str]:
    user = f"Câu hỏi: {question}\n\nĐoạn văn bản: {chunk_text}"

    async def _attempt() -> list[str]:
        try:
            result, _, _ = await _llm_generate_json(
                SYSTEM_PROMPTS["pivot-chunk"],
                user,
                max_tokens=256,
                system_type="pivot-chunk",
            )
        except Exception:
            return []
        return [pivot for pivot in (result.get("pivots") or []) if isinstance(pivot, str) and pivot.strip()]

    return await _retry_while_empty(_attempt, max_attempts=cfg.CONFLICT_SEMANTIC_RETRY_ATTEMPTS)


async def _compare_pivot_sets(question: str, incoming_pivots: list[str], existing_pivots: list[str]) -> dict:
    user = (
        f"Câu hỏi: {question}\n\n"
        f"Incoming pivots: {incoming_pivots}\n\n"
        f"Existing pivots: {existing_pivots}"
    )
    try:
        result, _, _ = await _llm_generate_json(
            SYSTEM_PROMPTS["pivot-compare"],
            user,
            max_tokens=256,
            system_type="pivot-compare",
        )
        return {criterion: _to_score(result.get(criterion), default=0.0) for criterion in _PIVOT_CRITERIA}
    except Exception:
        return {criterion: (1.0 if criterion == "irrelevant_score" else 0.0) for criterion in _PIVOT_CRITERIA}


async def _score_candidates(question: str, incoming_pivots: list[str], candidate_hits: list) -> list[tuple[Any, dict]]:
    document_semaphore = asyncio.Semaphore(cfg.CONFLICT_DOCUMENT_CONCURRENCY)

    async def _score_bounded(hit: Any) -> dict:
        async with document_semaphore:
            existing_pivots = await _extract_chunk_pivots(question, _hit_summary(hit))
            return await _compare_pivot_sets(question, incoming_pivots, existing_pivots)

    scores_by_candidate = await asyncio.gather(*(_score_bounded(hit) for hit in candidate_hits))
    return [
        (hit, scores)
        for hit, scores in zip(candidate_hits, scores_by_candidate)
        if scores["irrelevant_score"] < cfg.CONFLICT_IRRELEVANT_THRESHOLD
    ]


def _normalize_criteria_matrix(criteria_by_candidate: list[dict]) -> list[dict]:
    column_totals = {criterion: sum(row[criterion] for row in criteria_by_candidate) for criterion in _PIVOT_CRITERIA}
    return [
        {
            criterion: (row[criterion] / column_totals[criterion]) if column_totals[criterion] > 0 else 0.0
            for criterion in _PIVOT_CRITERIA
        }
        for row in criteria_by_candidate
    ]


def _dominant_conflict_criterion(raw_scores: dict) -> str:
    return max(
        (criterion for criterion in _PIVOT_CRITERIA if criterion != "irrelevant_score"),
        key=lambda criterion: raw_scores[criterion],
    )


def _select_strongest_conflict(qualifying: list[tuple[Any, dict]]) -> tuple[Any, dict, str]:
    normalized_matrix = _normalize_criteria_matrix([scores for _, scores in qualifying])
    best_index = max(
        range(len(qualifying)),
        key=lambda index: normalized_matrix[index][_dominant_conflict_criterion(qualifying[index][1])],
    )
    best_hit, best_scores = qualifying[best_index]
    return best_hit, best_scores, _dominant_conflict_criterion(best_scores)


async def detect_pairwise_conflict(existing_content: str, incoming_content: str) -> tuple[dict, str] | None:
    question_pivots_list = await _derive_question_pivots(incoming_content[: cfg.CONFLICT_QUESTION_SOURCE_CHAR_LIMIT])
    for question_pivots in question_pivots_list:
        existing_pivots = await _extract_chunk_pivots(question_pivots.question, existing_content)
        scores = await _compare_pivot_sets(question_pivots.question, question_pivots.pivots, existing_pivots)
        if scores["irrelevant_score"] >= cfg.CONFLICT_IRRELEVANT_THRESHOLD:
            continue
        dominant = _dominant_conflict_criterion(scores)
        if scores[dominant] >= cfg.CONFLICT_MERGE_RECHECK_THRESHOLD:
            return scores, dominant
    return None


async def decide_keep_table(merged_content: str) -> bool:
    try:
        result, _, _ = await _llm_generate_json(
            SYSTEM_PROMPTS["table-merge"],
            merged_content[: cfg.CONFLICT_QUESTION_SOURCE_CHAR_LIMIT],
            max_tokens=16,
            system_type="table-merge",
        )
        return bool(result.get("keep_table", True))
    except Exception:
        return True


async def _run_question_pipelines(
    question_pivots_list: list[QuestionPivots],
    candidate_hits: list,
) -> list[QuestionPipelineResult]:
    question_semaphore = asyncio.Semaphore(cfg.CONFLICT_QUESTION_CONCURRENCY)

    async def _score_bounded(question_pivots: QuestionPivots) -> QuestionPipelineResult:
        async with question_semaphore:
            qualifying = await _score_candidates(question_pivots.question, question_pivots.pivots, candidate_hits)
            return QuestionPipelineResult(question_pivots, candidate_hits, qualifying)

    return list(await asyncio.gather(*(_score_bounded(question_pivots) for question_pivots in question_pivots_list)))


async def _find_open_block_batch(postgres, tenant_id, existing_block_id) -> str | None:
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


async def _find_or_create_block_batch(
    postgres,
    database,
    *,
    tenant_id,
    knowledge_base,
    batch_title,
    existing_block_id,
    existing_summary,
    stage_record: StageChain,
) -> str:
    batch_id = await _find_open_block_batch(postgres, tenant_id, existing_block_id)
    if batch_id is not None:
        return batch_id

    batch_response = await postgres.insert(
        KBConflictBatchInsert(
            tenant_id=tenant_id,
            batch_title=batch_title,
            existing_block_id=existing_block_id,
            status=ConflictStatus.PENDING,
        )
    )
    if batch_response.code != 200:
        batch_id = await _find_open_block_batch(postgres, tenant_id, existing_block_id)
        if batch_id is None:
            raise RuntimeError(f"conflict batch insert returned code {batch_response.code} for {batch_title!r}")
        return batch_id

    batch_id = batch_response.data["batch_id"]
    stage_record.push(f"conflict_batch_created_{batch_id}")

    active_version = await fetch_active_block_version(postgres, existing_block_id)
    await conflict_store.create_resolution_batch(
        database,
        batch_id=batch_id,
        tenant_id=tenant_id,
        knowledge_base=knowledge_base,
        existing_summary=existing_summary,
        existing_content=active_version[0] if active_version else None,
    )
    stage_record.push(f"conflict_batch_working_text_seeded_{batch_id}")
    return batch_id


def _build_conflict_payload(
    *,
    conflict_id,
    batch_id,
    tenant_id,
    data_id,
    block_index,
    approved_by,
    content,
    summary,
    embedding,
    table_involved,
    table,
    embed_model_id,
    chunk,
    best_scores,
) -> dict:
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
        "conflict_criteria_scores": best_scores,
    }


async def _delete_conflict_row(postgres, conflict_id: str) -> None:
    try:
        async with postgres.get_client() as session:
            try:
                await session.execute(sa_delete(KBConflictORM).where(KBConflictORM.conflict_id == uuid.UUID(conflict_id)))
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
    *,
    postgres,
    database,
    conflict_batch_id,
    existing_block_id,
    tenant_id,
    data_id,
    conflict_type: ConflictType,
    severity: ConflictSeverity,
    dominant_criterion: str,
    question: str,
    best_scores: dict,
    payload_fields: dict,
    stage_record: StageChain,
) -> None:
    insert_response = await postgres.insert(
        KBConflictInsert(
            tenant_id=tenant_id,
            data_id=data_id,
            conflict_type=conflict_type,
            severity=severity,
            batch_id=conflict_batch_id,
            status=ConflictStatus.PENDING,
            detailed_explanation=f"{dominant_criterion} ({best_scores[dominant_criterion]:.2f}) — {question}",
        )
    )
    if insert_response.code != 200:
        raise RuntimeError(f"conflict insert returned code {insert_response.code} block={existing_block_id}")
    conflict_id = insert_response.data["conflict_id"]

    try:
        await conflict_store.save_payload(
            database,
            _build_conflict_payload(
                conflict_id=conflict_id,
                batch_id=conflict_batch_id,
                tenant_id=tenant_id,
                data_id=data_id,
                best_scores=best_scores,
                **payload_fields,
            ),
        )
    except Exception:
        await _delete_conflict_row(postgres, conflict_id)
        raise

    stage_record.push(
        f"conflict_recorded_{conflict_id}",
        rollback_func=_rollback_conflict_record,
        rollback_args=(postgres, database, tenant_id, conflict_id),
    )


async def _emit_conflict_timing(emitter, block_index, total_candidates, pipeline_started) -> None:
    await emitter.emit(
        "gold.conflict.timing",
        {
            "index": block_index,
            "candidates": total_candidates,
            "duration_ms": round((_time.monotonic() - pipeline_started) * 1000),
        },
    )


def _strongest_conflict_per_existing_block(results: list) -> dict:
    strongest_by_block: dict = {}
    for result in results:
        if not result.qualifying:
            continue
        best_hit, best_scores, dominant_criterion = _select_strongest_conflict(result.qualifying)
        existing_block_id = str(best_hit.id)
        incumbent = strongest_by_block.get(existing_block_id)
        if incumbent is None or best_scores[dominant_criterion] > incumbent[1][incumbent[2]]:
            strongest_by_block[existing_block_id] = (
                best_hit,
                best_scores,
                dominant_criterion,
                result.question_pivots,
            )
    return strongest_by_block


async def _detect_and_record_conflict(
    *,
    postgres,
    qdrant,
    database,
    tenant_id,
    data_id,
    doc_name,
    knowledge_base,
    embed_model_id,
    approved_by,
    block_index,
    content,
    summary,
    table_involved,
    table,
    embedding,
    chunk,
    emitter,
    stage_record: StageChain,
) -> bool:
    pipeline_started = _time.monotonic()
    candidate_hits = await _retrieve_candidate_chunks(
        qdrant=qdrant,
        tenant_id=tenant_id,
        knowledge_base=knowledge_base,
        embedding=embedding,
    )
    await emitter.emit(
        "gold.conflict.check",
        {
            "index": block_index,
            "candidates": len(candidate_hits),
            "threshold": cfg.CONFLICT_SIMILARITY_THRESHOLD,
            "top_score": round(max((hit.score for hit in candidate_hits), default=0.0), 4),
        },
    )
    if not candidate_hits:
        await _emit_conflict_timing(emitter, block_index, 0, pipeline_started)
        return False

    question_pivots_list = await _derive_question_pivots(summary or content[: cfg.CONFLICT_QUESTION_SOURCE_CHAR_LIMIT])
    results = await _run_question_pipelines(question_pivots_list, candidate_hits)

    strongest_by_block = _strongest_conflict_per_existing_block(results)
    if not strongest_by_block:
        await _emit_conflict_timing(emitter, block_index, len(candidate_hits), pipeline_started)
        return False

    for existing_block_id, (best_hit, best_scores, dominant_criterion, question_pivots) in strongest_by_block.items():
        conflict_type = _CRITERION_CONFLICT_TYPE[dominant_criterion]
        severity = _CRITERION_SEVERITY[dominant_criterion]
        existing_summary = _hit_summary(best_hit)
        batch_title = f"Gold pipeline · {doc_name or data_id} · block {existing_block_id[:8]}"
        conflict_batch_id = await _find_or_create_block_batch(
            postgres,
            database,
            tenant_id=tenant_id,
            knowledge_base=knowledge_base,
            batch_title=batch_title,
            existing_block_id=existing_block_id,
            existing_summary=existing_summary,
            stage_record=stage_record,
        )
        await _persist_conflict_record(
            postgres=postgres,
            database=database,
            conflict_batch_id=conflict_batch_id,
            existing_block_id=existing_block_id,
            tenant_id=tenant_id,
            data_id=data_id,
            conflict_type=conflict_type,
            severity=severity,
            dominant_criterion=dominant_criterion,
            question=question_pivots.question,
            best_scores=best_scores,
            payload_fields=dict(
                block_index=block_index,
                approved_by=approved_by,
                content=content,
                summary=summary,
                embedding=embedding,
                table_involved=table_involved,
                table=table,
                embed_model_id=embed_model_id,
                chunk=chunk,
            ),
            stage_record=stage_record,
        )
        await emitter.emit(
            "gold.conflict.found",
            {
                "index": block_index,
                "conflict_type": conflict_type.value,
                "severity": severity.value,
                "dominant_criterion": dominant_criterion,
                "question": question_pivots.question,
                "existing_summary": existing_summary,
                "similarity": round(best_hit.score, 4),
            },
        )

    await _emit_conflict_timing(emitter, block_index, len(candidate_hits), pipeline_started)
    return True
