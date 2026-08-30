"""sandbox-executor — the only service in the platform that touches the
Docker socket (doc §1.2 hub-and-spoke isolation: one controlled gate, no
direct access from workflow-runtime). Exposes the endpoints that
workflow-runtime's execute_python / execute_sql tool handlers call over
HTTP; never reachable from outside the docker-compose network.

backend/services/sandbox-executor/ops/ "Kế hoạch 1" mục i: POST /execute/python and /execute/sql no
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
from typing import Any
from collections.abc import AsyncIterator
import asyncio
import base64
import datetime
import functools
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

import minio_store
from input_names import validate_input_name
from security_visitor import validate_python_code, SecurityError
from sql_guard import validate_select_only, SQLGuardError
from docker_runner import (
    run_python_sandboxed,
    run_shell_sandboxed,
    ExecutionTimeoutError,
    SandboxRunError,
    SandboxCircuitOpenError,
    get_pool,
    shutdown_pool,
)
from job_queue import Job, JobQueue, JobStatus, QueueFullError, TERMINAL_STATUSES
from output import truncate_output, DEFAULT_MAX_OUTPUT_CHARS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [sandbox-executor] %(levelname)s %(message)s")
logger = logging.getLogger("sandbox-executor")

_DEFAULT_TIMEOUT_S = int(os.environ.get("SANDBOX_TIMEOUT_S", "10"))
_DEFAULT_MEMORY_MB = int(os.environ.get("SANDBOX_MEMORY_MB", "256"))
_DEFAULT_CPUS = float(os.environ.get("SANDBOX_CPUS", "0.5"))

# Workers pull jobs at the rate the resource behind them can actually
# serve (backend/services/sandbox-executor/ops/ mục i) — sized to the container pool for python, and to
# a separate, independently-tunable cap for sql (no container involved,
# bottleneck is Snowflake connections/DDL rate instead).
_PYTHON_QUEUE_WORKERS = int(os.environ.get("SANDBOX_PYTHON_QUEUE_WORKERS", os.environ.get("SANDBOX_POOL_SIZE", "3")))
_SQL_QUEUE_WORKERS = int(os.environ.get("SANDBOX_SQL_QUEUE_WORKERS", "5"))
_JOB_TTL_S = float(os.environ.get("SANDBOX_JOB_TTL_S", "600"))
_QUEUE_MAXSIZE = int(os.environ.get("SANDBOX_QUEUE_MAXSIZE", "200"))
# Total size of the input files one /execute/python request may stage into the
# container. The sandbox's whole memory budget is SANDBOX_MEMORY_MB (256 by
# default) and the bytes travel base64'd inside one JSON request, so this is a
# transport and memory bound, not a policy one.
_MAX_INPUT_BYTES = int(os.environ.get("SANDBOX_MAX_INPUT_BYTES", str(8 * 1024 * 1024)))
_SSE_HEARTBEAT_S = float(os.environ.get("SANDBOX_SSE_HEARTBEAT_S", "15"))

python_queue: JobQueue | None = None
sql_queue: JobQueue | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global python_queue, sql_queue
    # Spins up the warm container pool (backend/services/sandbox-executor/ops/ Kế hoạch 1) once at
    # startup instead of paying container create+start cost per request.
    await asyncio.to_thread(get_pool)
    # Best-effort — see minio_store.py docstring; a MinIO outage must
    # never stop sandbox-executor from serving requests.
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


app = FastAPI(title="sandbox-executor", version="0.1.0", lifespan=lifespan)


class ExecutePythonRequest(BaseModel):
    code: str
    timeout_s: int = _DEFAULT_TIMEOUT_S
    memory_mb: int = _DEFAULT_MEMORY_MB
    cpus: float = _DEFAULT_CPUS
    # {bare filename: base64 content}, staged into the container's /tmp before
    # the code runs. The sandbox cannot see the caller's filesystem — that is
    # the isolation, not an oversight — so a tool whose subject is a file (
    # workflow-runtime's text_to_pandas) sends the bytes with the code.
    files: dict[str, str] = Field(default_factory=dict)

    @field_validator("files")
    @classmethod
    def _validate_files(cls, value: dict[str, str]) -> dict[str, str]:
        """Names must be single path segments and the total must fit the cap.

        Decoded here purely to validate — a 422 at submit time says "this
        request is malformed", which is true and actionable, where the same
        failure discovered inside the worker would come back as a mysterious
        job error. _run_execute_python decodes again for real; base64 of a few
        MiB is not worth threading decoded bytes through the request model to
        avoid.
        """
        total = 0
        for name, encoded in value.items():
            # Same function docker_runner calls at staging time — one rule, so
            # the 422 here and the refusal there can never disagree.
            validate_input_name(name)
            try:
                total += len(base64.b64decode(encoded, validate=True))
            except Exception as e:
                raise ValueError(f"input file {name!r} is not valid base64: {e}") from e
        if total > _MAX_INPUT_BYTES:
            raise ValueError(
                f"input files total {total} bytes, over the {_MAX_INPUT_BYTES}-byte limit "
                "(SANDBOX_MAX_INPUT_BYTES)"
            )
        return value


class ExecuteShellRequest(BaseModel):
    """A shell command to run IN THE SANDBOX.

    There is no allowlist and no parsing of `command`, deliberately: a shell
    command cannot be validated the way Python source can, so the container is
    the control. It is the same container /execute/python uses, with the same
    network/filesystem/resource limits and the same wipe afterwards, which is
    why this endpoint is not a new hole — it is where workflow-runtime's `bash`
    tool stopped being one.
    """

    command: str
    timeout_s: int = _DEFAULT_TIMEOUT_S
    # A directory INSIDE the container. The tool's `cwd` config used to name a
    # path on workflow-runtime's own filesystem, which is exactly what it no
    # longer has access to.
    workdir: str | None = None


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
    """Wire shape for the postgres backend — originally the local/dev/CI
    stand-in for Snowflake (docs/sandbox_report.md, postgres_readonly.py),
    now also the target of every DB Connector node connection. No
    warehouse/role concepts; just enough to reach a Postgres database."""

    host: str
    port: int = 5432
    user: str
    password: str
    dbname: str


class ClickHouseConnectionParams(BaseModel):
    """Wire shape for the clickhouse backend (clickhouse_readonly.py) — the
    other target type the DB Connector canvas node offers."""

    host: str
    port: int = 8123
    user: str
    password: str
    database: str


# SANDBOX_SQL_BACKEND is now only the DEFAULT for a request that doesn't name
# its own backend — it is no longer a global mode. It used to be: the module
# imported exactly one backend at startup and `connection` was pinned to that
# one shape, which meant /execute/sql could serve Snowflake OR Postgres but
# never both. That made it unusable for the DB Connector node, whose whole
# point is several per-tenant connections of possibly different engines in one
# agent turn — so that node bypassed this service entirely and ran LLM SQL
# through raw asyncpg with no guard at all. Dispatching per request is what
# lets that path come back behind the guard.
#
# Backward compatible: code_interpreter.py's execute_sql tool still posts a
# bare Snowflake connection with no "type", and still resolves to snowflake
# under the unchanged default.
_SQL_BACKEND = os.environ.get("SANDBOX_SQL_BACKEND", "snowflake")

_CONNECTION_PARAMS: dict[str, type[BaseModel]] = {
    "postgres":   PostgresConnectionParams,
    "snowflake":  SnowflakeConnectionParams,
    "clickhouse": ClickHouseConnectionParams,
}

if _SQL_BACKEND not in _CONNECTION_PARAMS:
    raise ValueError(
        f"unknown SANDBOX_SQL_BACKEND: {_SQL_BACKEND!r} "
        f"(expected one of {sorted(_CONNECTION_PARAMS)})"
    )


@functools.cache
def _load_backend(backend: str) -> tuple[Any, type[Exception]]:
    """Import a backend's driver on first use and return
    (execute_readonly_query, its error type).

    Lazy and cached rather than imported at module scope: each backend pulls a
    different driver (snowflake-connector-python / asyncpg / clickhouse-connect)
    and an image is not obliged to carry all three. A missing driver now fails
    the one request that needs it, with the ImportError text intact, instead of
    stopping the whole service from starting.
    """
    if backend == "postgres":
        from postgres_readonly import PostgresQueryError, execute_readonly_query
        return execute_readonly_query, PostgresQueryError
    if backend == "snowflake":
        from snowflake_readonly import SnowflakeQueryError, execute_readonly_query
        return execute_readonly_query, SnowflakeQueryError
    if backend == "clickhouse":
        from clickhouse_readonly import ClickHouseQueryError, execute_readonly_query
        return execute_readonly_query, ClickHouseQueryError
    raise ValueError(f"unknown SQL backend: {backend!r}")


class ExecuteSQLRequest(BaseModel):
    sql: str
    connection: dict[str, Any]

    @field_validator("connection")
    @classmethod
    def _validate_connection(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Resolve `type` (defaulting to SANDBOX_SQL_BACKEND) and validate the
        rest of the payload against that backend's own params model, so a
        malformed connection is still a 422 before anything is queued — the
        property the pinned-model version had, kept while allowing several
        shapes. The resolved backend is stored back under "type" so
        _run_execute_sql doesn't have to re-derive it.
        """
        backend = str(value.get("type") or _SQL_BACKEND).lower()
        params_model = _CONNECTION_PARAMS.get(backend)
        if params_model is None:
            raise ValueError(
                f"unknown connection type {backend!r} (expected one of {sorted(_CONNECTION_PARAMS)})"
            )
        params = params_model.model_validate({k: v for k, v in value.items() if k != "type"})
        return {"type": backend, **params.model_dump(by_alias=True)}


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
    command = payload["command"]
    job_id = payload["job_id"]

    # Audit copy of the submitted command, same lifecycle as execute_python's
    # copy of the submitted code (backend/services/sandbox-executor/ops/ mục h): uploaded before dispatch,
    # always removed once the request ends.
    minio_key = f"shell/{job_id}.sh"
    await asyncio.to_thread(minio_store.put_code, minio_key, command)
    assert python_queue is not None
    try:
        result = await asyncio.to_thread(
            run_shell_sandboxed, command, payload["timeout_s"],
            on_output=lambda stream, text: python_queue.append_output(job_id, stream, text),
            workdir=payload.get("workdir"),
        )
    except ExecutionTimeoutError as e:
        return {"success": False, "error": str(e), "timed_out": True}
    except SandboxRunError as e:
        logger.error("execute_shell | sandbox run failed: %s", e)
        return {"success": False, "error": str(e)}
    # SandboxCircuitOpenError propagates, exactly as in _run_execute_python.
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


async def _run_execute_python(payload: dict[str, Any]) -> dict[str, Any]:
    code = payload["code"]
    job_id = payload["job_id"]

    try:
        validate_python_code(code)
    except SecurityError as e:
        logger.warning("execute_python | rejected by AST validation: %s", e)
        return {"success": False, "error": f"security validation failed: {e}"}
    except SyntaxError as e:
        return {"success": False, "error": f"syntax error: {e}"}

    # backend/services/sandbox-executor/ops/ mục h: audit copy of the submitted code, uploaded before
    # dispatch and always removed once the request ends (finally below),
    # kept/kept-deleted regardless of success/error/timeout — same "always
    # runs" branch as the container's own kill+wipe (mục c).
    minio_key = f"python/{job_id}.py"
    await asyncio.to_thread(minio_store.put_code, minio_key, code)
    # _run_execute_python only ever runs as python_queue's own worker-loop
    # executor (wired up in lifespan()), which starts only after
    # python_queue has already been assigned — never None by this point.
    assert python_queue is not None
    try:
        result = await asyncio.to_thread(
            run_python_sandboxed, code, payload["timeout_s"], payload["memory_mb"], payload["cpus"],
            on_output=lambda stream, text: python_queue.append_output(job_id, stream, text),
            files={
                name: base64.b64decode(encoded)
                for name, encoded in (payload.get("files") or {}).items()
            },
        )
    except ExecutionTimeoutError as e:
        return {"success": False, "error": str(e), "timed_out": True}
    except SandboxRunError as e:
        logger.error("execute_python | sandbox run failed: %s", e)
        return {"success": False, "error": str(e)}
    # SandboxCircuitOpenError is deliberately NOT caught here — it
    # propagates to JobQueue's worker loop, which maps it to
    # JobStatus.REJECTED; the GET endpoint below turns that into an actual
    # 503 (backend/services/sandbox-executor/ops/ mục e: "TỪ CHỐI NGAY (503)").
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


async def _run_execute_sql(payload: dict[str, Any]) -> dict[str, Any]:
    sql = payload["sql"]
    job_id = payload["job_id"]
    connection = dict(payload["connection"])
    backend = connection.pop("type")

    # Guard here as well as inside each backend. The backends keep their own
    # call (defence in depth, and they are importable/testable on their own),
    # but running it at the dispatcher is what lets the response distinguish
    # "the model wrote a non-read query" from "the query failed to run".
    # Callers use that to decide whether a retry is even appropriate: a guard
    # rejection is a policy event, not a transient fault, so re-prompting the
    # model for it just burns tokens and blurs the audit signal.
    try:
        # The backend name IS the dialect (sql_scan.DIALECTS uses the same
        # spellings). Passing it matters: the engines disagree about whether a
        # backslash escapes a quote inside a string literal, and therefore about
        # where a statement ends — see sql_scan's module docstring.
        validate_select_only(sql, backend)
    except SQLGuardError as e:
        logger.warning("execute_sql | rejected by sql_guard (backend=%s): %s", backend, e)
        return {"success": False, "error": f"SQL rejected: {e}", "rejected_by_guard": True}

    try:
        execute_readonly_query, backend_error = _load_backend(backend)
    except Exception as e:  # unknown backend slipped past validation, or driver import blew up
        return {"success": False, "error": f"SQL backend {backend!r} unavailable: {e}"}

    minio_key = f"sql/{job_id}.sql"
    await asyncio.to_thread(minio_store.put_code, minio_key, sql)
    try:
        result = await execute_readonly_query(sql, connection)
    except backend_error as e:
        return {"success": False, "error": str(e)}
    finally:
        await asyncio.to_thread(minio_store.delete_code, minio_key)

    return {
        "success": True,
        "rows": result["rows"],
        "row_count": result["row_count"],
        "is_truncated": result["is_truncated"],
        # WHY it was cut, not just that it was. "the first 10,000 rows" and
        # "as much as fitted in 8MB" support different answers, and a caller
        # that cannot tell them apart cannot explain either to a user.
        "truncation_reason": result.get("truncation_reason", ""),
        "cells_truncated": result.get("cells_truncated", 0),
        "result_bytes": result.get("result_bytes", 0),
        "backend": backend,
    }


# -- HTTP surface ------------------------------------------------------------

def _json_default(obj: Any) -> Any:
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
    def render(self, content: Any) -> bytes:
        return json.dumps(content, ensure_ascii=False, default=_json_default).encode("utf-8")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def _job_summary(job: Job, kind: str) -> dict[str, Any]:
    """Lightweight view for the ops dashboard's job list (backend/services/sandbox-executor/ops/ mục
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
async def get_status() -> dict[str, Any]:
    """Ops dashboard endpoint (backend/services/sandbox-executor/ops/ mục h) — pool health (idle/busy/
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


def _sse_line(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, default=_json_default)}\n\n"


async def _sse_events(queue: JobQueue, job_id: str) -> AsyncIterator[str]:
    """backend/services/sandbox-executor/ops/ mục h: "stream trạng thái/stdout-stderr của lần exec
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
