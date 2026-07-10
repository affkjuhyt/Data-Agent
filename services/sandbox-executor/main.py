"""sandbox-executor — the only service in the platform that touches the
Docker socket (doc §1.2 hub-and-spoke isolation: one controlled gate, no
direct access from workflow-runtime). Exposes the endpoints that
workflow-runtime's execute_python / execute_sql tool handlers call over
HTTP; never reachable from outside the docker-compose network.

docs/1.md "Kế hoạch 1" mục i: POST /execute/python and /execute/sql no
longer run the request inline and hold the HTTP connection open for the
full acquire+exec/query duration — they enqueue onto a JobQueue (see
job_queue.py) and return 202 + {job_id} immediately. The caller polls
GET .../{job_id} (or subscribes to .../{job_id}/stream over SSE) until a
terminal status. This changes the wire contract between workflow-runtime
and sandbox-executor; it does NOT change the contract one layer up — the
agent's tool-call still waits for a final result, because
code_interpreter.py polls to completion before returning (mục i, "đã
chốt mô hình đồng bộ").
"""
import asyncio
import datetime
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

import minio_store
from security_visitor import validate_python_code, SecurityError
from docker_runner import (
    run_python_sandboxed,
    ExecutionTimeoutError,
    SandboxRunError,
    SandboxCircuitOpenError,
    get_pool,
    shutdown_pool,
)
from job_queue import JobQueue, JobStatus, QueueFullError, TERMINAL_STATUSES
from output import truncate_output, DEFAULT_MAX_OUTPUT_CHARS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [sandbox-executor] %(levelname)s %(message)s")
logger = logging.getLogger("sandbox-executor")

_DEFAULT_TIMEOUT_S = int(os.environ.get("SANDBOX_TIMEOUT_S", "10"))
_DEFAULT_MEMORY_MB = int(os.environ.get("SANDBOX_MEMORY_MB", "256"))
_DEFAULT_CPUS = float(os.environ.get("SANDBOX_CPUS", "0.5"))

# Workers pull jobs at the rate the resource behind them can actually
# serve (docs/1.md mục i) — sized to the container pool for python, and to
# a separate, independently-tunable cap for sql (no container involved,
# bottleneck is Snowflake connections/DDL rate instead).
_PYTHON_QUEUE_WORKERS = int(os.environ.get("SANDBOX_PYTHON_QUEUE_WORKERS", os.environ.get("SANDBOX_POOL_SIZE", "3")))
_SQL_QUEUE_WORKERS = int(os.environ.get("SANDBOX_SQL_QUEUE_WORKERS", "5"))
_JOB_TTL_S = float(os.environ.get("SANDBOX_JOB_TTL_S", "600"))
_QUEUE_MAXSIZE = int(os.environ.get("SANDBOX_QUEUE_MAXSIZE", "200"))
_SSE_HEARTBEAT_S = float(os.environ.get("SANDBOX_SSE_HEARTBEAT_S", "15"))

python_queue: JobQueue | None = None
sql_queue: JobQueue | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global python_queue, sql_queue
    # Spins up the warm container pool (docs/1.md Kế hoạch 1) once at
    # startup instead of paying container create+start cost per request.
    await asyncio.to_thread(get_pool)
    # Best-effort — see minio_store.py docstring; a MinIO outage must
    # never stop sandbox-executor from serving requests.
    await asyncio.to_thread(minio_store.ensure_bucket)

    python_queue = JobQueue(
        _run_execute_python,
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


app = FastAPI(title="sandbox-executor", version="0.1.0", lifespan=lifespan)


class ExecutePythonRequest(BaseModel):
    code: str
    timeout_s: int = _DEFAULT_TIMEOUT_S
    memory_mb: int = _DEFAULT_MEMORY_MB
    cpus: float = _DEFAULT_CPUS


class SnowflakeConnectionParams(BaseModel):
    # `schema` as a field name shadows BaseModel.schema() (Pydantic v2) —
    # alias keeps the wire/dict key "schema" (what snowflake_readonly.py and
    # workflow-runtime's code_interpreter.py both send/expect) while the
    # Python attribute is schema_name.
    model_config = ConfigDict(populate_by_name=True)

    account: str
    user: str
    password: str
    warehouse: str | None = None
    database: str | None = None
    schema_name: str | None = Field(default=None, alias="schema")
    role: str | None = None


class PostgresConnectionParams(BaseModel):
    """Wire shape for SANDBOX_SQL_BACKEND=postgres — local/dev/CI stand-in
    for Snowflake (docs/sandbox_report.md, postgres_readonly.py). No
    warehouse/role concepts; just enough to reach a Postgres database."""

    host: str
    port: int = 5432
    user: str
    password: str
    dbname: str


# docs/sandbox_report.md follow-up: SANDBOX_SQL_BACKEND picks which SQL
# engine /execute/sql talks to. Default stays "snowflake" (production,
# unchanged); "postgres" is for developers/CI without a real Snowflake
# account. The two backends are never active at once, so this is a single
# module-level switch rather than a per-request field.
_SQL_BACKEND = os.environ.get("SANDBOX_SQL_BACKEND", "snowflake")
if _SQL_BACKEND == "postgres":
    from postgres_readonly import execute_readonly_query, PostgresQueryError as SQLBackendError
    _ConnectionParams = PostgresConnectionParams
elif _SQL_BACKEND == "snowflake":
    from snowflake_readonly import execute_readonly_query, SnowflakeQueryError as SQLBackendError
    _ConnectionParams = SnowflakeConnectionParams
else:
    raise ValueError(f"unknown SANDBOX_SQL_BACKEND: {_SQL_BACKEND!r}")


class ExecuteSQLRequest(BaseModel):
    sql: str
    connection: _ConnectionParams


# -- job executors (run inside JobQueue's worker loop, not on the request
#    path) -----------------------------------------------------------------

async def _run_execute_python(payload: dict) -> dict:
    code = payload["code"]
    job_id = payload["job_id"]

    try:
        validate_python_code(code)
    except SecurityError as e:
        logger.warning("execute_python | rejected by AST validation: %s", e)
        return {"success": False, "error": f"security validation failed: {e}"}
    except SyntaxError as e:
        return {"success": False, "error": f"syntax error: {e}"}

    # docs/1.md mục h: audit copy of the submitted code, uploaded before
    # dispatch and always removed once the request ends (finally below),
    # kept/kept-deleted regardless of success/error/timeout — same "always
    # runs" branch as the container's own kill+wipe (mục c).
    minio_key = f"python/{job_id}.py"
    await asyncio.to_thread(minio_store.put_code, minio_key, code)
    try:
        result = await asyncio.to_thread(
            run_python_sandboxed, code, payload["timeout_s"], payload["memory_mb"], payload["cpus"],
            on_output=lambda stream, text: python_queue.append_output(job_id, stream, text),
        )
    except ExecutionTimeoutError as e:
        return {"success": False, "error": str(e), "timed_out": True}
    except SandboxRunError as e:
        logger.error("execute_python | sandbox run failed: %s", e)
        return {"success": False, "error": str(e)}
    # SandboxCircuitOpenError is deliberately NOT caught here — it
    # propagates to JobQueue's worker loop, which maps it to
    # JobStatus.REJECTED; the GET endpoint below turns that into an actual
    # 503 (docs/1.md mục e: "TỪ CHỐI NGAY (503)").
    finally:
        await asyncio.to_thread(minio_store.delete_code, minio_key)

    stdout, truncated = truncate_output(result["stdout"], DEFAULT_MAX_OUTPUT_CHARS)
    return {
        "success": result["exit_code"] == 0,
        "stdout": stdout,
        "stderr": result["stderr"],
        "exit_code": result["exit_code"],
        "timed_out": False,
        "is_truncated": truncated,
    }


async def _run_execute_sql(payload: dict) -> dict:
    sql = payload["sql"]
    job_id = payload["job_id"]

    minio_key = f"sql/{job_id}.sql"
    await asyncio.to_thread(minio_store.put_code, minio_key, sql)
    try:
        result = await execute_readonly_query(sql, payload["connection"])
    except SQLBackendError as e:
        return {"success": False, "error": str(e)}
    finally:
        await asyncio.to_thread(minio_store.delete_code, minio_key)

    return {
        "success": True,
        "rows": result["rows"],
        "row_count": result["row_count"],
        "is_truncated": result["is_truncated"],
    }


# -- HTTP surface ------------------------------------------------------------

def _json_default(obj):
    # execute_sql rows can carry column types neither asyncpg nor
    # snowflake-connector's DictCursor hand back as JSON-native Python
    # types — stdlib json.dumps doesn't know how to encode any of these
    # on its own. Found via an actual end-to-end run against the new
    # SANDBOX_SQL_BACKEND=postgres path (docs/sandbox_report.md): a
    # TIMESTAMPTZ column crashed the response with an unhandled 500
    # (`TypeError: Object of type datetime is not JSON serializable`) —
    # the existing unit tests never caught it because their fake rows
    # only used plain dict/int/str values, never a real datetime/UUID.
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
    def render(self, content) -> bytes:
        return json.dumps(content, ensure_ascii=False, default=_json_default).encode("utf-8")


@app.get("/health")
async def health():
    return {"status": "ok"}


def _job_summary(job, kind: str) -> dict:
    """Lightweight view for the ops dashboard's job list (docs/1.md mục
    h) — never the raw monotonic timestamps (time.monotonic() has no
    fixed epoch, so it means nothing to a human reading it directly),
    only ages/durations derived from them, and never `result`/
    `output_chunks` (can be large; the dashboard fetches those via the
    existing per-job GET/stream endpoints when a user drills in). `kind`
    is stamped on each row (not just implied by which array it's in) so
    a client that flattens both lists into one combined view still knows
    which endpoint (`/execute/{kind}/...`) a given job_id belongs to."""
    now = time.monotonic()
    return {
        "job_id": job.id,
        "kind": kind,
        "status": job.status.value,
        "age_s": round(now - job.created_at, 1),
        "duration_s": round(job.finished_at - job.created_at, 1) if job.finished_at is not None else None,
        "error": job.error,
    }


@app.get("/status")
async def get_status():
    """Ops dashboard endpoint (docs/1.md mục h) — pool health (idle/busy/
    circuit breaker state) plus a snapshot of recent jobs in both queues.
    Not on the request path; polled periodically by a dashboard, not by
    sandbox execution itself."""
    return {
        "pool": get_pool().stats(),
        "jobs": {
            "python": [_job_summary(j, "python") for j in python_queue.list_jobs()],
            "sql": [_job_summary(j, "sql") for j in sql_queue.list_jobs()],
        },
    }


def _submit(queue: JobQueue, payload: dict) -> JSONResponse:
    try:
        job_id = queue.submit(payload)
    except QueueFullError as e:
        return JSONResponse(status_code=503, content={"error": str(e)})
    return JSONResponse(status_code=202, content={"job_id": job_id, "status": "queued"})


def _job_status_response(queue: JobQueue, job_id: str) -> JSONResponse:
    job = queue.get(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "unknown or expired job_id"})

    if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
        return JSONResponse(status_code=200, content={"job_id": job_id, "status": job.status.value})

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


def _sse_line(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, default=_json_default)}\n\n"


async def _sse_events(queue: JobQueue, job_id: str):
    """docs/1.md mục h: "stream trạng thái/stdout-stderr của lần exec
    đang chạy" — status transitions (queued -> running -> done/rejected/
    error) AND stdout/stderr chunks as the container produces them, not
    only the final polled result. Real incremental output for
    /execute/python (docker_runner.py execs with exec_start(stream=True)
    and python3 -u, feeding job.output_chunks via JobQueue.append_output
    as each chunk arrives). /execute/sql has no equivalent mid-flight
    output — a Snowflake query either hasn't returned yet or has — so its
    stream only ever carries status/done events, never "output"."""
    job = queue.get(job_id)
    if job is None:
        yield _sse_line("error", {"error": "unknown or expired job_id"})
        return

    last_status: JobStatus | None = None
    last_output_len = 0
    while True:
        emitted = False

        if len(job.output_chunks) != last_output_len:
            for stream, text in job.output_chunks[last_output_len:]:
                yield _sse_line("output", {"job_id": job_id, "stream": stream, "text": text})
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
            yield _sse_line("done" if terminal else "status", payload)
            emitted = True
            if terminal:
                return

        if not emitted:
            yield ": heartbeat\n\n"

        job = await queue.wait_for_change(
            job_id, last_status, timeout_s=_SSE_HEARTBEAT_S, last_output_len=last_output_len,
        )
        if job is None:
            yield _sse_line("error", {"error": "job expired"})
            return


@app.post("/execute/python")
async def execute_python(req: ExecutePythonRequest):
    return _submit(python_queue, {
        "code": req.code, "timeout_s": req.timeout_s, "memory_mb": req.memory_mb, "cpus": req.cpus,
    })


@app.get("/execute/python/{job_id}")
async def get_execute_python(job_id: str):
    return _job_status_response(python_queue, job_id)


@app.get("/execute/python/{job_id}/stream")
async def stream_execute_python(job_id: str):
    return StreamingResponse(_sse_events(python_queue, job_id), media_type="text/event-stream")


@app.post("/execute/sql")
async def execute_sql(req: ExecuteSQLRequest):
    return _submit(sql_queue, {"sql": req.sql, "connection": req.connection.model_dump(by_alias=True)})


@app.get("/execute/sql/{job_id}")
async def get_execute_sql(job_id: str):
    return _job_status_response(sql_queue, job_id)


@app.get("/execute/sql/{job_id}/stream")
async def stream_execute_sql(job_id: str):
    return StreamingResponse(_sse_events(sql_queue, job_id), media_type="text/event-stream")
