from __future__ import annotations

import asyncio
import logging
import time as _time
from collections import Counter
from typing import Any

from qdrant_client.async_qdrant_client import AsyncQdrantClient

from libs.basemodel.services_databaseconnector.postgres_model import Tier
from libs.database_connector.model_connector import ModelServiceClient
from libs.database_connector.mongo_connector import MongoClient
from libs.database_connector.postgres_connector import PostgresClient
from libs.shared.pii_detector import mask as pii_mask, mask_deep
from services.kb_ingestion_worker.gold_pipeline.conflicts import _detect_and_record_conflict
from services.kb_ingestion_worker.gold_pipeline.persistence import (
    _cleanup_previous_promotion,
    _commit_chunk_atomic,
    _get_collection_embed_model_id,
    _get_data_row,
    _load_pii_policy,
    _record_gold_lifecycle,
    _reduce_document_summary,
    _update_document_tier,
    _write_doc_summary_point,
)
from services.kb_ingestion_worker.gold_pipeline.shared import (
    ChunkEntities,
    _heuristic_summary,
    canonicalize_chunk_entities,
    remap_chunk_relationships,
)
from services.kb_ingestion_worker.utils import config as cfg
from services.kb_ingestion_worker.utils.graph_store import GraphStore
from services.kb_ingestion_worker.utils.log_writer import IngestionLogger
from services.kb_ingestion_worker.utils.pipeline_events import NULL_EMITTER, PipelineEventEmitter
from services.kb_ingestion_worker.utils.stage_chain import StageChain

log = logging.getLogger(__name__)


async def _add_chunk_to_graph(
    *,
    graph,
    tenant_id,
    knowledge_base,
    data_id,
    block_id,
    chunk,
    entities: ChunkEntities,
    embed_model_id,
    block_index,
    emitter,
    stage_record: StageChain,
) -> tuple[bool, str]:
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
    *,
    chunk,
    postgres,
    qdrant,
    graph,
    model,
    tenant_id,
    data_id,
    doc_name,
    knowledge_base,
    embed_model_id,
    approved_by,
    database,
    emitter,
    stage_record: StageChain,
    pii_mask_types: list[str] | None = None,
) -> dict:
    block_index = chunk["block_index"]
    if pii_mask_types:
        masked_content, masked_counts = pii_mask(chunk["content"], pii_mask_types)
        chunk["content"] = masked_content
        if masked_counts:
            await emitter.emit("gold.pii.masked", {"index": block_index, "counts": masked_counts})
        for field_name in ("table", "entities", "relationships"):
            if chunk.get(field_name):
                chunk[field_name], _ = mask_deep(chunk[field_name], pii_mask_types)

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
    await emitter.emit(
        "gold.embed.complete",
        {
            "index": block_index,
            "dim": len(embedding),
            "duration_ms": round((_time.monotonic() - embed_started) * 1000),
        },
    )

    is_conflict = await _detect_and_record_conflict(
        postgres=postgres,
        qdrant=qdrant,
        database=database,
        tenant_id=tenant_id,
        data_id=data_id,
        doc_name=doc_name,
        knowledge_base=knowledge_base,
        embed_model_id=embed_model_id,
        approved_by=approved_by,
        block_index=block_index,
        content=content,
        summary=summary,
        table_involved=chunk.get("table_involved", False),
        table=chunk.get("table"),
        embedding=embedding,
        chunk=chunk,
        emitter=emitter,
        stage_record=stage_record,
    )
    if is_conflict:
        return {"outcome": "conflict"}

    entities = await canonicalize_chunk_entities(
        qdrant,
        postgres,
        model,
        tenant_id,
        chunk.get("entities") or [],
        knowledge_base=knowledge_base,
        embed_model_id=embed_model_id,
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
    await emitter.emit(
        "gold.commit.ok",
        {"index": block_index, "block_id": block_id, "embedding_model_id": embed_model_id},
    )

    graph_failed, graph_error = await _add_chunk_to_graph(
        graph=graph,
        tenant_id=tenant_id,
        knowledge_base=knowledge_base,
        data_id=data_id,
        block_id=block_id,
        chunk=chunk,
        entities=entities,
        embed_model_id=embed_model_id,
        block_index=block_index,
        emitter=emitter,
        stage_record=stage_record,
    )
    return {
        "outcome": "committed",
        "summary": summary,
        "intents": intents,
        "graph_failed": graph_failed,
        "graph_error": graph_error,
    }


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

        pii_mask_types = await _load_pii_policy(postgres, tenant_id)
        if pii_mask_types:
            log.info("Gold: PII masking ENABLED tenant=%s types=%s", tenant_id, pii_mask_types)

        staging = await database["kb_silver_staging"].find_one({"data_id": data_id})
        if not staging:
            raise ValueError(f"No silver staging found for data_id={data_id}")
        chunks: list[dict] = staging.get("chunks", [])
        log.info("Gold: processing %d chunks data_id=%s", len(chunks), data_id)

        counts = {"committed": 0, "conflicted": 0, "failed": 0, "skipped": 0, "graph_failed": 0}
        committed_summaries: list[str] = []
        intent_counter: Counter[str] = Counter()

        async with emitter.step("gold", total_chunks=len(chunks)) as gold_scope:
            await logger.progress(total=len(chunks), processed=0)
            await _cleanup_previous_promotion(
                postgres=postgres,
                qdrant=qdrant,
                graph=graph,
                database=database,
                tenant_id=tenant_id,
                data_id=data_id,
                knowledge_base=knowledge_base,
                stage_record=stage_record,
            )

            concurrency = max(1, cfg.GOLD_CHUNK_CONCURRENCY)
            done = {"n": 0}

            async def _handle_chunk(chunk: dict[str, Any]) -> dict[str, Any] | None:
                block_index = chunk["block_index"]
                result: dict[str, Any] | None = None
                try:
                    async with emitter.step("gold.chunk", index=block_index, length=len(chunk["content"])):
                        result = await _process_chunk(
                            chunk=chunk,
                            postgres=postgres,
                            qdrant=qdrant,
                            graph=graph,
                            model=model,
                            tenant_id=tenant_id,
                            data_id=data_id,
                            doc_name=doc_name,
                            knowledge_base=knowledge_base,
                            embed_model_id=embed_model_id,
                            approved_by=approved_by,
                            database=database,
                            emitter=emitter,
                            stage_record=stage_record,
                            pii_mask_types=pii_mask_types,
                        )
                except Exception as exc:
                    counts["failed"] += 1
                    await logger.logging(f"Chunk {block_index} failed during gold promotion: {exc}", level="ERROR")
                else:
                    if result.get("graph_failed"):
                        await logger.logging(
                            f"Graph update failed for chunk {block_index}: {result.get('graph_error', '')}",
                            level="ERROR",
                        )
                done["n"] += 1
                await logger.progress(processed=done["n"])
                return result

            if concurrency <= 1:
                results = [await _handle_chunk(chunk) for chunk in chunks]
            else:
                semaphore = asyncio.Semaphore(concurrency)

                async def _guarded(chunk: dict[str, Any]) -> dict[str, Any] | None:
                    async with semaphore:
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

            await logger.progress(processed=len(chunks))
            top_intents = [intent for intent, _ in intent_counter.most_common(5)]

            if committed_summaries:
                doc_summary = await _reduce_document_summary(committed_summaries, logger, emitter)
                await _write_doc_summary_point(
                    qdrant=qdrant,
                    model=model,
                    logger=logger,
                    data_id=data_id,
                    tenant_id=tenant_id,
                    knowledge_base=knowledge_base,
                    embed_model_id=embed_model_id,
                    doc_summary=doc_summary,
                    top_intents=top_intents,
                    committed_count=counts["committed"],
                    emitter=emitter,
                    stage_record=stage_record,
                )
            else:
                await logger.logging(
                    f"No committed chunks for data_id={data_id} — skipping document rollup "
                    f"(no doc point, document invisible to 2-step retrieval)",
                    level="WARNING",
                )

            await _update_document_tier(postgres, data_id=data_id, top_intents=top_intents, stage_record=stage_record)
            await _record_gold_lifecycle(
                postgres,
                data_id=data_id,
                approved_by=approved_by,
                counts=counts,
                embed_model_id=embed_model_id,
            )

            gold_scope.set(
                committed=counts["committed"],
                conflicted=counts["conflicted"],
                skipped=counts["skipped"],
                failed=counts["failed"],
                graph_failed=counts["graph_failed"],
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
