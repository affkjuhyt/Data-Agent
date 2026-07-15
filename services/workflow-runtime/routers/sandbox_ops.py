from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from runtime_services.sandbox_proxy import get_sandbox_job, get_sandbox_status, stream_sandbox_job

router = APIRouter()


def _check_kind(kind: str) -> JSONResponse | None:
    if kind not in ("python", "sql"):
        return JSONResponse(status_code=400, content={"error": "kind must be 'python' or 'sql'"})
    return None


@router.get("/api/sandbox/status")
async def sandbox_status():
    return await get_sandbox_status()


@router.get("/api/sandbox/job/{kind}/{job_id}")
async def sandbox_job(kind: str, job_id: str):
    return _check_kind(kind) or await get_sandbox_job(kind, job_id)


@router.get("/api/sandbox/stream/{kind}/{job_id}")
async def sandbox_stream(kind: str, job_id: str):
    return _check_kind(kind) or StreamingResponse(stream_sandbox_job(kind, job_id), media_type="text/event-stream")
