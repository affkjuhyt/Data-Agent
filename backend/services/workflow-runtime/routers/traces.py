from typing import Any, Optional

from fastapi import APIRouter

from runtime_services.langfuse_proxy import get_trace_observations

router = APIRouter()


@router.get("/api/traces/{conv_id}", response_model=None)
async def get_traces_by_conv(
    conv_id: str,
    trace_id: Optional[str] = None,
) -> Any:
    return await get_trace_observations(conv_id, trace_id=trace_id)
