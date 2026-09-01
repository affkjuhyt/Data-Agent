"""JSON, polling, and SSE response helpers for sandbox_executor."""

from __future__ import annotations

from collections.abc import AsyncIterator
import datetime
from decimal import Decimal
import json
import time
from typing import Any
import uuid

from fastapi.responses import JSONResponse

from job_queue import Job, JobQueue, JobStatus, QueueFullError, TERMINAL_STATUSES


def json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime.datetime, datetime.date, datetime.time)):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, (bytes, bytearray)):
        return obj.hex()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


class SandboxJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            default=json_default,
        ).encode("utf-8")


def job_summary(job: Job, kind: str) -> dict[str, Any]:
    now = time.monotonic()
    return {
        "job_id": job.id,
        "kind": kind,
        "status": job.status.value,
        "age_s": round(now - job.created_at, 1),
        "duration_s": (
            round(job.finished_at - job.created_at, 1)
            if job.finished_at is not None
            else None
        ),
        "error": job.error,
    }


def submit(queue: JobQueue, payload: dict[str, Any]) -> JSONResponse:
    try:
        job_id = queue.submit(payload)
    except QueueFullError as exc:
        return JSONResponse(status_code=503, content={"error": str(exc)})
    return JSONResponse(
        status_code=202,
        content={"job_id": job_id, "status": "queued"},
    )


def job_status_response(queue: JobQueue, job_id: str) -> JSONResponse:
    job = queue.get(job_id)
    if job is None:
        return JSONResponse(
            status_code=404,
            content={"error": "unknown or expired job_id"},
        )
    if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
        return JSONResponse(
            status_code=200,
            content={"job_id": job_id, "status": job.status.value},
        )
    if job.status == JobStatus.REJECTED:
        return JSONResponse(
            status_code=503,
            content={"job_id": job_id, "status": "rejected", "error": job.error},
        )
    if job.status == JobStatus.ERROR:
        return JSONResponse(
            status_code=500,
            content={"job_id": job_id, "status": "error", "error": job.error},
        )

    body = dict(job.result or {})
    body["job_id"] = job_id
    body["status"] = "done"
    return SandboxJSONResponse(status_code=200, content=body)


def sse_line(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, default=json_default)}\n\n"


async def sse_events(
    queue: JobQueue,
    job_id: str,
    *,
    heartbeat_s: float,
) -> AsyncIterator[str]:
    job = queue.get(job_id)
    if job is None:
        yield sse_line("error", {"error": "unknown or expired job_id"})
        return

    last_status: JobStatus | None = None
    last_output_len = 0
    while True:
        emitted = False

        if len(job.output_chunks) != last_output_len:
            for stream, text in job.output_chunks[last_output_len:]:
                yield sse_line(
                    "output",
                    {"job_id": job_id, "stream": stream, "text": text},
                )
            last_output_len = len(job.output_chunks)
            emitted = True

        if job.status != last_status:
            last_status = job.status
            payload = {"job_id": job_id, "status": job.status.value}
            if job.status == JobStatus.DONE:
                payload.update(job.result or {})
            elif job.error:
                payload["error"] = job.error

            terminal = job.status in TERMINAL_STATUSES
            yield sse_line("done" if terminal else "status", payload)
            emitted = True
            if terminal:
                return

        if not emitted:
            yield ": heartbeat\n\n"

        job = await queue.wait_for_change(
            job_id,
            last_status,
            timeout_s=heartbeat_s,
            last_output_len=last_output_len,
        )
        if job is None:
            yield sse_line("error", {"error": "job expired"})
            return

