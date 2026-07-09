"""In-memory job queue + store fronting /execute/python and /execute/sql
(docs/1.md "Kế hoạch 1" mục i, "Hàng đợi (queue) trước API cho Agent gọi
vào").

Before this module existed, both endpoints were called directly and
synchronously — one HTTP connection held open for the full
acquire+exec/query duration, with no smoothing between a load spike and
the container pool / Snowflake connection pool underneath. `JobQueue`
inserts backpressure ahead of that: `submit()` enqueues a bounded amount
of work and returns a job_id immediately; a small fixed pool of worker
coroutines pulls jobs at the rate the backing resource can actually serve
(container pool size for python, a separate concurrency cap for sql),
instead of firing off one concurrent acquire()/query per incoming
request.

Per the doc's explicit decisions for v1: synchronous contract preserved
one layer up (the caller still polls to completion before its own
tool-call returns — see workflow-runtime's code_interpreter.py), no
webhook (polling only), and an in-memory, single-process job store (no
Postgres/Redis) — meaning jobs and their results do not survive a
sandbox-executor restart, and this service must run as exactly one
instance until that's revisited (see doc's own risk note for this mục).
"""
from __future__ import annotations

import asyncio
import dataclasses
import enum
import logging
import time
import uuid
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    # A timed-out execution is a *handled* outcome, not an exception —
    # docker_runner.run_python_sandboxed's caller in main.py already turns
    # ExecutionTimeoutError into a normal {"success": False, "timed_out":
    # True} result dict (unchanged from before this queue existed), so it
    # lands here as DONE with that shape, same as any other handled
    # failure (validation error, SandboxRunError, SnowflakeQueryError).
    DONE = "done"
    REJECTED = "rejected"  # circuit breaker was open when a worker picked this job up
    ERROR = "error"  # the executor itself raised unexpectedly (a bug, not a handled failure)


TERMINAL_STATUSES = frozenset({JobStatus.DONE, JobStatus.REJECTED, JobStatus.ERROR})


class QueueFullError(Exception):
    """Raised by submit() when the queue is already at its configured
    maxsize — the natural backpressure signal the doc describes ("tạo
    backpressure tự nhiên khớp với kích thước pool"), surfaced as an
    immediate 503 rather than accepting unbounded queued work."""


@dataclasses.dataclass
class Job:
    id: str
    status: JobStatus = JobStatus.QUEUED
    result: dict | None = None
    error: str | None = None
    created_at: float = dataclasses.field(default_factory=time.monotonic)
    finished_at: float | None = None
    # docs/1.md Kế hoạch 1 mục h: incremental (stream, text) chunks
    # appended as the executor produces them — e.g. docker_runner.py's
    # on_output callback, invoked per stdout/stderr chunk while the
    # container exec is still running, not just once at the end. Appended
    # to (never mutated in place) from whatever thread the executor
    # happens to run output on; readers (main.py's SSE generator) only
    # ever slice `output_chunks[n:]`, which is safe under the GIL without
    # an explicit lock for this single-writer/many-reader access pattern.
    output_chunks: list[tuple[str, str]] = dataclasses.field(default_factory=list)


class JobQueue:
    """One instance per job "kind" (python vs sql) so a burst of one kind
    can never starve the other's workers.

    `executor` is an async callable `payload -> result dict`. It is
    expected to catch and normalize its own handled failures into the
    result dict (e.g. `{"success": False, "error": ...}`) the same way
    main.py's request handlers already did before this queue existed —
    those still land as JobStatus.DONE, unchanged from today's behavior.
    Only two things are treated specially at this layer:
      - an instance of `rejection_exc` (the pool's circuit-breaker-open
        error) -> JobStatus.REJECTED, mapped by main.py to an actual
        HTTP 503 on the polling GET so callers stop and back off instead
        of treating it as an ordinary tool failure.
      - any other exception escaping `executor` -> JobStatus.ERROR (a bug,
        not a handled failure).
    """

    def __init__(
        self,
        executor: Callable[[dict], Awaitable[dict]],
        num_workers: int,
        rejection_exc: type[Exception] = (),
        job_ttl_s: float = 600.0,
        maxsize: int = 200,
    ):
        self._executor = executor
        self._num_workers = num_workers
        self._rejection_exc = rejection_exc
        self._job_ttl_s = job_ttl_s
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=maxsize)
        self._jobs: dict[str, Job] = {}
        self._payloads: dict[str, dict] = {}
        self._workers: list[asyncio.Task] = []

    def start(self) -> None:
        self._workers = [
            asyncio.create_task(self._worker_loop(), name=f"sandbox-job-worker-{i}")
            for i in range(self._num_workers)
        ]

    async def stop(self) -> None:
        for task in self._workers:
            task.cancel()
        for task in self._workers:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._workers = []

    def submit(self, payload: dict) -> str:
        """Returns the generated job_id. The executor receives `payload`
        with `job_id` merged in (so it can key e.g. a MinIO audit-copy
        object by the same id the caller polls for), without every caller
        of submit() having to generate and thread an id through itself."""
        self._sweep_expired()
        job_id = uuid.uuid4().hex
        self._jobs[job_id] = Job(id=job_id)
        self._payloads[job_id] = {**payload, "job_id": job_id}
        try:
            self._queue.put_nowait(job_id)
        except asyncio.QueueFull as e:
            del self._jobs[job_id]
            del self._payloads[job_id]
            raise QueueFullError(
                f"sandbox-executor job queue is full ({self._queue.maxsize} pending) — try again shortly"
            ) from e
        return job_id

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[Job]:
        """Snapshot for an ops dashboard (docs/1.md mục h — "vận hành nhìn
        thấy job đang chạy gì") — most-recently-created first. Same
        no-lock reasoning as get()/wait_for_change(): everything here runs
        on the event loop thread, dict/list reads are safe without one."""
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def append_output(self, job_id: str, stream: str, text: str) -> None:
        """Called from docker_runner.run_python_sandboxed's on_output
        callback — i.e. from a plain OS thread (asyncio.to_thread's worker
        thread), NOT the event loop. Must stay a plain, synchronous list
        append for exactly that reason (see Job.output_chunks' docstring
        for why that's safe without a lock here)."""
        job = self._jobs.get(job_id)
        if job is not None:
            job.output_chunks.append((stream, text))

    async def wait_for_change(
        self,
        job_id: str,
        last_status: JobStatus | None,
        timeout_s: float,
        last_output_len: int = 0,
    ) -> Job | None:
        """Polls the in-memory store for a status change OR new output
        chunks, used by the SSE stream endpoint. Not condition-variable-
        based — the job store is a plain dict with no per-job signal to
        wait on, and this is v1's deliberately simple in-memory design
        (see module docstring); a short internal poll interval is cheap
        here since it never leaves the process."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status != last_status or len(job.output_chunks) != last_output_len:
                return job
            await asyncio.sleep(0.1)
        return self._jobs.get(job_id)

    def _sweep_expired(self) -> None:
        now = time.monotonic()
        expired = [
            jid
            for jid, job in self._jobs.items()
            if job.finished_at is not None and now - job.finished_at > self._job_ttl_s
        ]
        for jid in expired:
            self._jobs.pop(jid, None)
            self._payloads.pop(jid, None)

    async def _worker_loop(self) -> None:
        while True:
            job_id = await self._queue.get()
            job = self._jobs.get(job_id)
            payload = self._payloads.pop(job_id, None)
            if job is None:
                continue
            job.status = JobStatus.RUNNING
            try:
                job.result = await self._executor(payload)
                job.status = JobStatus.DONE
            except self._rejection_exc as e:
                job.error = str(e)
                job.status = JobStatus.REJECTED
            except Exception as e:
                logger.exception("sandbox-executor | job %s: executor raised unexpectedly", job_id)
                job.error = str(e)
                job.status = JobStatus.ERROR
            job.finished_at = time.monotonic()
