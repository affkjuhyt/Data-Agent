from __future__ import annotations

import asyncio
import datetime
import json
import logging

import uuid as _uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func as sa_func, select as sa_select

from basemodel.services_databaseconnector.postgres_model import (
    Language, SourceType, Document, Image, Video, Web, Warehouse,
    ReadJoinRequest, WhereFilter,
)
from basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import KBTextBlockORM
from services.backend.dependencies.ui_context import get_ui_context, svc
from services.backend.pipeline.ingestion import config as cfg
from services.backend.pipeline.ingestion.bronze import BronzeUploadRequest
from services.backend.dev_seed import DEV_ROLE_PERMISSION_IDS

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipeline/ingestion", tags=["Ingestion"])

_META_CLS = {
    SourceType.DOC:       Document,
    SourceType.WEB:       Web,
    SourceType.IMAGE:     Image,
    SourceType.VIDEO:     Video,
    SourceType.WAREHOUSE: Warehouse,
}

_TERMINAL_STATUSES = {"PUBLISHED", "FAILED"}


class PromoteResponse(BaseModel):
    data_id: str
    stage: str
    message: str


@router.post("/bronze")
async def ingest_bronze(
    request: Request,
    file: UploadFile = File(...),
    abstract: str = Form(...),
    language: str = Form("english"),
    source_type: str = Form(...),
    doc_metadata: str = Form("{}"),
    knowledge_base_id: str = Form(""),
    ctx: dict = Depends(get_ui_context),
):
    """
    Ingest document to BRONZE layer.

    Context (tenant_id, user_id, role_id) comes from headers (X-Tenant-Id, X-User-Id, X-Role),
    NOT from request body. This ensures context cannot be spoofed by clients.
    """
    file_bytes = await file.read()
    filename   = file.filename or "upload"
    extension  = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    try:
        src  = SourceType(source_type)
        lang = Language(language)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid source type or language")

    try:
        meta_dict = json.loads(doc_metadata)
        meta_dict["source_type"] = src.value
        metadata_obj = _META_CLS[src](**meta_dict)
    except Exception as e:
        log.warning("ingestion: invalid doc_metadata from client: %s", e)
        raise HTTPException(status_code=422, detail="Invalid metadata format")

    # Map role name → RolePermissions UUID; fall back to role as-is if already a UUID.
    # Role comes from headers (X-Role), not request body
    resolved_role_id = str(DEV_ROLE_PERMISSION_IDS.get(ctx["role"], ctx["role"]))

    log.info("POST /ingestion/bronze tenant=%s source=%s size=%d", ctx["tenant_id"], src, len(file_bytes))
    try:
        return await svc(request, "ingestion").bronze(
            BronzeUploadRequest(
                tenant_id=ctx["tenant_id"],
                role_id=resolved_role_id,
                name=filename,
                extension=extension,
                language=lang.value,
                source_type=src.value,
                added_by=ctx["user_id"],
                abstract=abstract,
                doc_metadata=metadata_obj.model_dump(mode="json"),
                file_bytes=file_bytes,
                content_type=file.content_type or "application/octet-stream",
                knowledge_base_id=knowledge_base_id or None,
            ),
            svc(request, "minio").get_client(),
            svc(request, "postgres"),
        )
    except ValueError as e:
        log.warning("Bronze ingestion ValueError: %s", e)
        raise HTTPException(status_code=400, detail="Invalid ingestion parameters")
    except Exception as e:
        log.exception("Bronze ingestion error: %s", e)
        raise HTTPException(status_code=500, detail="Ingestion failed")


@router.post("/promote/{data_id}/silver", response_model=PromoteResponse)
async def promote_to_silver(
    data_id: str,
    request: Request,
    ctx: dict = Depends(get_ui_context),
):
    postgres       = svc(request, "postgres")
    kafka_producer = svc(request, "kafka_producer")
    mongo          = svc(request, "mongo")

    resp = await postgres.read(ReadJoinRequest(
        tenant_id=ctx["tenant_id"],
        joins_table=["KBData"],
        filters=[WhereFilter(table_name="KBData", column_name="data_id", value=data_id)],
        limit=1,
    ))
    if resp.code != 200 or not resp.data:
        raise HTTPException(status_code=404, detail=f"KBData not found: {data_id}")

    record = resp.data[0]
    log.info("POST /ingestion/promote/%s/silver tenant=%s", data_id, ctx["tenant_id"])

    try:
        await kafka_producer.produce(
            topic=cfg.KAFKA_TOPIC_SILVER,
            value={
                "event":       "promote_silver",
                "data_id":     data_id,
                "tenant_id":   ctx["tenant_id"],
                "source_type": record.get("source_type", "doc"),
                "extension":   record.get("extension", ""),
                "minio_path":  record.get("path", ""),
                "approved_by": ctx["user_id"],
            },
            key=data_id,
        )
    except Exception as e:
        log.exception("Kafka produce error (silver): %s", e)
        raise HTTPException(status_code=500, detail="Promotion queuing failed")

    now = datetime.datetime.utcnow()
    db = mongo.get_client()
    await db["kb_ingestion_logs"].update_one(
        {"data_id": data_id},
        {
            "$set":         {"status": "PENDING", "stage": "silver", "updated_at": now},
            "$push":        {"logs": {"timestamp": now, "level": "INFO", "message": "Silver promotion queued"}},
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )

    return PromoteResponse(
        data_id=data_id,
        stage="silver",
        message="Silver promotion queued — processing asynchronously",
    )


@router.post("/promote/{data_id}/gold", response_model=PromoteResponse)
async def promote_to_gold(
    data_id: str,
    request: Request,
    ctx: dict = Depends(get_ui_context),
):
    postgres       = svc(request, "postgres")
    kafka_producer = svc(request, "kafka_producer")
    mongo          = svc(request, "mongo")

    resp = await postgres.read(ReadJoinRequest(
        tenant_id=ctx["tenant_id"],
        joins_table=["KBData"],
        filters=[WhereFilter(table_name="KBData", column_name="data_id", value=data_id)],
        limit=1,
    ))
    if resp.code != 200 or not resp.data:
        raise HTTPException(status_code=404, detail=f"KBData not found: {data_id}")

    record = resp.data[0]
    if record.get("current_tier") != "silver":
        raise HTTPException(
            status_code=409,
            detail=f"Asset is not in SILVER tier (current: {record.get('current_tier')})",
        )

    log.info("POST /ingestion/promote/%s/gold tenant=%s", data_id, ctx["tenant_id"])

    try:
        await kafka_producer.produce(
            topic=cfg.KAFKA_TOPIC_GOLD,
            value={
                "event":               "promote_gold",
                "data_id":             data_id,
                "tenant_id":           ctx["tenant_id"],
                "neo4j_connection_id": record.get("neo4j_connection_id", ""),
                "approved_by":         ctx["user_id"],
            },
            key=data_id,
        )
    except Exception as e:
        log.exception("Kafka produce error (gold): %s", e)
        raise HTTPException(status_code=500, detail="Promotion queuing failed")

    now = datetime.datetime.utcnow()
    db = mongo.get_client()
    await db["kb_ingestion_logs"].update_one(
        {"data_id": data_id},
        {
            "$set":         {"status": "PENDING", "stage": "gold", "updated_at": now},
            "$push":        {"logs": {"timestamp": now, "level": "INFO", "message": "Gold promotion queued"}},
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )

    return PromoteResponse(
        data_id=data_id,
        stage="gold",
        message="Gold promotion queued — processing asynchronously",
    )


@router.get("/documents/{data_id}/stream")
async def stream_pipeline_events(
    data_id: str,
    request: Request,
):
    """SSE endpoint — streams structured pipeline events from MongoDB in real time.

    Polls ``pipeline_events`` collection for events with seq > last_seen,
    sorted by seq, at 250 ms intervals. Closes automatically on terminal
    events (pipeline.done / pipeline.error) or client disconnect.
    """
    mongo = svc(request, "mongo")
    _TERMINAL = {"pipeline.done", "pipeline.error"}

    async def generate():
        db = mongo.get_client()
        last_seq = 0

        while True:
            if await request.is_disconnected():
                return

            docs = await db["pipeline_events"].find(
                {"data_id": data_id, "seq": {"$gt": last_seq}},
                sort=[("seq", 1)],
                limit=50,
            ).to_list(50)

            for doc in docs:
                last_seq = doc["seq"]
                ts = doc["ts"]
                payload = {
                    "seq":     doc["seq"],
                    "event":   doc["event"],
                    "ts":      ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                    "payload": doc.get("payload", {}),
                }
                yield f"data: {json.dumps(payload)}\n\n"
                if doc["event"] in _TERMINAL:
                    return

            if not docs:
                await asyncio.sleep(0.25)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _progress_pct(status: str, stage: str, processed: int, total: int) -> float:
    if status == "PUBLISHED":
        return 100.0
    if total > 0:
        return min(round(processed / total * 100, 1), 95.0)
    stage_map = {
        "queued": 3, "bronze": 8, "ocr": 18, "cleaning": 32,
        "chunking": 50, "embedding": 72, "graph": 85, "silver": 45, "gold": 82,
    }
    return float(stage_map.get((stage or "").lower(), 5))


@router.get("/documents/{data_id}/progress")
async def stream_progress(data_id: str, request: Request):
    """SSE endpoint — streams ingestion progress with chunk-based percentage."""
    mongo    = svc(request, "mongo")
    postgres = svc(request, "postgres")

    async def generate():
        db = mongo.get_client()
        last_count = 0
        try:
            doc_uuid = _uuid.UUID(data_id)
        except ValueError:
            return

        while True:
            if await request.is_disconnected():
                return

            doc = await db["kb_ingestion_logs"].find_one({"data_id": data_id})

            # Count already-processed chunks from Postgres
            try:
                async with postgres.get_client() as session:
                    processed_chunks: int = await session.scalar(
                        sa_select(sa_func.count()).select_from(KBTextBlockORM)
                        .where(KBTextBlockORM.owner_id == doc_uuid)
                    ) or 0
            except Exception:
                processed_chunks = 0

            total_chunks: int = int((doc or {}).get("total_chunks", 0))
            status: str = (doc or {}).get("status", "PENDING")
            stage: str  = (doc or {}).get("stage",  "")
            pct = _progress_pct(status, stage, processed_chunks, total_chunks)

            if not doc:
                yield f"data: {json.dumps({'status': 'PENDING', 'message': 'Waiting for worker...', 'processed_chunks': 0, 'total_chunks': 0, 'progress_pct': 0})}\n\n"
            else:
                logs = doc.get("logs", [])
                for entry in logs[last_count:]:
                    payload = {
                        "status":           status,
                        "stage":            stage,
                        "level":            entry.get("level", "INFO"),
                        "message":          entry.get("message", ""),
                        "processed_chunks": processed_chunks,
                        "total_chunks":     total_chunks,
                        "progress_pct":     pct,
                    }
                    yield f"data: {json.dumps(payload)}\n\n"
                last_count = len(logs)

                if status in _TERMINAL_STATUSES:
                    final_pct = 100.0 if status == "PUBLISHED" else pct
                    yield f"data: {json.dumps({'status': status, 'done': True, 'progress_pct': final_pct, 'processed_chunks': processed_chunks, 'total_chunks': total_chunks})}\n\n"
                    return

            await asyncio.sleep(2)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
