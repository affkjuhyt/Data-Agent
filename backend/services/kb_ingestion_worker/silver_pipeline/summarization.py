"""Concurrent per-chunk summarization for the silver pipeline."""
from __future__ import annotations

import asyncio
from typing import Any

from services.kb_ingestion_worker.silver_pipeline.shared import summarize_chunk
from services.kb_ingestion_worker.utils import config as cfg
from services.kb_ingestion_worker.utils.log_writer import IngestionLogger
from services.kb_ingestion_worker.utils.pipeline_events import NULL_EMITTER, PipelineEventEmitter


async def _summarize_chunks(
    chunks: list[dict[str, Any]],
    logger: IngestionLogger,
    emitter: PipelineEventEmitter = NULL_EMITTER,
) -> list[dict[str, Any]]:
    await logger.progress(total=len(chunks), processed=0)
    summary_semaphore = asyncio.Semaphore(cfg.SILVER_SUMMARY_CONCURRENCY)
    completed_count = 0

    async def _summarize_one(chunk: dict[str, Any]) -> dict[str, Any]:
        nonlocal completed_count
        async with summary_semaphore:
            result = await summarize_chunk(chunk["content"])
        completed_count += 1
        await logger.progress(processed=completed_count)
        return result

    summary_results = await asyncio.gather(*(_summarize_one(chunk) for chunk in chunks))

    kept_chunks: list[dict[str, Any]] = []
    for chunk, summary_result in zip(chunks, summary_results):
        if summary_result["skip"] and not chunk.get("table_involved"):
            await emitter.emit("silver.summary.skipped", {
                "index": chunk["block_index"],
                "reason": "boilerplate_no_descriptive_content",
            })
            continue
        chunk["summary"] = summary_result["summary"]
        chunk["intents"] = summary_result["intents"]
        chunk["entities"] = summary_result.get("entities", [])
        chunk["relationships"] = summary_result.get("relationships", [])
        await emitter.emit("silver.summary.item", {
            "index": chunk["block_index"],
            "summary": summary_result["summary"],
            "intents": summary_result["intents"],
            "entities": [entity["name"] for entity in summary_result.get("entities", [])],
        })
        kept_chunks.append(chunk)
    return kept_chunks
