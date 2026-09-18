"""Concurrent per-chunk summarization for the silver pipeline."""
from __future__ import annotations

import asyncio
from typing import Any

from libs.basemodel.services_databaseconnector.postgres_model import ReadJoinRequest
from libs.ingestion.silver.shared import summarize_chunk
from libs.ingestion import config as cfg
from libs.ingestion.log_writer import IngestionLogger
from libs.ingestion.pipeline_events import NULL_EMITTER, PipelineEventEmitter


async def _get_extraction_addendum(postgres: Any, tenant_id: str) -> str:
    """The tenant's Policy Center "Custom Extraction Addendum"
    (`KBExtractionPolicy.custom_override`) — same read pattern as
    `filtering.py`'s `_filter_chunks`. Empty when the tenant has never set one."""
    resp = await postgres.read(ReadJoinRequest(
        tenant_id=tenant_id, joins_table=["KBExtractionPolicy"], limit=1,
    ))
    if resp.code == 200 and resp.data:
        return str(resp.data[0].get("custom_override") or "").strip()
    return ""


async def _summarize_chunks(
    chunks: list[dict[str, Any]],
    logger: IngestionLogger,
    emitter: PipelineEventEmitter = NULL_EMITTER,
    postgres: Any = None,
    tenant_id: str | None = None,
) -> list[dict[str, Any]]:
    await logger.progress(total=len(chunks), processed=0)
    summary_semaphore = asyncio.Semaphore(cfg.SILVER_SUMMARY_CONCURRENCY)
    completed_count = 0
    extraction_addendum = (
        await _get_extraction_addendum(postgres, tenant_id) if postgres is not None and tenant_id else ""
    )

    async def _summarize_one(chunk: dict[str, Any]) -> dict[str, Any]:
        nonlocal completed_count
        async with summary_semaphore:
            result = await summarize_chunk(chunk["content"], extraction_addendum)
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
