from __future__ import annotations

from typing import Any, AsyncGenerator
import asyncio
import datetime
import json
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from libs.basemodel.services_databaseconnector.postgres_model import (
    Language, SourceType, Document, Image, Video, Web, Warehouse,
)
from services.kb_backend.config import ROLE_PERMISSION_IDS
from services.kb_backend.schemas import PromoteResponse, WebIngestRequest
from services.kb_backend.dependencies.auth import authenticate
from services.kb_backend.dependencies.services import svc
from services.kb_backend.dependencies.auth import require_perm
from services.kb_backend.operations.document_ops import (
    fetch_one as _fetch_doc, name_exists as _name_exists, reprocess_gold_documents,
)
from services.kb_backend.pipeline.ingestion import config as cfg
from services.kb_backend.pipeline.ingestion.bronze import BronzeUploadRequest, WebBronzeRequest

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipeline/ingestion", tags=["Ingestion"])

_META_CLS: dict[SourceType, type[BaseModel]] = {
    SourceType.DOC:       Document,
    SourceType.WEB:       Web,
    SourceType.IMAGE:     Image,
    SourceType.VIDEO:     Video,
    SourceType.WAREHOUSE: Warehouse,
}

_TERMINAL_STATUSES = {"PUBLISHED", "FAILED"}



async def _fetch_kbdata_or_404(postgres: Any, tenant_id: str, data_id: str) -> dict[str, Any]:
    """Fetch the single KBData row for (tenant, data_id) or raise 404.

    Thin 404 wrapper over document_ops.fetch_one — the query itself lives in ops.
    Shared by the silver/gold promote handlers, which both need the record
    (source/extension/path for silver, tier + neo4j connection for gold).
    """
    record = await _fetch_doc(postgres, data_id, tenant_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"KBData not found: {data_id}")
    return record


async def _log_promotion_queued(mongo: Any, data_id: str, stage: str) -> None:
    """Upsert the ingestion-log doc marking a promotion to `stage` as queued."""
    now = datetime.datetime.now(datetime.timezone.utc)
    db = mongo.get_client()
    await db["kb_ingestion_logs"].update_one(
        {"data_id": data_id},
        {
            "$set":         {"status": "PENDING", "stage": stage, "updated_at": now},
            "$push":        {"logs": {"timestamp": now, "level": "INFO",
                                      "message": f"{stage.capitalize()} promotion queued"}},
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )


@router.post("/bronze", response_model=None)
async def ingest_bronze(
    request: Request,
    file: UploadFile = File(...),
    name: str = Form(...),
    abstract: str = Form(...),
    language: str = Form("english"),
    source_type: str = Form(...),
    doc_metadata: str = Form("{}"),
    knowledge_base_id: str = Form(""),
    ctx: dict[str, Any] = Depends(require_perm("add_data")),
) -> Any:
    """
    Ingest document to BRONZE layer.

    ``name`` is the user-supplied Knowledge Name and becomes the document name;
    the upload filename is used only for object storage. Context (tenant_id,
    user_id, role_id) comes from headers (X-Tenant-Id, X-User-Id, X-Role), NOT
    from the request body, so context cannot be spoofed by clients.
    """
    file_bytes = await file.read()
    filename   = file.filename or "upload"
    extension  = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    name = name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Knowledge Name is required")

    try:
        src  = SourceType(source_type)
        lang = Language(language)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid source type or language")

    # KBData.name is globally unique — reject duplicates up front with a clear
    # message instead of letting the insert blow up with a generic 500.
    postgres = svc(request, "postgres")
    if await _name_exists(postgres, ctx["tenant_id"], name):
        raise HTTPException(
            status_code=409,
            detail=f"A document named '{name}' already exists. Choose a different Knowledge Name.",
        )

    try:
        meta_dict = json.loads(doc_metadata)
        meta_dict["source_type"] = src.value
        metadata_obj = _META_CLS[src](**meta_dict)
    except Exception as e:
        log.warning("ingestion: invalid doc_metadata from client: %s", e)
        raise HTTPException(status_code=422, detail="Invalid metadata format")

    # Map role name → RolePermissions UUID; fall back to role as-is if already a UUID.
    # Role comes from headers (X-Role), not request body
    resolved_role_id = str(ROLE_PERMISSION_IDS.get(ctx["role"], ctx["role"]))

    log.info("POST /ingestion/bronze tenant=%s source=%s size=%d", ctx["tenant_id"], src, len(file_bytes))
    try:
        return await svc(request, "ingestion").bronze(
            BronzeUploadRequest(
                tenant_id=ctx["tenant_id"],
                role_id=resolved_role_id,
                name=name,
                filename=filename,
                extension=extension,
                language=lang.value,
                source_type=src.value,
                added_by=ctx["user_name"],
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


@router.post("/bronze/web", response_model=None)
async def ingest_bronze_web(
    request: Request,
    body: WebIngestRequest,
    ctx: dict[str, Any] = Depends(require_perm("add_data")),
) -> Any:
    """
    Ingest a web portal to BRONZE by URL (no file upload).

    Only the URL is stored now; the live page is fetched, stripped of boilerplate
    to markdown, and Docling-chunked at the bronze→silver step (silver._parse_web).
    ``name`` is the user-supplied Knowledge Name and becomes the document name.
    """
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Knowledge Name is required")
    url = body.url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=422, detail="Web URL must start with http:// or https://")

    try:
        lang = Language(body.language)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid language")

    # KBData.name is globally unique — reject duplicates up front (same as files).
    postgres = svc(request, "postgres")
    if await _name_exists(postgres, ctx["tenant_id"], name):
        raise HTTPException(
            status_code=409,
            detail=f"A document named '{name}' already exists. Choose a different Knowledge Name.",
        )

    resolved_role_id = str(ROLE_PERMISSION_IDS.get(ctx["role"], ctx["role"]))
    log.info("POST /ingestion/bronze/web tenant=%s url=%s", ctx["tenant_id"], url)
    try:
        return await svc(request, "ingestion").bronze_web(
            WebBronzeRequest(
                tenant_id=ctx["tenant_id"],
                role_id=resolved_role_id,
                name=name,
                url=url,
                web_name=(body.web_name or name),
                language=lang.value,
                added_by=ctx["user_name"],
                abstract=body.abstract or "",
                knowledge_base_id=body.knowledge_base_id or None,
            ),
            postgres,
        )
    except ValueError as e:
        log.warning("Web bronze ValueError: %s", e)
        raise HTTPException(status_code=400, detail="Invalid web ingestion parameters")
    except Exception as e:
        log.exception("Web bronze error: %s", e)
        raise HTTPException(status_code=500, detail="Web ingestion failed")


@router.post("/promote/{data_id}/silver", response_model=PromoteResponse)
async def promote_to_silver(
    data_id: str,
    request: Request,
    ctx: dict[str, Any] = Depends(require_perm("process_layer")),
) -> PromoteResponse:
    postgres       = svc(request, "postgres")
    kafka_producer = svc(request, "kafka_producer")
    mongo          = svc(request, "mongo")

    record = await _fetch_kbdata_or_404(postgres, ctx["tenant_id"], data_id)
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
                "approved_by": ctx["user_name"],
            },
            key=data_id,
        )
    except Exception as e:
        log.exception("Kafka produce error (silver): %s", e)
        raise HTTPException(status_code=500, detail="Promotion queuing failed")

    await _log_promotion_queued(mongo, data_id, "silver")

    return PromoteResponse(
        data_id=data_id,
        stage="silver",
        message="Silver promotion queued — processing asynchronously",
    )


@router.post("/promote/{data_id}/gold", response_model=PromoteResponse)
async def promote_to_gold(
    data_id: str,
    request: Request,
    ctx: dict[str, Any] = Depends(require_perm("process_layer")),
) -> PromoteResponse:
    postgres       = svc(request, "postgres")
    kafka_producer = svc(request, "kafka_producer")
    mongo          = svc(request, "mongo")

    record = await _fetch_kbdata_or_404(postgres, ctx["tenant_id"], data_id)
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
                "approved_by":         ctx["user_name"],
            },
            key=data_id,
        )
    except Exception as e:
        log.exception("Kafka produce error (gold): %s", e)
        raise HTTPException(status_code=500, detail="Promotion queuing failed")

    await _log_promotion_queued(mongo, data_id, "gold")

    return PromoteResponse(
        data_id=data_id,
        stage="gold",
        message="Gold promotion queued — processing asynchronously",
    )


@router.post("/reprocess-pii", response_model=None)
async def reprocess_pii_route(
    request: Request,
    ctx: dict[str, Any] = Depends(require_perm("process_layer")),
) -> dict[str, Any]:
    """Re-run Gold promotion for every live Gold-tier document of the tenant so
    the current PII-masking policy is applied to already-ingested content.
    Returns how many documents were re-queued (and any that could not be)."""
    postgres       = svc(request, "postgres")
    kafka_producer = svc(request, "kafka_producer")
    log.info("POST /ingestion/reprocess-pii tenant=%s", ctx["tenant_id"])
    return await reprocess_gold_documents(postgres, kafka_producer, ctx)


@router.get("/documents/{data_id}/logs", response_model=None)
async def get_ingestion_logs(
    data_id: str, request: Request, ctx: dict[str, Any] = Depends(authenticate),
) -> Any:
    """Stored per-document ingestion logs (Mongo ``kb_ingestion_logs``).

    Tenant-scoped: only returns logs for a document the caller's tenant owns —
    the kb_ingestion_logs record itself carries no tenant, so ownership is
    verified against KBData first."""
    postgres = svc(request, "postgres")
    doc = await _fetch_doc(postgres, data_id, ctx["tenant_id"])
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    def _iso(v: Any) -> str | None:
        return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v else None)

    rec = await svc(request, "mongo").get_client()["kb_ingestion_logs"].find_one({"data_id": data_id})
    if not rec:
        return {"data_id": data_id, "status": "PENDING", "stage": "", "updated_at": None, "logs": []}

    return {
        "data_id":    data_id,
        "status":     rec.get("status", ""),
        "stage":      rec.get("stage", ""),
        "updated_at": _iso(rec.get("updated_at")),
        "logs": [
            {
                "timestamp": _iso(e.get("timestamp")),
                "level":     e.get("level", "INFO"),
                "message":   e.get("message", ""),
            }
            for e in (rec.get("logs") or [])
        ],
    }


@router.get("/documents/{data_id}/stream")
async def stream_pipeline_events(
    data_id: str,
    request: Request,
    ctx: dict[str, Any] = Depends(authenticate),
) -> StreamingResponse:
    """SSE endpoint — streams structured pipeline events from MongoDB in real time.

    Polls ``pipeline_events`` collection for events with seq > last_seen,
    sorted by seq, at 250 ms intervals. Closes automatically on terminal
    events (pipeline.done / pipeline.error) or client disconnect.
    """
    mongo = svc(request, "mongo")
    _TERMINAL = {"pipeline.done", "pipeline.error"}

    async def generate() -> AsyncGenerator[str, None]:
        db = mongo.get_client()
        last_seq = 0
        # Keep the stream warm: emit an SSE comment at least every HEARTBEAT_S of
        # silence so a quiet pipeline stage never sits idle long enough for Kong's
        # read_timeout (or any proxy) to drop the connection. Comment lines are
        # ignored by EventSource and skipped by the fetch parser (no "data: " prefix).
        loop = asyncio.get_running_loop()
        last_emit = loop.time()
        HEARTBEAT_S = 15.0

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
                last_emit = loop.time()
                if doc["event"] in _TERMINAL:
                    return

            if not docs:
                if loop.time() - last_emit >= HEARTBEAT_S:
                    yield ": keepalive\n\n"
                    last_emit = loop.time()
                await asyncio.sleep(0.25)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Each stage occupies a band on the global bar; within a stage we interpolate by
# processed/total chunks (both written to kb_ingestion_logs by the worker) so the bar
# moves smoothly and stays monotonic across silver→gold. Gold's CHUNK loop fills only
# 50→85 — the slow doc-level phases after it (summary reduce, relationship reconcile,
# graph build) have no per-chunk count, so the worker marks them with the statuses below
# to step the bar 85→95 instead of flat-lining at the top while that LLM work runs.
_STAGE_BAND = {
    "queued": (0.0, 5.0), "bronze": (5.0, 10.0), "silver": (10.0, 50.0), "gold": (50.0, 85.0),
}
_GOLD_TAIL = {
    "gold_reduce": 87.0, "gold_relations": 90.0, "gold_graph": 94.0,
}


def _progress_pct(status: str, stage: str, processed: int, total: int) -> float:
    s = (status or "").lower()
    if s == "published":
        return 100.0
    if s in _GOLD_TAIL:
        return _GOLD_TAIL[s]
    lo, hi = _STAGE_BAND.get((stage or "").lower(), (0.0, 5.0))
    if total > 0:
        return round(lo + min(processed / total, 1.0) * (hi - lo), 1)
    return lo  # stage known but no chunk count yet → sit at the band start


@router.get("/documents/{data_id}/progress")
async def stream_progress(data_id: str, request: Request, ctx: dict[str, Any] = Depends(authenticate)) -> StreamingResponse:
    """SSE endpoint — streams ingestion progress with chunk-based percentage."""
    mongo = svc(request, "mongo")

    async def generate() -> AsyncGenerator[str, None]:
        db = mongo.get_client()
        last_count = 0

        loop = asyncio.get_running_loop()
        last_emit = loop.time()
        HEARTBEAT_S = 15.0

        while True:
            if await request.is_disconnected():
                return

            emitted = False
            doc = await db["kb_ingestion_logs"].find_one({"data_id": data_id})

            # processed/total are written by the worker (silver + gold) onto this same
            # doc, so the % comes from one Mongo source — no cross-store Postgres count.
            processed_chunks: int = int((doc or {}).get("processed_chunks", 0))
            total_chunks: int = int((doc or {}).get("total_chunks", 0))
            status: str = (doc or {}).get("status", "PENDING")
            stage: str  = (doc or {}).get("stage",  "")
            pct = _progress_pct(status, stage, processed_chunks, total_chunks)

            if not doc:
                yield f"data: {json.dumps({'status': 'PENDING', 'message': 'Waiting for worker...', 'processed_chunks': 0, 'total_chunks': 0, 'progress_pct': 0})}\n\n"
                emitted = True
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
                    emitted = True
                last_count = len(logs)

                if status in _TERMINAL_STATUSES:
                    final_pct = 100.0 if status == "PUBLISHED" else pct
                    yield f"data: {json.dumps({'status': status, 'done': True, 'progress_pct': final_pct, 'processed_chunks': processed_chunks, 'total_chunks': total_chunks})}\n\n"
                    return

            # Heartbeat during quiet stages so Kong's read_timeout can't drop us.
            if emitted:
                last_emit = loop.time()
            elif loop.time() - last_emit >= HEARTBEAT_S:
                yield ": keepalive\n\n"
                last_emit = loop.time()
            await asyncio.sleep(2)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
