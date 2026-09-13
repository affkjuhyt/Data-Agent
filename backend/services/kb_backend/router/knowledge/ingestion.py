from __future__ import annotations

from typing import Any, AsyncGenerator, AsyncIterator
import asyncio
import datetime
import hashlib
import json
import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text

from libs.basemodel.services_databaseconnector.postgres_model import (
    Language, SourceType, Document, Image, Video, Web, Warehouse,
)
from services.kb_backend.schemas import PromoteResponse, WebIngestRequest
from services.kb_backend.dependencies.services import svc
from services.kb_backend.dependencies.auth import require_perm
from services.kb_backend.operations.document_ops import (
    fetch_one as _fetch_doc, fetch_by_name as _fetch_doc_by_name, reprocess_gold_documents,
)
from services.kb_backend.operations.trash_ops import flush as _trash_flush
from services.kb_backend.pipeline.ingestion import config as cfg
from services.kb_backend.pipeline.ingestion.bronze import BronzeUploadRequest, WebBronzeRequest

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipeline/ingestion", tags=["Ingestion"])

_META_CLS: dict[SourceType, type[BaseModel]] = {
    SourceType.DOC: Document,
    SourceType.WEB: Web,
    SourceType.IMAGE: Image,
    SourceType.VIDEO: Video,
    SourceType.WAREHOUSE: Warehouse,
}

_TERMINAL_STATUSES = {"PUBLISHED", "FAILED"}


async def _fetch_kbdata_or_404(postgres: Any, tenant_id: str, role_id: str, data_id: str) -> dict[str, Any]:
    record = await _fetch_doc(postgres, data_id, tenant_id, role_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"KBData not found: {data_id}")
    return record


def _document_name_key(tenant_id: str, name: str) -> int:
    digest = hashlib.blake2b(f"{tenant_id}:{name}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


@asynccontextmanager
async def _document_name_lock(postgres: Any, tenant_id: str, name: str) -> AsyncIterator[None]:
    """Serialise replace-then-insert for one (tenant, name).

    Rejects rather than queues: a waiting request would hold its connection for
    the whole ingest. The session stays in one transaction so the lock keeps the
    same backend under transaction pooling.
    """
    key = _document_name_key(tenant_id, name)
    session = postgres.get_client()
    acquired = await session.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
    if not acquired:
        await session.close()
        raise HTTPException(
            status_code=409,
            detail=f"'{name}' is currently being ingested. Wait for it to finish, or use a different name.",
        )
    try:
        yield
    finally:
        await session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
        await session.close()


async def _replace_existing_document_if_any(request: Request, ctx: dict[str, Any], name: str) -> None:
    """Re-uploading under a Knowledge Name that already exists is treated as
    "this is an updated version of that same source" (e.g. a periodically
    revised statistics export or a re-crawled web portal) rather than a
    naming conflict: hard-delete the existing document first — this frees
    the (tenant_id, name) unique constraint, which a soft delete does not
    (see trash_ops.flush's docstring / idx_kbdata_tenant_name) — then the
    caller's normal bronze insert proceeds with the new content."""
    postgres = svc(request, "postgres")
    existing = await _fetch_doc_by_name(postgres, ctx["tenant_id"], name)
    if existing is None:
        return
    log.info(
        "ingestion: replacing existing document name=%r data_id=%s tenant=%s",
        name, existing["data_id"], ctx["tenant_id"],
    )
    await _trash_flush(
        postgres, svc(request, "qdrant"), svc(request, "neo4j"),
        "document", str(existing["data_id"]), ctx["tenant_id"],
        minio=svc(request, "minio"), mongo=svc(request, "mongo"),
    )


async def _log_promotion_queued(mongo: Any, data_id: str, stage: str) -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    db = mongo.get_client()
    await db["kb_ingestion_logs"].update_one(
        {"data_id": data_id},
        {
            "$set": {"status": "PENDING", "stage": stage, "updated_at": now},
            "$push": {"logs": {"timestamp": now, "level": "INFO",
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
        ingestion_mode: str = Form("document"),
        ctx: dict[str, Any] = Depends(require_perm("addData")),
) -> Any:
    file_bytes = await file.read()
    filename = file.filename or "upload"
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    name = name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Knowledge Name is required")

    if ingestion_mode not in ("document", "structured_table"):
        raise HTTPException(status_code=422, detail="Invalid ingestion_mode")
    if ingestion_mode == "structured_table" and extension not in ("xlsx", "xls", "csv"):
        raise HTTPException(
            status_code=422,
            detail="structured_table mode requires an .xlsx, .xls, or .csv file",
        )

    try:
        src = SourceType(source_type)
        lang = Language(language)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid source type or language")

    postgres = svc(request, "postgres")

    try:
        meta_dict = json.loads(doc_metadata)
        meta_dict["source_type"] = src.value
        metadata_obj = _META_CLS[src](**meta_dict)
    except Exception as e:
        log.warning("ingestion: invalid doc_metadata from client: %s", e)
        raise HTTPException(status_code=422, detail="Invalid metadata format")

    log.info("POST /ingestion/bronze tenant=%s source=%s size=%d", ctx["tenant_id"], src, len(file_bytes))
    async with _document_name_lock(svc(request, "postgres"), ctx["tenant_id"], name):
        await _replace_existing_document_if_any(request, ctx, name)
        try:
            return await svc(request, "ingestion").bronze(
                BronzeUploadRequest(
                    tenant_id=ctx["tenant_id"],
                    role_id=ctx["role_id"],
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
                    ingestion_mode=ingestion_mode,
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
        ctx: dict[str, Any] = Depends(require_perm("addData")),
) -> Any:
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

    postgres = svc(request, "postgres")

    log.info("POST /ingestion/bronze/web tenant=%s url=%s", ctx["tenant_id"], url)
    async with _document_name_lock(svc(request, "postgres"), ctx["tenant_id"], name):
        await _replace_existing_document_if_any(request, ctx, name)
        try:
            return await svc(request, "ingestion").bronze_web(
                WebBronzeRequest(
                    tenant_id=ctx["tenant_id"],
                    role_id=ctx["role_id"],
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
        ctx: dict[str, Any] = Depends(require_perm("processLayer")),
) -> PromoteResponse:
    postgres = svc(request, "postgres")
    kafka_producer = svc(request, "kafka_producer")
    mongo = svc(request, "mongo")

    record = await _fetch_kbdata_or_404(postgres, ctx["tenant_id"], ctx["role_id"], data_id)
    log.info("POST /ingestion/promote/%s/silver tenant=%s", data_id, ctx["tenant_id"])

    try:
        await kafka_producer.produce(
            topic=cfg.KAFKA_TOPIC_SILVER,
            value={
                "event": "promote_silver",
                "data_id": data_id,
                "tenant_id": ctx["tenant_id"],
                "source_type": record.get("source_type", "doc"),
                "extension": record.get("extension", ""),
                "minio_path": record.get("path", ""),
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
        ctx: dict[str, Any] = Depends(require_perm("processLayer")),
) -> PromoteResponse:
    postgres = svc(request, "postgres")
    kafka_producer = svc(request, "kafka_producer")
    mongo = svc(request, "mongo")

    record = await _fetch_kbdata_or_404(postgres, ctx["tenant_id"], ctx["role_id"], data_id)
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
                "event": "promote_gold",
                "data_id": data_id,
                "tenant_id": ctx["tenant_id"],
                "neo4j_connection_id": record.get("neo4j_connection_id", ""),
                "approved_by": ctx["user_name"],
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
        ctx: dict[str, Any] = Depends(require_perm("processLayer")),
) -> dict[str, Any]:
    postgres = svc(request, "postgres")
    kafka_producer = svc(request, "kafka_producer")
    log.info("POST /ingestion/reprocess-pii tenant=%s", ctx["tenant_id"])
    return await reprocess_gold_documents(postgres, kafka_producer, ctx)


@router.get("/documents/{data_id}/logs", response_model=None)
async def get_ingestion_logs(
        data_id: str, request: Request, ctx: dict[str, Any] = Depends(require_perm("viewDocuments")),
) -> Any:
    postgres = svc(request, "postgres")
    doc = await _fetch_doc(postgres, data_id, ctx["tenant_id"], ctx["role_id"])
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    def _iso(v: Any) -> str | None:
        return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v else None)

    rec = await svc(request, "mongo").get_client()["kb_ingestion_logs"].find_one({"data_id": data_id})
    if not rec:
        return {"data_id": data_id, "status": "PENDING", "stage": "", "updated_at": None, "logs": []}

    return {
        "data_id": data_id,
        "status": rec.get("status", ""),
        "stage": rec.get("stage", ""),
        "updated_at": _iso(rec.get("updated_at")),
        "logs": [
            {
                "timestamp": _iso(e.get("timestamp")),
                "level": e.get("level", "INFO"),
                "message": e.get("message", ""),
            }
            for e in (rec.get("logs") or [])
        ],
    }


@router.get("/documents/{data_id}/stream")
async def stream_pipeline_events(
        data_id: str,
        request: Request,
        ctx: dict[str, Any] = Depends(require_perm("viewDocuments")),
) -> StreamingResponse:
    mongo = svc(request, "mongo")
    _TERMINAL = {"pipeline.done", "pipeline.error"}

    async def generate() -> AsyncGenerator[str, None]:
        db = mongo.get_client()
        last_seq = 0
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
                    "seq": doc["seq"],
                    "event": doc["event"],
                    "ts": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
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
    return lo


@router.get("/documents/{data_id}/progress")
async def stream_progress(
        data_id: str,
        request: Request,
        ctx: dict[str, Any] = Depends(require_perm("viewDocuments"))) -> StreamingResponse:
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

            processed_chunks: int = int((doc or {}).get("processed_chunks", 0))
            total_chunks: int = int((doc or {}).get("total_chunks", 0))
            status: str = (doc or {}).get("status", "PENDING")
            stage: str = (doc or {}).get("stage", "")
            pct = _progress_pct(status, stage, processed_chunks, total_chunks)

            if not doc:
                yield f"data: {json.dumps({'status': 'PENDING', 'message': 'Waiting for worker...', 'processed_chunks': 0, 'total_chunks': 0, 'progress_pct': 0})}\n\n"
                emitted = True
            else:
                logs = doc.get("logs", [])
                for entry in logs[last_count:]:
                    payload = {
                        "status": status,
                        "stage": stage,
                        "level": entry.get("level", "INFO"),
                        "message": entry.get("message", ""),
                        "processed_chunks": processed_chunks,
                        "total_chunks": total_chunks,
                        "progress_pct": pct,
                    }
                    yield f"data: {json.dumps(payload)}\n\n"
                    emitted = True
                last_count = len(logs)

                if status in _TERMINAL_STATUSES:
                    final_pct = 100.0 if status == "PUBLISHED" else pct
                    yield f"data: {json.dumps({'status': status, 'done': True, 'progress_pct': final_pct, 'processed_chunks': processed_chunks, 'total_chunks': total_chunks})}\n\n"
                    return

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
