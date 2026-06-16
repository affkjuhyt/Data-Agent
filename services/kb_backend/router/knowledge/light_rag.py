from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services.kb_backend.dependencies.ui_context import get_ui_context, svc
from services.kb_backend.pipeline.light_rag import config as cfg

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipeline/light-rag", tags=["LightRAG"])


class RetrieveRequest(BaseModel):
    query: str
    tenant_id: str = ""
    collection: str = cfg.QDRANT_COLLECTION
    # Restrict retrieval to one knowledge base (the "kind of knowledge"). Empty = all KBs.
    knowledge_base: str = ""
    # Insert fields for a query-enhancement front layer: when entities are given,
    # the pipeline skips its internal canonicalize LLM call and uses them as the
    # pass-1 filter + graph seeds. Empty = pipeline does its own extraction.
    entities: list[str] = []
    intent: str = ""
    # Optional entity name → type (person/organization/location/...) from the
    # front layer. Enables per-entity typed search: "find chunks where Việt Minh
    # appears AS AN ORGANIZATION" (ent_organization MatchAny) — disambiguates
    # homonyms and sharpens the typed pass.
    entity_types: dict[str, str] = {}


class RetrieveResponse(BaseModel):
    source: str
    canonical: dict[str, Any]
    results: list[dict[str, Any]]
    timings: dict[str, float] = {}


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(
    body: RetrieveRequest,
    request: Request,
    ctx: dict = Depends(get_ui_context),
):
    tenant_id = body.tenant_id or ctx["tenant_id"] or "default"
    knowledge_base = body.knowledge_base or None
    log.info("POST /retrieve | query=%r tenant=%s kb=%s", body.query[:80], tenant_id, knowledge_base)
    try:
        return await svc(request, "light_rag").run(
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
    except Exception as exc:
        log.exception("light_rag | unhandled error: %s", exc)
        raise HTTPException(status_code=500, detail="Retrieval failed")


@router.post("/retrieve/stream")
async def retrieve_stream(
    body: RetrieveRequest,
    request: Request,
    ctx: dict = Depends(get_ui_context),
):
    """SSE endpoint — streams one event per pipeline step so the UI can show progress.

    Each SSE data line is a JSON dict with at minimum {"step": "<name>", "ms": float}.
    The final event is {"step": "done", ...full result...} or {"step": "error", ...}.
    """
    tenant_id = body.tenant_id or ctx["tenant_id"] or "default"
    knowledge_base = body.knowledge_base or None
    log.info("POST /retrieve/stream | query=%r tenant=%s kb=%s", body.query[:80], tenant_id, knowledge_base)

    async def generate():
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


@router.get("/health")
async def health(request: Request, ctx: dict = Depends(get_ui_context)):
    pipeline = getattr(request.app.state, "light_rag", None)
    return {"status": "ok" if pipeline else "not_initialised"}
