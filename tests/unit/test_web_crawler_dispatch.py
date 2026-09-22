"""Crawler worker: queued runs, the parallel pool, and never running one source twice.

Run Now used to publish a Kafka message the worker ran inline, one source at a
time. It now records a `queued` KBSourceRun row that the worker claims into a
small thread pool.
"""
from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest

from services.platform_workers.workers.web_crawler import worker as wc

pytestmark = pytest.mark.unit


class _Cursor:
    def __init__(self, conn: "_Conn") -> None:
        self._conn = conn
        self.rowcount = 1

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        self._conn.executed.append((" ".join(sql.split()), params))
        self._last_select = sql.lstrip().upper().startswith("SELECT")

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._conn.queued if self._last_select else []


class _Conn:
    def __init__(self, queued: list[tuple[Any, ...]] | None = None) -> None:
        self.queued = queued or []
        self.executed: list[tuple[str, Any]] = []
        self.commits = 0
        self.closed = False

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        self.closed = True


class _FakePool:
    def __init__(self, slots: int = 3, running: set[str] | None = None) -> None:
        self.slots = slots
        self.running = running or set()
        self.submitted: list[tuple[str, str, str | None]] = []

    def free_slots(self) -> int:
        return self.slots

    def is_running(self, source_id: str) -> bool:
        return source_id in self.running

    def submit(self, source: dict[str, Any], trigger: str, retry_count: int = 0, run_id: str | None = None) -> bool:
        self.submitted.append((str(source["source_id"]), trigger, run_id))
        return True


def _sql(conn: _Conn, needle: str) -> list[tuple[str, Any]]:
    return [e for e in conn.executed if needle in e[0]]


def test_queued_run_is_claimed_then_submitted() -> None:
    conn, pool = _Conn(queued=[("r1", "s1", 0)]), _FakePool()
    with patch.object(wc, "get_active_sources", return_value=[{"source_id": "s1"}]):
        wc.dispatch_queued_runs(conn, pool)
    assert _sql(conn, "SET status = 'running'")
    assert pool.submitted == [("s1", "manual", "r1")]


def test_run_for_a_busy_source_stays_queued() -> None:
    conn, pool = _Conn(queued=[("r1", "s1", 0)]), _FakePool(running={"s1"})
    with patch.object(wc, "get_active_sources", return_value=[{"source_id": "s1"}]):
        wc.dispatch_queued_runs(conn, pool)
    assert not _sql(conn, "SET status = 'running'")
    assert pool.submitted == []


def test_run_for_a_deleted_source_is_failed_not_left_queued() -> None:
    conn, pool = _Conn(queued=[("r1", "gone", 0)]), _FakePool()
    with patch.object(wc, "get_active_sources", return_value=[]):
        wc.dispatch_queued_runs(conn, pool)
    failed = _sql(conn, "SET status = %s")
    assert failed and failed[0][1][0] == "failed"
    assert pool.submitted == []


def test_nothing_is_claimed_when_the_pool_is_full() -> None:
    conn, pool = _Conn(queued=[("r1", "s1", 0)]), _FakePool(slots=0)
    wc.dispatch_queued_runs(conn, pool)
    assert conn.executed == []


def test_pool_runs_up_to_its_limit_and_never_the_same_source_twice() -> None:
    release = threading.Event()
    started: list[str] = []

    def _slow_run(_conn: Any, source: dict[str, Any], *_: Any, **__: Any) -> None:
        started.append(str(source["source_id"]))
        release.wait(5)

    with patch.object(wc, "run_source", _slow_run), patch.object(wc.pg, "connect", lambda _s: _Conn()):
        pool = wc.RunPool(object(), max_workers=2)
        assert pool.submit({"source_id": "a"}, "manual")
        assert not pool.submit({"source_id": "a"}, "manual")  # already running
        assert pool.submit({"source_id": "b"}, "manual")
        assert not pool.submit({"source_id": "c"}, "manual")  # both slots taken
        assert pool.free_slots() == 0 and pool.is_running("a")
        release.set()
        pool._executor.shutdown(wait=True)
        assert sorted(started) == ["a", "b"]
        assert pool.free_slots() == 2 and not pool.is_running("a")


def test_a_crash_outside_run_source_fails_the_claimed_run_and_frees_the_slot() -> None:
    conn = _Conn()

    def _boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("db went away")

    with patch.object(wc, "run_source", _boom), patch.object(wc.pg, "connect", lambda _s: conn):
        pool = wc.RunPool(object(), max_workers=1)
        assert pool.submit({"source_id": "a"}, "manual", run_id="r9")
        pool._executor.shutdown(wait=True)
    assert _sql(conn, "SET status = %s") and conn.closed
    assert pool.free_slots() == 1


def test_scheduled_run_skips_a_source_already_queued_or_running() -> None:
    conn, pool = _Conn(), _FakePool()
    latest = {"s1": {"started_at": None, "status": "running", "retry_count": 0, "next_retry_at": None}}
    with (
        patch.object(wc, "get_active_sources", return_value=[{"source_id": "s1", "config": {"cron": "* * * * *"}}]),
        patch.object(wc, "_latest_run_by_source", return_value=latest),
    ):
        wc.crawl_due_sources(conn, pool)
    assert pool.submitted == []


def test_a_scheduled_source_that_never_ran_waits_for_its_first_manual_run() -> None:
    conn, pool = _Conn(), _FakePool()
    with (
        patch.object(wc, "get_active_sources", return_value=[{"source_id": "s1", "config": {"cron": "* * * * *"}}]),
        patch.object(wc, "_latest_run_by_source", return_value={}),
    ):
        wc.crawl_due_sources(conn, pool)
    assert pool.submitted == []


def test_after_its_first_run_a_scheduled_source_runs_when_the_cron_is_due() -> None:
    conn, pool = _Conn(), _FakePool()
    long_ago = datetime.now(UTC) - timedelta(days=2)
    latest = {"s1": {"started_at": long_ago, "status": "success", "retry_count": 0, "next_retry_at": None}}
    with (
        patch.object(wc, "get_active_sources", return_value=[{"source_id": "s1", "config": {"cron": "0 3 * * *"}}]),
        patch.object(wc, "_latest_run_by_source", return_value=latest),
    ):
        wc.crawl_due_sources(conn, pool)
    assert pool.submitted == [("s1", "scheduled", None)]


def test_legacy_trigger_becomes_a_queued_row_only_when_none_is_active() -> None:
    conn = _Conn()
    wc.enqueue_manual_run(conn, "s1")
    insert = _sql(conn, "INSERT INTO")
    assert insert and "'queued'" in insert[0][0] and "NOT EXISTS" in insert[0][0]
