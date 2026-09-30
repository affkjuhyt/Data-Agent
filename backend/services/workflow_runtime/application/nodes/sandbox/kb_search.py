import asyncio
import logging
import unicodedata
from typing import Any

import httpx
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ValidationError
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from libs.shared import audit
from libs.shared.service_identity import (
    ServiceAssertionError,
    get_secret,
    mint_service_assertion,
)
from services.workflow_runtime.domain.state import AgentState
from services.workflow_runtime.infrastructure.mtls import kb_backend_ssl_context

logger = logging.getLogger(__name__)

SERVICE_NAME = "workflow_runtime"

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
    # Trang chứa đoạn này, để citation mở đúng chỗ (#page=N) thay vì mở đầu
    # tài liệu. None với tài liệu nạp trước khi toạ độ được lưu, và với định
    # dạng không có hình học trang (HTML, TXT, MD).
    page: int | None = None


async def kb_search_node(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
    kb_call = next(
        (tc for tc in state.get("tool_calls", []) if tc.get("type") == "kb_search"),
        None,
    )
    search_query = (kb_call or {}).get("query") or state["query"]

    speculation = ((config or {}).get("configurable") or {}).get("kb_speculation")
    prefetched = _take_speculative_search(speculation, search_query)
    if prefetched is not None:
        logger.info("kb_search | reusing the search started alongside the planner")
        return await prefetched
    return await search_knowledge_base(state["config"], search_query)


async def search_knowledge_base(cfg: dict[str, Any], search_query: str) -> dict[str, Any]:
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
        audit.emit(audit.DataAccessEvent(
            tenant_id=tenant_id,
            user_id=cfg.get("user_ref", "") or "anonymous",
            user_roles=list(cfg.get("user_roles") or []),
            auth_source=cfg.get("auth_source", "") or "",
            action=audit.ACTION_KB_RETRIEVE,
            decision=audit.DECISION_DENY,
            target=cfg.get("kb_endpoint", ""),
            reason=str(e),
            session_id=cfg.get("session_id", "") or "",
            agent_id=str(cfg.get("agent_id", "") or ""),
        ))
        return {"kb_chunks": [], "kb_error": f"knowledge base search not authorized: {e}"}

    timeout = httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)

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
                        json={"query": search_query},
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
                                page    = ((item.get("source", {}) or {}).get("region") or {}).get("page_no"),
                            ).model_dump())
                        except ValidationError:
                            skipped += 1

                    if skipped:
                        logger.warning("kb_search | skipped %d invalid chunks", skipped)
                    logger.info("kb_search | tenant=%s found=%d", tenant_id, len(valid))
                    audit.emit(audit.DataAccessEvent(
                        tenant_id=tenant_id,
                        user_id=cfg.get("user_ref", "") or "anonymous",
                        user_roles=list(cfg.get("user_roles") or []),
                        auth_source=cfg.get("auth_source", "") or "",
                        action=audit.ACTION_KB_RETRIEVE,
                        decision=audit.DECISION_ALLOW,
                        target=cfg.get("kb_endpoint", ""),
                        # The documents actually returned. Without these the
                        # record says a search happened but not what it
                        # disclosed, which is the half that matters after an
                        # over-broad-permissions incident.
                        target_objects=sorted({c["doc_id"] for c in valid if c.get("doc_id")}),
                        statement_redacted=audit.redact_sql(search_query),
                        statement_sha256=audit.statement_hash(search_query),
                        row_count=len(valid),
                        session_id=cfg.get("session_id", "") or "",
                        agent_id=str(cfg.get("agent_id", "") or ""),
                    ))
                    return {"kb_chunks": valid}

    except Exception as e:
        logger.warning("kb_search | failed after retries: %s — returning empty", e)
        return {"kb_chunks": [], "kb_error": str(e)}

    # Unreachable in practice: AsyncRetrying(reraise=True) always either
    # returns from inside the loop or raises (caught above) — this satisfies
    # the type checker's need for an explicit fallthrough return.
    return {"kb_chunks": []}


# ── Speculative search ───────────────────────────────────────────────────────
# The planner and the knowledge-base search used to run one after the other,
# though the search usually does not depend on the plan: when the planner asks
# for no tool at all, the graph still searches the user's question. So the
# planner starts that search the moment it starts deciding, and kb_search_node
# takes the result if its query turns out to be the same one. A different query
# (the planner rewrote it) or no kb_search at all cancels it. The per-turn
# holder travels in LangGraph's `configurable` ("kb_speculation", set by
# conversation_runner), never in state, since a task cannot be checkpointed.

def _normalize_query(query: str) -> str:
    return " ".join(unicodedata.normalize("NFC", query or "").casefold().split())


def start_speculative_search(speculation: dict[str, Any], cfg: dict[str, Any], query: str) -> None:
    if not speculation.get("enabled") or "task" in speculation or not cfg.get("kb_endpoint"):
        return
    speculation["query"] = _normalize_query(query)
    speculation["task"] = asyncio.create_task(search_knowledge_base(cfg, query))


def discard_speculative_search(speculation: dict[str, Any] | None) -> None:
    task = (speculation or {}).pop("task", None)
    if task is not None and not task.done():
        task.cancel()


def _take_speculative_search(
    speculation: dict[str, Any] | None, query: str,
) -> "asyncio.Task[dict[str, Any]] | None":
    if not speculation or "task" not in speculation:
        return None
    if speculation.get("query") == _normalize_query(query):
        task: asyncio.Task[dict[str, Any]] = speculation.pop("task")
        return task
    discard_speculative_search(speculation)
    return None
