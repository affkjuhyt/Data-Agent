"""Silver-stage orchestration."""
from __future__ import annotations

import json
from typing import Any

from libs.basemodel.services_databaseconnector.postgres_model import SourceType, Tier
from libs.database_connector.minio_connector import MinIOClient
from libs.database_connector.mongo_connector import MongoClient
from libs.database_connector.postgres_connector import PostgresClient
from libs.ingestion.silver.filtering import _filter_chunks
from libs.ingestion.silver.parsing import (
    _parse_doc,
    _parse_image,
    _parse_video,
    _parse_web,
)
from libs.ingestion.silver.persistence import _load_kb_data, _persist_silver
from libs.ingestion.silver.summarization import _summarize_chunks
from libs.ingestion import config as cfg
from libs.ingestion.log_writer import IngestionLogger
from libs.ingestion.pipeline_events import NULL_EMITTER, PipelineEventEmitter
from libs.ingestion.stage_chain import StageChain


async def _parse_source(
    minio: MinIOClient,
    docling: Any,
    kb_data: dict[str, Any],
    source_type: SourceType,
    path: str,
    extension: str,
    emitter: PipelineEventEmitter,
) -> list[dict[str, Any]]:
    if source_type == SourceType.DOC:
        return await _parse_doc(docling, cfg.MINIO_BUCKET, path, extension)
    if source_type == SourceType.WEB:
        document_metadata = kb_data.get("metadata") or kb_data.get("doc_metadata") or {}
        if isinstance(document_metadata, str):
            try:
                document_metadata = json.loads(document_metadata)
            except Exception:
                document_metadata = {}
        web_url = (document_metadata or {}).get("url", "")
        return await _parse_web(docling, web_url)
    if source_type in (SourceType.IMAGE, SourceType.VIDEO):
        file_bytes = await minio.download(cfg.MINIO_BUCKET, path)
        if source_type == SourceType.IMAGE:
            return await _parse_image(file_bytes, extension)
        return await _parse_video(file_bytes)
    raise ValueError(f"Unsupported source_type for silver promotion: {source_type}")


async def promote_to_silver(
    minio: MinIOClient,
    postgres: PostgresClient,
    docling: Any,
    mongo: MongoClient,
    data_id: str,
    tenant_id: str,
    approved_by: str,
    emitter: PipelineEventEmitter = NULL_EMITTER,
) -> dict[str, Any]:
    database = mongo.get_client()
    logger = IngestionLogger(db=database, data_id=data_id)
    stage_record = StageChain()

    await logger.logging("Silver stage started", status="RUNNING", stage="silver")
    try:
        kb_data = await _load_kb_data(postgres, tenant_id=tenant_id, data_id=data_id)
        source_type = SourceType(kb_data["source_type"])
        path: str = kb_data["path"]
        extension: str = kb_data["extension"]
        parser_name = "docling" if source_type in (SourceType.DOC, SourceType.WEB) else "vlm"

        async with emitter.step("silver", source_type=source_type.value, parser=parser_name) as silver_scope:
            await logger.logging("Parsing document with Docling", status="CHUNKING")
            async with emitter.step("silver.parse", parser=parser_name) as parse_scope:
                raw_chunks = await _parse_source(
                    minio,
                    docling,
                    kb_data,
                    source_type,
                    path,
                    extension,
                    emitter,
                )
                parse_scope.set(chunks=len(raw_chunks))
            stage_record.push("parsed")

            for chunk in raw_chunks:
                content = chunk["content"]
                await emitter.emit("silver.chunk.found", {
                    "index": chunk["block_index"],
                    "preview": content[:200],
                    "length": len(content),
                    "table_involved": chunk.get("table_involved", False),
                    "table_name": (chunk.get("table") or {}).get("table_name"),
                })

            async with emitter.step("silver.filter") as filter_scope:
                accepted_chunks = await _filter_chunks(
                    postgres,
                    tenant_id=tenant_id,
                    chunks=raw_chunks,
                    emitter=emitter,
                )
                filter_scope.set(total=len(raw_chunks), accepted=len(accepted_chunks))
            stage_record.push("filtered")

            async with emitter.step("silver.summary", total=len(accepted_chunks)) as summary_scope:
                accepted_chunks = await _summarize_chunks(
                    accepted_chunks,
                    logger=logger,
                    emitter=emitter,
                    postgres=postgres,
                    tenant_id=tenant_id,
                )
                summary_scope.set(kept=len(accepted_chunks))
            stage_record.push("summarized")

            for new_index, chunk in enumerate(accepted_chunks):
                chunk["block_index"] = new_index

            await _persist_silver(
                postgres,
                mongo,
                data_id=data_id,
                tenant_id=tenant_id,
                source_type=source_type,
                accepted_chunks=accepted_chunks,
                raw_chunk_count=len(raw_chunks),
                approved_by=approved_by,
                stage_record=stage_record,
            )

            silver_scope.set(
                accepted=len(accepted_chunks),
                rejected=len(raw_chunks) - len(accepted_chunks),
                total=len(raw_chunks),
            )
    except Exception as exc:
        await logger.logging(f"Silver stage failed: {exc}", level="ERROR", status="FAILED")
        await stage_record.unwind()
        await emitter.emit("pipeline.error", {"stage": "silver", "error": str(exc)[:300]})
        raise

    await logger.logging(
        f"Silver complete — {len(accepted_chunks)} chunks accepted, queued for gold",
        status="EMBEDDING",
    )
    return {"data_id": data_id, "chunks_count": len(accepted_chunks), "layer": Tier.SILVER.value}
