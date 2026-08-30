"""Unit tests for job_queue.py (docs/1.md "Kế hoạch 1" mục i) — pure
asyncio, no Docker/network involved. main.py wires JobQueue's executor
callable to docker_runner.run_python_sandboxed / snowflake_readonly's
query function; these tests stand in fake executors to isolate the
queue's own contract: submit returns immediately, a worker eventually
picks the job up, handled failures/unexpected exceptions/the
circuit-breaker-rejection exception type map to the right terminal
JobStatus, job_id is available to the executor, and the queue itself
applies bounded backpressure.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "sandbox_executor")


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    sys.modules.pop("job_queue", None)
    yield
    sys.modules.pop("job_queue", None)
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


class Boom(Exception):
    pass


class CircuitOpen(Exception):
    pass


async def _wait_terminal(queue, job_id, max_wait_s=5.0):
    import job_queue as jq
    deadline = asyncio.get_event_loop().time() + max_wait_s
    while True:
        job = queue.get(job_id)
        assert job is not None
        if job.status in jq.TERMINAL_STATUSES:
            return job
        assert asyncio.get_event_loop().time() < deadline, f"job {job_id} never reached a terminal status"
        await asyncio.sleep(0.01)


@pytest.mark.unit
class TestJobQueue:
    async def test_submit_returns_job_id_immediately_in_queued_state(self):
        import job_queue as jq

        async def slow_executor(payload):
            await asyncio.sleep(1)
            return {"success": True}

        q = jq.JobQueue(slow_executor, num_workers=1)
        q.start()
        try:
            job_id = q.submit({})
            job = q.get(job_id)
            assert job is not None
            assert job.status in (jq.JobStatus.QUEUED, jq.JobStatus.RUNNING)
        finally:
            await q.stop()

    async def test_successful_job_reaches_done_with_result(self):
        import job_queue as jq

        async def executor(payload):
            return {"success": True, "echo": payload.get("x")}

        q = jq.JobQueue(executor, num_workers=2)
        q.start()
        try:
            job_id = q.submit({"x": 42})
            job = await _wait_terminal(q, job_id)
            assert job.status == jq.JobStatus.DONE
            assert job.result == {"success": True, "echo": 42}
        finally:
            await q.stop()

    async def test_job_id_is_injected_into_payload_for_the_executor(self):
        import job_queue as jq
        seen = {}

        async def executor(payload):
            seen["job_id"] = payload["job_id"]
            return {"success": True}

        q = jq.JobQueue(executor, num_workers=1)
        q.start()
        try:
            job_id = q.submit({"unrelated": "field"})
            await _wait_terminal(q, job_id)
            assert seen["job_id"] == job_id
        finally:
            await q.stop()

    async def test_unexpected_exception_maps_to_error_status(self):
        import job_queue as jq

        async def executor(payload):
            raise Boom("bug")

        q = jq.JobQueue(executor, num_workers=1)
        q.start()
        try:
            job_id = q.submit({})
            job = await _wait_terminal(q, job_id)
            assert job.status == jq.JobStatus.ERROR
            assert "bug" in job.error
        finally:
            await q.stop()

    async def test_rejection_exc_maps_to_rejected_status(self):
        """Mirrors main.py wiring SandboxCircuitOpenError as the
        rejection_exc for the python queue — a distinct terminal status
        from an ordinary handled failure or an unexpected bug, so the
        HTTP layer can map it to 503 instead of 200/500."""
        import job_queue as jq

        async def executor(payload):
            raise CircuitOpen("breaker is open")

        q = jq.JobQueue(executor, num_workers=1, rejection_exc=CircuitOpen)
        q.start()
        try:
            job_id = q.submit({})
            job = await _wait_terminal(q, job_id)
            assert job.status == jq.JobStatus.REJECTED
            assert "breaker is open" in job.error
        finally:
            await q.stop()

    async def test_handled_failure_dict_still_lands_as_done(self):
        """A normalized {"success": False, ...} return (validation error,
        timeout, query failure — all handled inside the executor, same as
        main.py's _run_execute_python/_run_execute_sql) is a completed
        job, not an error — unchanged from the pre-queue behavior."""
        import job_queue as jq

        async def executor(payload):
            return {"success": False, "error": "bad code", "timed_out": True}

        q = jq.JobQueue(executor, num_workers=1)
        q.start()
        try:
            job_id = q.submit({})
            job = await _wait_terminal(q, job_id)
            assert job.status == jq.JobStatus.DONE
            assert job.result["success"] is False
        finally:
            await q.stop()

    async def test_unknown_job_id_returns_none(self):
        import job_queue as jq

        async def executor(payload):
            return {}

        q = jq.JobQueue(executor, num_workers=1)
        assert q.get("does-not-exist") is None

    async def test_list_jobs_returns_most_recent_first(self):
        """Backs the ops-dashboard status endpoint (docs/1.md mục h)."""
        import job_queue as jq

        async def executor(payload):
            return {"success": True}

        q = jq.JobQueue(executor, num_workers=1)
        q.start()
        try:
            first_id = q.submit({})
            await _wait_terminal(q, first_id)
            second_id = q.submit({})
            await _wait_terminal(q, second_id)

            listed = q.list_jobs()
            ids = [j.id for j in listed]
            assert ids == [second_id, first_id]
        finally:
            await q.stop()

    async def test_queue_full_raises_immediately_without_blocking(self):
        import job_queue as jq

        blocker = asyncio.Event()

        async def executor(payload):
            await blocker.wait()
            return {"success": True}

        # No workers running at all — nothing will ever drain the queue,
        # so maxsize=1 fills on the second submit deterministically.
        q = jq.JobQueue(executor, num_workers=0, maxsize=1)
        q.submit({})
        with pytest.raises(jq.QueueFullError):
            q.submit({})
        blocker.set()

    async def test_wait_for_change_returns_promptly_on_status_change(self):
        import job_queue as jq

        async def executor(payload):
            await asyncio.sleep(0.05)
            return {"success": True}

        q = jq.JobQueue(executor, num_workers=1)
        q.start()
        try:
            job_id = q.submit({})
            # q.get() returns a live reference into the job store, not a
            # snapshot — capture the enum *value* now (immutable) rather
            # than keeping the Job object itself, which the worker will
            # go on to mutate in place as it runs.
            last_status = q.get(job_id).status
            updated = await q.wait_for_change(job_id, last_status, timeout_s=5.0)
            assert updated is not None
            assert updated.status != last_status
        finally:
            await q.stop()

    async def test_wait_for_change_times_out_returning_current_job(self):
        import job_queue as jq

        async def executor(payload):
            await asyncio.sleep(10)
            return {"success": True}

        q = jq.JobQueue(executor, num_workers=1)
        q.start()
        try:
            job_id = q.submit({})
            job = q.get(job_id)
            updated = await q.wait_for_change(job_id, job.status, timeout_s=0.1)
            assert updated is not None  # still returns the (unchanged) job, not None
        finally:
            await q.stop()
