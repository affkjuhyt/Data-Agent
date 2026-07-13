"""Relay to sandbox-executor's ops-dashboard endpoints (docs/1.md Kế hoạch 1
mục h — "vận hành nhìn thấy job đang chạy gì").

sandbox-executor is internal-only (doc §1.2 hub-and-spoke isolation — same
reason code_interpreter.py's tool handlers proxy through it instead of the
browser calling it directly): no port exposed to the host, not reachable
through Kong, unreachable from the frontend. This module is the read-only
counterpart to code_interpreter.py's write path (submit+poll) — it never
submits or mutates a job, only relays status/stream reads for an ops UI.
"""
import json
import logging
import os

import httpx
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

_SANDBOX_EXECUTOR_URL = os.environ.get("SANDBOX_EXECUTOR_URL", "http://localhost:8500")


async def get_sandbox_status() -> JSONResponse:
    try:
        async with httpx.AsyncClient(timeout=5) as http:
            resp = await http.get(f"{_SANDBOX_EXECUTOR_URL}/status")
            return JSONResponse(status_code=resp.status_code, content=resp.json())
    except Exception as e:
        logger.warning("sandbox_proxy | status relay failed: %s", e)
        return JSONResponse(status_code=503, content={"error": f"sandbox-executor unreachable: {e}"})


async def get_sandbox_job(kind: str, job_id: str) -> JSONResponse:
    """Full detail (stdout/stderr/rows, not just the /status list's
    summary) for one job — used when a dashboard user drills into a job
    that's already finished, where polling once is enough and opening an
    SSE connection would be pointless."""
    try:
        async with httpx.AsyncClient(timeout=5) as http:
            resp = await http.get(f"{_SANDBOX_EXECUTOR_URL}/execute/{kind}/{job_id}")
            return JSONResponse(status_code=resp.status_code, content=resp.json())
    except Exception as e:
        logger.warning("sandbox_proxy | job detail relay failed for %s job %s: %s", kind, job_id, e)
        return JSONResponse(status_code=503, content={"error": f"sandbox-executor unreachable: {e}"})


async def stream_sandbox_job(kind: str, job_id: str):
    """Async generator of raw SSE bytes — the caller wraps this in a
    StreamingResponse. Relays sandbox-executor's own event framing
    unchanged (event:/data: lines) rather than re-parsing and re-emitting
    it, so main.py's _sse_events (job_queue.py's JobStatus/output-chunk
    shape) stays the single source of truth for the wire format.
    """
    url = f"{_SANDBOX_EXECUTOR_URL}/execute/{kind}/{job_id}/stream"
    try:
        async with httpx.AsyncClient(timeout=None) as http:
            async with http.stream("GET", url) as resp:
                async for chunk in resp.aiter_bytes():
                    yield chunk
    except Exception as e:
        logger.warning("sandbox_proxy | stream relay failed for %s job %s: %s", kind, job_id, e)
        payload = json.dumps({"error": f"sandbox-executor unreachable: {e}"})
        yield f"event: error\ndata: {payload}\n\n".encode()
