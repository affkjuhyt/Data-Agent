from __future__ import annotations

from typing import Any, AsyncGenerator, cast
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from services.kb_backend.dependencies.auth import authenticate
from services.kb_backend.dependencies.services import svc
from services.kb_backend.schemas import RetrieveRequest, RetrieveResponse
from libs.shared.kb_result_cache import ResultCache, query_hash

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipeline/light-rag", tags=["LightRAG"])


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(
    body: RetrieveRequest,
    request: Request,
    ctx: dict[str, Any] = Depends(authenticate),
) -> RetrieveResponse:
    tenant_id = body.tenant_id or ctx["tenant_id"] or "default"
    knowledge_base = body.knowledge_base or None

    # L0 result cache — an exact repeat returns before embedding (KB-SCALE-10K §2).
    cache: ResultCache = request.app.state.result_cache
    qhash = query_hash(
        body.query, body.collection, body.entities or None,
        body.intent or None, body.entity_types or None,
    )
    cached = await cache.get(tenant_id, knowledge_base, qhash)
    if cached is not None:
        cached["source"] = "result_cache"
        return cast(RetrieveResponse, cached)

    log.info("POST /retrieve | query=%r tenant=%s kb=%s", body.query[:80], tenant_id, knowledge_base)
    try:
        result = await svc(request, "light_rag").run(
            query=body.query,
            tenant_id=tenant_id,
            qdrant=svc(request, "qdrant").get_client(),
            neo4j=svc(request, "neo4j").get_client(),
            model=svc(request, "model").get_client(),
            postgres=svc(request, "postgres"),   # PostgresClient — not a session
            collection=body.collection,
            knowledge_base=knowledge_base,
            entities=body.entities or None,
            intent=body.intent or None,
            entity_types=body.entity_types or None,
        )
        if result.get("results"):
            await cache.set(tenant_id, knowledge_base, qhash, result)
        return cast(RetrieveResponse, result)
    except Exception as exc:
        log.exception("light_rag | unhandled error: %s", exc)
        raise HTTPException(status_code=500, detail="Retrieval failed")


@router.post("/retrieve/stream")
async def retrieve_stream(
    body: RetrieveRequest,
    request: Request,
    ctx: dict[str, Any] = Depends(authenticate),
) -> StreamingResponse:
    """SSE endpoint — streams one event per pipeline step so the UI can show progress.

    Each SSE data line is a JSON dict with at minimum {"step": "<name>", "ms": float}.
    The final event is {"step": "done", ...full result...} or {"step": "error", ...}.
    """
    tenant_id = body.tenant_id or ctx["tenant_id"] or "default"
    knowledge_base = body.knowledge_base or None
    log.info("POST /retrieve/stream | query=%r tenant=%s kb=%s", body.query[:80], tenant_id, knowledge_base)

    async def generate() -> AsyncGenerator[str, None]:
        try:
            async for event in svc(request, "light_rag").run_stream(
                query=body.query,
                tenant_id=tenant_id,
                qdrant=svc(request, "qdrant").get_client(),
                neo4j=svc(request, "neo4j").get_client(),
                model=svc(request, "model").get_client(),
                postgres=svc(request, "postgres"),
                collection=body.collection,
                knowledge_base=knowledge_base,
                entities=body.entities or None,
                intent=body.intent or None,
                entity_types=body.entity_types or None,
            ):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:
            log.exception("retrieve_stream | generator error: %s", exc)
            yield f"data: {json.dumps({'step': 'error', 'message': str(exc)[:300]})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/health", response_model=None)
async def health(request: Request, ctx: dict[str, Any] = Depends(authenticate)) -> Any:
    pipeline = getattr(request.app.state, "light_rag", None)
    return {"status": "ok" if pipeline else "not_initialised"}
