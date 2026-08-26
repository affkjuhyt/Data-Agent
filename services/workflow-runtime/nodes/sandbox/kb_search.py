from typing import Any
import logging
import httpx
from pydantic import BaseModel, ValidationError
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from libs.shared.service_identity import (
    ServiceAssertionError,
    get_secret,
    mint_service_assertion,
)
from mtls import kb_backend_ssl_context
from state import AgentState

logger = logging.getLogger(__name__)

SERVICE_NAME = "workflow-runtime"

_RETRYABLE = lambda e: isinstance(e, httpx.TransportError) or (
    isinstance(e, httpx.HTTPStatusError) and e.response.status_code >= 500
)


def _identity_headers(cfg: dict[str, Any], tenant_id: str) -> dict[str, str]:
    """Identity for the service-to-service call to kb-backend.

    This used to forge Kong's marker plus a fixed admin role set
    (`X-Kong-Verified: true`, `X-User-Roles: platform-admin,tenant-admin,...`)
    on every retrieval, which meant kb-backend's role filter (access.py
    document_role_filter) always saw an administrator no matter who asked. Now
    the real caller's roles travel inside an HMAC-signed assertion that
    kb-backend verifies; the identity headers are not sent at all, so there is
    nothing left to forge and nothing for kb-backend to trust by accident.

    Raises ServiceAssertionError when the caller has no roles, or no signing
    secret is configured. Callers must let that fail the retrieval: falling back
    to any default identity is what this change exists to remove.
    """
    roles = [r for r in (cfg.get("user_roles") or []) if r]
    user_ref = cfg.get("user_ref") or ""

    if not roles:
        raise ServiceAssertionError(
            "no caller roles on this turn — refusing to search the knowledge base without an identity"
        )

    assertion = mint_service_assertion(
        service=SERVICE_NAME,
        tenant_id=tenant_id,
        user_id=user_ref,
        roles=roles,
        secret=get_secret(),
    )
    return {"X-Service-Assertion": assertion}


class KBChunk(BaseModel):
    id: str
    content: str
    score: float
    source: str = ""    # doc_name — tên tài liệu hiển thị cho citation
    doc_id: str = ""     # để FE build link mở file gốc: /api/knowledge/documents/{doc_id}/file


async def kb_search_node(state: AgentState) -> dict[str, Any]:
    cfg         = state["config"]
    kb_endpoint = cfg.get("kb_endpoint", "")
    tenant_id   = cfg.get("tenant_id", "")

    if not kb_endpoint:
        return {"kb_chunks": []}

    try:
        headers = _identity_headers(cfg, tenant_id)
    except ServiceAssertionError as e:
        # Fail closed. Previously this path could not fail, because it asserted
        # an admin identity unconditionally; an agent turn with no caller
        # identity must now return nothing rather than everything.
        logger.warning("kb_search | refusing to search without a verifiable identity: %s", e)
        return {"kb_chunks": [], "kb_error": f"knowledge base search not authorized: {e}"}

    timeout = httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)

    kb_call = next(
        (tc for tc in state.get("tool_calls", []) if tc.get("type") == "kb_search"),
        None,
    )
    search_query = (kb_call or {}).get("query") or state["query"]

    try:
        async for attempt in AsyncRetrying(
            retry=retry_if_exception(_RETRYABLE),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            reraise=True,
        ):
            with attempt:
                # verify= carries the client certificate when mTLS is enabled,
                # so the transport authenticates this service to kb-backend and
                # kb-backend's server certificate back to it. Falls back to
                # plain HTTP verification when unconfigured (see mtls.py).
                async with httpx.AsyncClient(timeout=timeout, verify=kb_backend_ssl_context()) as client:
                    resp = await client.post(
                        f"{kb_endpoint}/api/pipeline/light-rag/retrieve",
                        headers=headers,
                        json={
                            "query":     search_query,
                            "tenant_id": tenant_id,
                        },
                    )
                    resp.raise_for_status()
                    raw = resp.json().get("results", [])

                    valid, skipped = [], 0
                    for item in raw:
                        try:
                            valid.append(KBChunk(
                                id      = item.get("source", {}).get("block_id", ""),
                                content = item.get("chunk_context", ""),
                                score   = item.get("chunk_score", 0.0),
                                source  = item.get("source", {}).get("doc_name", ""),
                                doc_id  = item.get("source", {}).get("doc_id", ""),
                            ).model_dump())
                        except ValidationError:
                            skipped += 1

                    if skipped:
                        logger.warning("kb_search | skipped %d invalid chunks", skipped)
                    logger.info("kb_search | tenant=%s found=%d", tenant_id, len(valid))
                    return {"kb_chunks": valid}

    except Exception as e:
        logger.warning("kb_search | failed after retries: %s — returning empty", e)
        return {"kb_chunks": [], "kb_error": str(e)}

    # Unreachable in practice: AsyncRetrying(reraise=True) always either
    # returns from inside the loop or raises (caught above) — this satisfies
    # the type checker's need for an explicit fallthrough return.
    return {"kb_chunks": []}
