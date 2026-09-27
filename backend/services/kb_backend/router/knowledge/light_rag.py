from __future__ import annotations

from typing import Any, cast
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from services.kb_backend.dependencies.auth import require_perm
from services.kb_backend.dependencies.services import svc
from services.kb_backend.schemas import RetrieveRequest, RetrieveResponse
from libs.shared.auth import require_tenant
from libs.shared.kb_result_cache import ResultCache, query_hash

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipeline/light-rag", tags=["LightRAG"])


def _require_role_id(ctx: dict[str, Any]) -> str:
    """The caller's document tier, or 403.

    Empty means the pipeline would run with no role scope — which, before this
    change, was every request. Refusing is the difference between a retrieval
    that is scoped and one that merely usually is: `require_perm` above cannot
    cover it, because "may retrieve" and "may retrieve THIS" are different
    questions and only the second one depends on the tier.
    """
    role_id = ctx.get("role_id") or ""
    if not role_id:
        log.warning("retrieve | caller %r has no document tier — refusing", ctx.get("user_id"))
        raise HTTPException(status_code=403, detail="No document role associated with this identity")
    return role_id


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(
        body: RetrieveRequest,
        request: Request,
        ctx: dict[str, Any] = Depends(require_perm("retrieveKnowledge")),
) -> RetrieveResponse:
    tenant_id = require_tenant(ctx)
    knowledge_base = body.knowledge_base or None
    # The caller's document tier, from the verified identity — never from the
    # request body. `require_perm` above answers "may this caller retrieve at
    # all"; this answers "from which documents", which is the question the
    # role hierarchy in operations/access.py exists to settle and which this
    # endpoint did not previously ask.
    role_id = _require_role_id(ctx)

    cache: ResultCache = request.app.state.result_cache
    qhash = query_hash(
        body.query,
        body.collection,
        body.entities or None,
        body.intent or None,
        body.entity_types or None,
        role_id,
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
            postgres=svc(request, "postgres"),
            collection=body.collection,
            knowledge_base=knowledge_base,
            entities=body.entities or None,
            intent=body.intent or None,
            entity_types=body.entity_types or None,
            role_id=role_id,
        )
        if result.get("results"):
            await cache.set(tenant_id, knowledge_base, qhash, result)
        return cast(RetrieveResponse, result)
    except Exception as exc:
        log.exception("light_rag | unhandled error: %s", exc)
        raise HTTPException(status_code=500, detail="Retrieval failed")
