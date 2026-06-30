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
    parent_observation_id: Optional[str] = None,
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
            # Orchestrated mode: spans nằm trong orchestrator trace, không có trace riêng với
            # sessionId=conv_id. Frontend truyền trace_id + parent_observation_id để query đúng.
            if trace_id and parent_observation_id:
                # Lấy TẤT CẢ observations của trace rồi lọc client-side để lấy toàn bộ
                # cây con của parent_observation_id. Lý do: Langfuse CallbackHandler tạo 1
                # wrapper span ("LangGraph") làm direct child của task span — các node spans
                # thực sự nằm ở level sâu hơn. Query theo parentObservationId trực tiếp
                # chỉ trả wrapper span đó (bị filter bởi frontend), không thấy node nào.
                obs_resp = await client.get(
                    f"{host}/api/public/observations",
                    params={"traceId": trace_id, "limit": 100},
                    headers=auth_headers,
                )
                obs_resp.raise_for_status()
                all_obs = obs_resp.json().get("data", [])

                # Build cây parent → children
                children_map: dict[str, list] = {}
                for o in all_obs:
                    pid = o.get("parentObservationId") or "__root__"
                    children_map.setdefault(pid, []).append(o)

                # BFS để lấy tất cả descendants của parent_observation_id
                descendants: list[dict] = []
                queue = [parent_observation_id]
                while queue:
                    cur = queue.pop(0)
                    kids = children_map.get(cur, [])
                    descendants.extend(kids)
                    queue.extend(k["id"] for k in kids if k.get("id"))

                return {"observations": descendants, "traceId": trace_id}

            # Standalone mode: workflow-runtime tạo trace riêng với sessionId=conv_id
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
