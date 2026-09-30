import asyncio
import base64
import logging
import os
from typing import Any, Optional

import httpx
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# Langfuse v3's /api/public/observations validates limit <= 100.
_PAGE_SIZE = 100
# The orchestrator's root span (routers/stream.py). Spans are exported when
# they end and the root ends last, so once it is stored the trace is whole.
_ROOT_SPAN_NAME = "orchestrate"

_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    # One pooled client: the trace panel polls this route, and a fresh client
    # per call paid a new connection to Langfuse every time.
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=10.0)
    return _client


async def _all_observations(host: str, headers: dict[str, str], trace_id: str) -> list[dict[str, Any]]:
    """Every page of a trace's observations. Only the first page used to be
    read, so a single agent turn (~185 observations) lost everything after the
    first 100 — typically its last steps."""
    url = f"{host}/api/public/observations"

    async def page(n: int) -> dict[str, Any]:
        resp = await _http().get(url, params={"traceId": trace_id, "limit": _PAGE_SIZE, "page": n}, headers=headers)
        resp.raise_for_status()
        body: dict[str, Any] = resp.json()
        return body

    first = await page(1)
    data: list[dict[str, Any]] = list(first.get("data", []))
    total_pages = int((first.get("meta") or {}).get("totalPages") or 1)
    if total_pages > 1:
        for rest in await asyncio.gather(*(page(n) for n in range(2, total_pages + 1))):
            data.extend(rest.get("data", []))
    return data


def _is_complete(observations: list[dict[str, Any]]) -> bool:
    # The root span still carries a parentObservationId — the remote parent
    # from the trace_context it was started with, which is never stored — so
    # "root" means its parent is not part of this trace.
    ids = {o.get("id") for o in observations}
    return any(
        o.get("name") == _ROOT_SPAN_NAME and o.get("parentObservationId") not in ids and o.get("endTime")
        for o in observations
    )


async def get_trace_observations(
    conv_id: str,
    trace_id: Optional[str] = None,
) -> JSONResponse | dict[str, Any]:
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    host = os.environ.get("LANGFUSE_HOST", "http://localhost:8420")

    if not public_key or not secret_key:
        return JSONResponse(status_code=503, content={"detail": "Langfuse not configured"})

    token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    auth_headers = {"Authorization": f"Basic {token}"}

    try:
        if trace_id:
            # Orchestrated mode: trả về TẤT CẢ observations của trace.
            # BFS để filter descendants được xử lý client-side (getDescendants trong frontend).
            observations = await _all_observations(host, auth_headers, trace_id)
            # `complete` lets the trace panel stop polling the moment the root
            # span is stored, instead of guessing from a count that stops changing.
            return {"observations": observations, "traceId": trace_id, "complete": _is_complete(observations)}

        # Standalone fallback: agent chạy trực tiếp không qua orchestrator,
        # workflow_runtime tạo trace riêng với sessionId=conv_id.
        traces_response = await _http().get(
            f"{host}/api/public/traces",
            params={"sessionId": conv_id},
            headers=auth_headers,
        )
        traces_response.raise_for_status()
        traces = traces_response.json().get("data", [])

        if not traces:
            return {"observations": []}

        latest = max(traces, key=lambda t: t.get("timestamp", ""))
        observations = await _all_observations(host, auth_headers, latest["id"])
        return {"observations": observations, "traceId": latest["id"]}

    except httpx.HTTPStatusError as e:
        logger.error("langfuse proxy HTTP error: %s body=%s", e.response.status_code, e.response.text)
        return JSONResponse(status_code=502, content={"detail": f"Langfuse HTTP {e.response.status_code}"})
    except Exception:
        logger.exception("langfuse proxy error")
        return JSONResponse(status_code=502, content={"detail": "Langfuse unavailable"})
