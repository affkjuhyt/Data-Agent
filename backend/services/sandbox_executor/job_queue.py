"""In-memory job queue + store fronting /execute/python and /execute/sql
(backend/services/sandbox_executor/ops/ "Kế hoạch 1" mục i, "Hàng đợi (queue) trước API cho Agent gọi
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
tool-call returns — see workflow_runtime's code_interpreter.py), no
webhook (polling only), and an in-memory, single-process job store (no
Postgres/Redis) — meaning jobs and their results do not survive a
sandbox_executor restart, and this service must run as exactly one
instance until that's revisited (see doc's own risk note for this mục).
"""
from __future__ import annotations

from typing import Any
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
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: float = dataclasses.field(default_factory=time.monotonic)
    finished_at: float | None = None
    # backend/services/sandbox_executor/ops/ Kế hoạch 1 mục h: incremental (stream, text) chunks
    # appended as the executor produces them — e.g. docker_runner.py's
    # on_output callback, invoked per stdout/stderr chunk while the
    # container exec is still running, not just once at the end. Appended
    # to (never mutated in place) from whatever thread the executor
    # happens to run output on; readers (main.py's SSE generator) only
    # ever slice `output_chunks[n:]`, which is safe under the GIL without
    # an explicit lock for this single-writer/many-reader access pattern.
    output_chunks: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    # Set whenever status changes or a new output chunk is appended, so
    # wait_for_change() below can block on it instead of busy-polling —
    # cleared again after each waiter wakes and re-checks its condition.
    change_event: asyncio.Event = dataclasses.field(default_factory=asyncio.Event)


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
        executor: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
        num_workers: int,
        rejection_exc: type[Exception] | tuple[type[Exception], ...] = (),
        job_ttl_s: float = 600.0,
        maxsize: int = 200,
    ):
        self._executor = executor
        self._num_workers = num_workers
        self._rejection_exc = rejection_exc
        self._job_ttl_s = job_ttl_s
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=maxsize)
        self._jobs: dict[str, Job] = {}
        self._payloads: dict[str, dict[str, Any]] = {}
        self._workers: list[asyncio.Task[Any]] = []
        # Captured in start() (runs on the event loop) so append_output —
        # called from a worker OS thread via asyncio.to_thread, not the
        # event loop — can wake waiters with the thread-safe call_soon_
        # threadsafe instead of calling Event.set() directly off-thread.
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self) -> None:
        self._loop = asyncio.get_event_loop()
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

    def submit(self, payload: dict[str, Any]) -> str:
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
                f"sandbox_executor job queue is full ({self._queue.maxsize} pending) — try again shortly"
            ) from e
        return job_id

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def payload_of(self, job_id: str) -> dict[str, Any]:
        """The submitted payload, for callers that need to know WHAT a job is
        rather than how it is doing — main.py's /status labels each job with its
        kind, and python and shell jobs share one queue (one container pool, one
        concurrency budget). Empty once the job has been swept."""
        return self._payloads.get(job_id, {})

    def list_jobs(self) -> list[Job]:
        """Snapshot for an ops dashboard (backend/services/sandbox_executor/ops/ mục h — "vận hành nhìn
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
            if self._loop is not None:
                self._loop.call_soon_threadsafe(job.change_event.set)

    async def wait_for_change(
        self,
        job_id: str,
        last_status: JobStatus | None,
        timeout_s: float,
        last_output_len: int = 0,
    ) -> Job | None:
        """Waits on the job's own asyncio.Event for a status change OR new
        output chunks, used by the SSE stream endpoint — no fixed-interval
        busy-poll, so many concurrent SSE connections cost nothing while
        idle. Each waiter clears the event right after waking (safe: no
        `await` between wake and clear, so no other coroutine can slip a
        change in between) and re-checks its own condition, since one
        event covers both status and output-chunk changes."""
        deadline = time.monotonic() + timeout_s
        while True:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status != last_status or len(job.output_chunks) != last_output_len:
                return job
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return job
            try:
                await asyncio.wait_for(job.change_event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                return self._jobs.get(job_id)
            job.change_event.clear()

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
            assert payload is not None  # submit() always inserts payload alongside the job
            job.status = JobStatus.RUNNING
            job.change_event.set()
            try:
                job.result = await self._executor(payload)
                job.status = JobStatus.DONE
            except self._rejection_exc as e:
                job.error = str(e)
                job.status = JobStatus.REJECTED
            except Exception as e:
                logger.exception("sandbox_executor | job %s: executor raised unexpectedly", job_id)
                job.error = str(e)
                job.status = JobStatus.ERROR
            job.finished_at = time.monotonic()
            job.change_event.set()
