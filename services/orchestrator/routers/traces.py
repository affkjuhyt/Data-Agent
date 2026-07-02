from __future__ import annotations

import base64
import logging
import os

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/orchestrate/traces/{trace_id}")
async def get_trace(trace_id: str):
    """Proxy sang Langfuse REST API — trả về toàn bộ observation tree của 1
    trace. Orchestrator luôn biết trace_id ngay từ đầu request (= session_id,
    xem routers/stream.py), nên không cần fallback tra theo sessionId như
    workflow-runtime's /api/traces/{conv_id} (endpoint đó vẫn giữ nguyên,
    phục vụ riêng cho use case test 1 agent lẻ trong agent-builder)."""
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    host = os.environ.get("LANGFUSE_HOST", "http://localhost:3001")

    if not public_key or not secret_key:
        return JSONResponse(status_code=503, content={"detail": "Langfuse not configured"})

    token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    auth_headers = {"Authorization": f"Basic {token}"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            obs_resp = await client.get(
                f"{host}/api/public/observations",
                # Langfuse v3's /api/public/observations validate limit <= 100
                # (v2 chấp nhận limit lớn hơn — breaking change khi nâng server).
                params={"traceId": trace_id, "limit": 100},
                headers=auth_headers,
            )
            obs_resp.raise_for_status()
            return {"observations": obs_resp.json().get("data", []), "traceId": trace_id}
    except httpx.HTTPStatusError as e:
        logger.error(
            "orchestrate traces proxy | HTTP error: %s body=%s url=%s",
            e.response.status_code, e.response.text, str(e.request.url),
        )
        return JSONResponse(status_code=502, content={"detail": f"Langfuse HTTP {e.response.status_code}"})
    except Exception:
        logger.exception("orchestrate traces proxy | error")
        return JSONResponse(status_code=502, content={"detail": "Langfuse unavailable"})
