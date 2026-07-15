from typing import Any
import logging
import httpx
from pydantic import BaseModel, ValidationError
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from state import AgentState

logger = logging.getLogger(__name__)

_RETRYABLE = lambda e: isinstance(e, httpx.TransportError) or (
    isinstance(e, httpx.HTTPStatusError) and e.response.status_code >= 500
)


class KBChunk(BaseModel):
    id: str
    content: str
    score: float


async def kb_search_node(state: AgentState) -> dict[str, Any]:
    cfg         = state["config"]
    kb_endpoint = cfg.get("kb_endpoint", "")
    tenant_id   = cfg.get("tenant_id", "")

    if not kb_endpoint:
        return {"kb_chunks": []}

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
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(
                        f"{kb_endpoint}/api/pipeline/light-rag/retrieve",
                        headers={
                            "X-Role":      "AI_ENGINEER",
                            "X-Tenant-Id": tenant_id,
                        },
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
