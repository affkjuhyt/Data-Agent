import base64
import logging
import os

import httpx
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


async def get_trace_observations(conv_id: str):
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    host = os.environ.get("LANGFUSE_HOST", "http://localhost:3001")

    if not public_key or not secret_key:
        return JSONResponse(status_code=503, content={"detail": "Langfuse not configured"})

    token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    auth_headers = {"Authorization": f"Basic {token}"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            traces_response = await client.get(
                f"{host}/api/public/traces",
                params={"sessionId": conv_id},
                headers=auth_headers,
            )
            traces_response.raise_for_status()
            traces = traces_response.json().get("data", [])

            if not traces:
                return {"observations": []}

            # Chỉ lấy trace mới nhất (1 turn chat = 1 trace)
            # Gộp tất cả traces sẽ khiến UI hiểu nhầm multiple turns là retry
            latest = max(traces, key=lambda t: t.get("timestamp", ""))
            obs_resp = await client.get(
                f"{host}/api/public/observations",
                params={"traceId": latest["id"]},
                headers=auth_headers,
            )
            obs_resp.raise_for_status()
            return {"observations": obs_resp.json().get("data", []), "traceId": latest["id"]}

    except httpx.HTTPStatusError as e:
        logger.error("langfuse proxy HTTP error: %s", e.response.status_code)
        return JSONResponse(status_code=502, content={"detail": f"Langfuse HTTP {e.response.status_code}"})
    except Exception:
        logger.exception("langfuse proxy error")
        return JSONResponse(status_code=502, content={"detail": "Langfuse unavailable"})
