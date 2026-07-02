import base64
import logging
import os
from typing import Optional

import httpx
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


async def get_trace_observations(
    conv_id: str,
    trace_id: Optional[str] = None,
):
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    host = os.environ.get("LANGFUSE_HOST", "http://localhost:3001")

    if not public_key or not secret_key:
        return JSONResponse(status_code=503, content={"detail": "Langfuse not configured"})

    token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    auth_headers = {"Authorization": f"Basic {token}"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if trace_id:
                # Orchestrated mode: trả về TẤT CẢ observations của trace.
                # BFS để filter descendants được xử lý client-side (getDescendants trong frontend).
                obs_resp = await client.get(
                    f"{host}/api/public/observations",
                    # Langfuse v3's /api/public/observations validate limit <= 100.
                    params={"traceId": trace_id, "limit": 100},
                    headers=auth_headers,
                )
                obs_resp.raise_for_status()
                return {"observations": obs_resp.json().get("data", []), "traceId": trace_id}

            # Standalone fallback: agent chạy trực tiếp không qua orchestrator,
            # workflow-runtime tạo trace riêng với sessionId=conv_id.
            traces_response = await client.get(
                f"{host}/api/public/traces",
                params={"sessionId": conv_id},
                headers=auth_headers,
            )
            traces_response.raise_for_status()
            traces = traces_response.json().get("data", [])

            if not traces:
                return {"observations": []}

            latest = max(traces, key=lambda t: t.get("timestamp", ""))
            obs_resp = await client.get(
                f"{host}/api/public/observations",
                params={"traceId": latest["id"]},
                headers=auth_headers,
            )
            obs_resp.raise_for_status()
            return {"observations": obs_resp.json().get("data", []), "traceId": latest["id"]}

    except httpx.HTTPStatusError as e:
        logger.error("langfuse proxy HTTP error: %s body=%s", e.response.status_code, e.response.text)
        return JSONResponse(status_code=502, content={"detail": f"Langfuse HTTP {e.response.status_code}"})
    except Exception:
        logger.exception("langfuse proxy error")
        return JSONResponse(status_code=502, content={"detail": "Langfuse unavailable"})
