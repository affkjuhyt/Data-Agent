"""sandbox_executor — the only service in the platform that touches the
Docker socket (doc §1.2 hub-and-spoke isolation: one controlled gate, no
direct access from workflow_runtime). Exposes the endpoints that
workflow_runtime's execute_python / execute_sql tool handlers call over
HTTP; never reachable from outside the docker-compose network.

backend/services/sandbox_executor/ops/ "Kế hoạch 1" mục i: POST /execute/python and /execute/sql no
longer run the request inline and hold the HTTP connection open for the
full acquire+exec/query duration — they enqueue onto a JobQueue (see
job_queue.py) and return 202 + {job_id} immediately. The caller polls
GET .../{job_id} (or subscribes to .../{job_id}/stream over SSE) until a
terminal status. This changes the wire contract between workflow_runtime
and sandbox_executor; it does NOT change the contract one layer up — the
agent's tool-call still waits for a final result, because
code_interpreter.py polls to completion before returning (mục i, "đã
chốt mô hình đồng bộ").
"""
import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import minio_store
from api.responses import (
    job_status_response as _response_job_status,
)
from api.responses import (
    job_summary as _response_job_summary,
)
from api.responses import (
    sse_events as _response_sse_events,
)
from api.responses import (
    submit as _response_submit,
)
from api.schemas import (
    CONNECTION_PARAMS,
    ExecutePythonRequest,
    ExecuteShellRequest,
    ExecuteSQLRequest,
)
from core.settings import load_settings
from execution.code_executor import run_python_job, run_shell_job
from execution.sql_executor import load_backend, run_sql_job
from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from job_queue import Job, JobQueue
from output import DEFAULT_MAX_OUTPUT_CHARS, truncate_output
from sandbox_runner_backend import (
    ExecutionTimeoutError,
    SandboxCircuitOpenError,
    SandboxRunError,
    get_pool,
    run_python_sandboxed,
    run_shell_sandboxed,
    shutdown_pool,
)
from security_visitor import SecurityError, validate_python_code
from sql_guard import SQLGuardError, validate_select_only

logging.basicConfig(level=logging.INFO, format="%(asctime)s [sandbox_executor] %(levelname)s %(message)s")
logger = logging.getLogger("sandbox_executor")

_settings = load_settings()
_DEFAULT_TIMEOUT_S = _settings.timeout_s
_DEFAULT_MEMORY_MB = _settings.memory_mb
_DEFAULT_CPUS = _settings.cpus
_PYTHON_QUEUE_WORKERS = _settings.python_queue_workers
_SQL_QUEUE_WORKERS = _settings.sql_queue_workers
_JOB_TTL_S = _settings.job_ttl_s
_QUEUE_MAXSIZE = _settings.queue_maxsize
_MAX_INPUT_BYTES = _settings.max_input_bytes
_SSE_HEARTBEAT_S = _settings.sse_heartbeat_s
_SQL_BACKEND = _settings.sql_backend
_CONNECTION_PARAMS = CONNECTION_PARAMS

python_queue: JobQueue | None = None
sql_queue: JobQueue | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global python_queue, sql_queue
    # Spins up the warm container pool (backend/services/sandbox_executor/ops/ Kế hoạch 1) once at
    # startup instead of paying container create+start cost per request.
    await asyncio.to_thread(get_pool)
    # Best-effort — see minio_store.py docstring; a MinIO outage must
    # never stop sandbox_executor from serving requests.
    await asyncio.to_thread(minio_store.ensure_bucket)

    python_queue = JobQueue(
        _run_sandbox_job,
        num_workers=_PYTHON_QUEUE_WORKERS,
        rejection_exc=SandboxCircuitOpenError,
        job_ttl_s=_JOB_TTL_S,
        maxsize=_QUEUE_MAXSIZE,
    )
    sql_queue = JobQueue(
        _run_execute_sql,
        num_workers=_SQL_QUEUE_WORKERS,
        job_ttl_s=_JOB_TTL_S,
        maxsize=_QUEUE_MAXSIZE,
    )
    python_queue.start()
    sql_queue.start()
    try:
        yield
    finally:
        await python_queue.stop()
        await sql_queue.stop()
        await asyncio.to_thread(shutdown_pool)


app = FastAPI(title="sandbox_executor", version="0.1.0", lifespan=lifespan)


def _load_backend(backend: str) -> tuple[Any, type[Exception]]:
    """Compatibility facade for tests and callers that patch ``main._load_backend``."""
    return load_backend(backend)


# -- job executors (run inside JobQueue's worker loop, not on the request
#    path) -----------------------------------------------------------------

async def _run_sandbox_job(payload: dict[str, Any]) -> dict[str, Any]:
    """python_queue's executor. Python and shell share one queue because they
    share one resource — a container from the pool — so two queues would let
    their worker counts add up past the pool size and turn a full pool into
    acquire timeouts instead of honest backpressure."""
    if payload.get("kind") == "shell":
        return await _run_execute_shell(payload)
    return await _run_execute_python(payload)


async def _run_execute_shell(payload: dict[str, Any]) -> dict[str, Any]:
    assert python_queue is not None
    return await run_shell_job(
        payload,
        queue=python_queue,
        runner=run_shell_sandboxed,
        timeout_error=ExecutionTimeoutError,
        run_error=SandboxRunError,
        minio_store=minio_store,
        truncate_output=truncate_output,
        max_output_chars=DEFAULT_MAX_OUTPUT_CHARS,
        logger=logger,
    )


async def _run_execute_python(payload: dict[str, Any]) -> dict[str, Any]:
    assert python_queue is not None
    return await run_python_job(
        payload,
        queue=python_queue,
        runner=run_python_sandboxed,
        timeout_error=ExecutionTimeoutError,
        run_error=SandboxRunError,
        minio_store=minio_store,
        validate_code=validate_python_code,
        security_error=SecurityError,
        truncate_output=truncate_output,
        max_output_chars=DEFAULT_MAX_OUTPUT_CHARS,
        logger=logger,
    )


async def _run_execute_sql(payload: dict[str, Any]) -> dict[str, Any]:
    return await run_sql_job(
        payload,
        backend_loader=_load_backend,
        validate_select_only=validate_select_only,
        guard_error=SQLGuardError,
        minio_store=minio_store,
        logger=logger,
    )


# -- HTTP surface ------------------------------------------------------------


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def _job_summary(job: Job, kind: str) -> dict[str, Any]:
    """Lightweight view for the ops dashboard's job list (backend/services/sandbox_executor/ops/ mục
    h) — never the raw monotonic timestamps (time.monotonic() has no
    fixed epoch, so it means nothing to a human reading it directly),
    only ages/durations derived from them, and never `result`/
    `output_chunks` (can be large; the dashboard fetches those via the
    existing per-job GET/stream endpoints when a user drills in). `kind`
    is stamped on each row (not just implied by which array it's in) so
    a client that flattens both lists into one combined view still knows
    which endpoint (`/execute/{kind}/...`) a given job_id belongs to."""
    return _response_job_summary(job, kind)


@app.get("/status")
async def get_status() -> dict[str, Any]:
    """Ops dashboard endpoint (backend/services/sandbox_executor/ops/ mục h) — pool health (idle/busy/
    circuit breaker state) plus a snapshot of recent jobs in both queues.
    Not on the request path; polled periodically by a dashboard, not by
    sandbox execution itself."""
    # Set once in lifespan() before this app ever starts serving requests.
    assert python_queue is not None
    assert sql_queue is not None
    return {
        "pool": get_pool().stats(),
        "jobs": {
            # Kind read back from each job's payload, not from which list it is
            # in: python and shell jobs share python_queue, and `kind` is what
            # tells a client which /execute/{kind}/ endpoint a job_id belongs to.
            "python": [
                _job_summary(j, str(python_queue.payload_of(j.id).get("kind") or "python"))
                for j in python_queue.list_jobs()
            ],
            "sql": [_job_summary(j, "sql") for j in sql_queue.list_jobs()],
        },
    }


def _submit(queue: JobQueue, payload: dict[str, Any]) -> JSONResponse:
    return _response_submit(queue, payload)


def _job_status_response(queue: JobQueue, job_id: str) -> JSONResponse:
    return _response_job_status(queue, job_id)


async def _sse_events(queue: JobQueue, job_id: str) -> AsyncIterator[str]:
    """backend/services/sandbox_executor/ops/ mục h: "stream trạng thái/stdout-stderr của lần exec
    đang chạy" — status transitions (queued -> running -> done/rejected/
    error) AND stdout/stderr chunks as the container produces them, not
    only the final polled result. Real incremental output for
    /execute/python (docker_runner.py execs with exec_start(stream=True)
    and python3 -u, feeding job.output_chunks via JobQueue.append_output
    as each chunk arrives). /execute/sql has no equivalent mid-flight
    output — a Snowflake query either hasn't returned yet or has — so its
    stream only ever carries status/done events, never "output"."""
    async for event in _response_sse_events(
        queue,
        job_id,
        heartbeat_s=_SSE_HEARTBEAT_S,
    ):
        yield event


@app.post("/execute/python")
async def execute_python(req: ExecutePythonRequest) -> JSONResponse:
    assert python_queue is not None  # set in lifespan() before requests are served
    return _submit(python_queue, {
        "kind": "python",
        "code": req.code, "timeout_s": req.timeout_s, "memory_mb": req.memory_mb, "cpus": req.cpus,
        "files": req.files,
    })


@app.post("/execute/shell")
async def execute_shell(req: ExecuteShellRequest) -> JSONResponse:
    assert python_queue is not None  # set in lifespan() before requests are served
    return _submit(python_queue, {
        "kind": "shell", "command": req.command, "timeout_s": req.timeout_s, "workdir": req.workdir,
    })


@app.get("/execute/shell/{job_id}")
async def get_execute_shell(job_id: str) -> JSONResponse:
    assert python_queue is not None  # set in lifespan() before requests are served
    return _job_status_response(python_queue, job_id)


@app.get("/execute/shell/{job_id}/stream")
async def stream_execute_shell(job_id: str) -> StreamingResponse:
    assert python_queue is not None  # set in lifespan() before requests are served
    return StreamingResponse(_sse_events(python_queue, job_id), media_type="text/event-stream")


@app.get("/execute/python/{job_id}")
async def get_execute_python(job_id: str) -> JSONResponse:
    assert python_queue is not None  # set in lifespan() before requests are served
    return _job_status_response(python_queue, job_id)


@app.get("/execute/python/{job_id}/stream")
async def stream_execute_python(job_id: str) -> StreamingResponse:
    assert python_queue is not None  # set in lifespan() before requests are served
    return StreamingResponse(_sse_events(python_queue, job_id), media_type="text/event-stream")


@app.post("/execute/sql")
async def execute_sql(req: ExecuteSQLRequest) -> JSONResponse:
    assert sql_queue is not None  # set in lifespan() before requests are served
    # req.connection is already the validated, backend-tagged dict the
    # field_validator produced — no second model_dump needed.
    return _submit(sql_queue, {"sql": req.sql, "connection": req.connection})


@app.get("/execute/sql/{job_id}")
async def get_execute_sql(job_id: str) -> JSONResponse:
    assert sql_queue is not None  # set in lifespan() before requests are served
    return _job_status_response(sql_queue, job_id)


@app.get("/execute/sql/{job_id}/stream")
async def stream_execute_sql(job_id: str) -> StreamingResponse:
    assert sql_queue is not None  # set in lifespan() before requests are served
    return StreamingResponse(_sse_events(sql_queue, job_id), media_type="text/event-stream")
