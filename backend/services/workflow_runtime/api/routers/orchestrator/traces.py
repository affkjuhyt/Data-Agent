from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from services.workflow_runtime.application.runtime_services.langfuse_proxy import (
    get_trace_observations,
)

router = APIRouter()


# response_model=None: the handler may return a JSONResponse OR a plain dict, so
# tell FastAPI not to infer a response_model from the union return annotation
# (a Response subclass in the union is not a valid response field). The annotation
# stays for static typing.
@router.get("/api/orchestrate/traces/{trace_id}", response_model=None)
async def get_trace(trace_id: str) -> JSONResponse | dict[str, Any]:
    """Toàn bộ observation tree của 1 trace Langfuse. Orchestrator luôn biết
    trace_id ngay từ đầu request (= session_id, xem routers/stream.py), nên gọi
    thẳng nhánh trace_id của langfuse_proxy — không cần fallback tra theo
    sessionId như /api/traces/{conv_id} (endpoint đó vẫn giữ nguyên, phục vụ
    riêng cho use case test 1 agent lẻ trong agent-builder)."""
    return await get_trace_observations(trace_id, trace_id=trace_id)
