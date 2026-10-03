"""Crawler worker dispatch, pool limits, and outbox publish behaviour."""
from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest

from libs.basemodel.services_databaseconnector.postgres_orm.outbox_orm import OutboxEventORM
from services.platform_workers.workers.web_crawler import worker as wc

pytestmark = pytest.mark.unit

RUN_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
SOURCE_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
TENANT = "a0000000-0000-0000-0000-000000000001"


@pytest.fixture(autouse=True)
def _one_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    """The scans walk the tenants under row-level security (common/db); the fake
    session has one, and scoping it is the integration tests' business."""
    monkeypatch.setattr(wc.worker_db, "tenant_ids", lambda _session: [TENANT])
    monkeypatch.setattr(wc.worker_db, "set_tenant", lambda _session, _tenant: None)


class _Result:
    def __init__(self, rows: list[Any] | None = None, rowcount: int = 1) -> None:
        self._rows = rows or []
        self.rowcount = rowcount

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    def __init__(self, queued: list[tuple[Any, Any, int]] | None = None, *, claim: bool = True) -> None:
        self.queued = queued or []
        self.claim = claim
        self.added: list[Any] = []
        self.commits = 0
        self.updates: list[Any] = []
        self.run = type("Run", (), {"status": "queued", "error": None, "finished_at": None})()

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def execute(self, stmt: Any) -> _Result:
        if getattr(stmt, "is_select", False):
            return _Result(self.queued)
        self.updates.append(stmt)
        return _Result(rowcount=1 if self.claim else 0)

    def get(self, _model: Any, _key: Any) -> Any:
        return self.run

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.commits += 1


class _SessionFactory:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session

    def __call__(self) -> _FakeSession:
        return self.session


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


def test_queued_run_is_claimed_then_submitted() -> None:
    session = _FakeSession(queued=[(RUN_ID, SOURCE_ID, 0)])
    pool = _FakePool()
    with patch.object(wc, "get_active_sources", return_value=[{"source_id": str(SOURCE_ID)}]):
        wc.dispatch_queued_runs(_SessionFactory(session), pool)  # type: ignore[arg-type]
    assert session.updates
    assert pool.submitted == [(str(SOURCE_ID), "manual", str(RUN_ID))]


def test_run_for_a_busy_source_stays_queued() -> None:
    session = _FakeSession(queued=[(RUN_ID, SOURCE_ID, 0)])
    pool = _FakePool(running={str(SOURCE_ID)})
    with patch.object(wc, "get_active_sources", return_value=[{"source_id": str(SOURCE_ID)}]):
        wc.dispatch_queued_runs(_SessionFactory(session), pool)  # type: ignore[arg-type]
    assert session.updates == []
    assert pool.submitted == []


def test_run_for_a_deleted_source_is_failed_not_left_queued() -> None:
    session = _FakeSession(queued=[(RUN_ID, SOURCE_ID, 0)])
    pool = _FakePool()
    with patch.object(wc, "get_active_sources", return_value=[]):
        wc.dispatch_queued_runs(_SessionFactory(session), pool)  # type: ignore[arg-type]
    assert session.run.status == "failed"
    assert session.run.error == "source not found or inactive"
    assert pool.submitted == []


def test_nothing_is_claimed_when_the_pool_is_full() -> None:
    session = _FakeSession(queued=[(RUN_ID, SOURCE_ID, 0)])
    pool = _FakePool(slots=0)
    wc.dispatch_queued_runs(_SessionFactory(session), pool)  # type: ignore[arg-type]
    assert session.updates == []


def test_pool_runs_up_to_its_limit_and_never_the_same_source_twice() -> None:
    release = threading.Event()
    started: list[str] = []

    def _slow_run(_factory: Any, source: dict[str, Any], *_: Any, **__: Any) -> None:
        started.append(str(source["source_id"]))
        release.wait(5)

    with patch.object(wc, "run_source", _slow_run):
        pool = wc.RunPool(_SessionFactory(_FakeSession()), max_workers=2)  # type: ignore[arg-type]
        assert pool.submit({"source_id": "a"}, "manual")
        assert not pool.submit({"source_id": "a"}, "manual")
        assert pool.submit({"source_id": "b"}, "manual")
        assert not pool.submit({"source_id": "c"}, "manual")
        assert pool.free_slots() == 0 and pool.is_running("a")
        release.set()
        pool._executor.shutdown(wait=True)
        assert sorted(started) == ["a", "b"]
        assert pool.free_slots() == 2 and not pool.is_running("a")


def test_a_crash_outside_run_source_fails_the_claimed_run_and_frees_the_slot() -> None:
    session = _FakeSession()

    def _boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("db went away")

    with patch.object(wc, "run_source", _boom):
        pool = wc.RunPool(_SessionFactory(session), max_workers=1)  # type: ignore[arg-type]
        assert pool.submit({"source_id": "a", "tenant_id": TENANT}, "manual", run_id=str(RUN_ID))
        pool._executor.shutdown(wait=True)
    assert session.run.status == "failed"
    assert pool.free_slots() == 1


def test_scheduled_run_skips_a_source_already_queued_or_running() -> None:
    session, pool = _FakeSession(), _FakePool()
    latest = {"s1": {"started_at": None, "status": "running", "retry_count": 0, "next_retry_at": None}}
    with (
        patch.object(wc, "get_active_sources", return_value=[{"source_id": "s1", "config": {"cron": "* * * * *"}}]),
        patch.object(wc, "_latest_run_by_source", return_value=latest),
    ):
        wc.crawl_due_sources(_SessionFactory(session), pool)  # type: ignore[arg-type]
    assert pool.submitted == []


def test_a_scheduled_source_that_never_ran_waits_for_its_first_manual_run() -> None:
    session, pool = _FakeSession(), _FakePool()
    with (
        patch.object(wc, "get_active_sources", return_value=[{"source_id": "s1", "config": {"cron": "* * * * *"}}]),
        patch.object(wc, "_latest_run_by_source", return_value={}),
    ):
        wc.crawl_due_sources(_SessionFactory(session), pool)  # type: ignore[arg-type]
    assert pool.submitted == []


def test_after_its_first_run_a_scheduled_source_runs_when_the_cron_is_due() -> None:
    session, pool = _FakeSession(), _FakePool()
    long_ago = datetime.now(UTC) - timedelta(days=2)
    latest = {"s1": {"started_at": long_ago, "status": "success", "retry_count": 0, "next_retry_at": None}}
    with (
        patch.object(wc, "get_active_sources", return_value=[{"source_id": "s1", "config": {"cron": "0 3 * * *"}}]),
        patch.object(wc, "_latest_run_by_source", return_value=latest),
    ):
        wc.crawl_due_sources(_SessionFactory(session), pool)  # type: ignore[arg-type]
    assert pool.submitted == [("s1", "scheduled", None)]


def test_queue_auto_promote_silver_writes_the_event_into_the_callers_transaction() -> None:
    session = _FakeSession()
    wc.queue_auto_promote_silver(session, "d1", "t1")  # type: ignore[arg-type]
    [event] = session.added
    assert isinstance(event, OutboxEventORM)
    assert (event.producer, event.topic, event.event_key) == ("web-crawler", wc._PROMOTE_SILVER_TOPIC, "d1")
    assert event.payload == {
        "event": "promote_silver", "data_id": "d1", "tenant_id": "t1",
        "approved_by": "web-crawler-worker",
    }


class _FakeFuture:
    def __init__(self, exc: Exception | None = None) -> None:
        self._exc = exc

    def get(self, timeout: float | None = None) -> Any:
        if self._exc is not None:
            raise self._exc
        return object()


class _FakeAutoPromoteProducer:
    def __init__(self, send_exc: Exception | None = None) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self._send_exc = send_exc

    def send(self, topic: str, value: dict[str, Any], key: bytes | None = None) -> _FakeFuture:
        self.sent.append((topic, value))
        return _FakeFuture(self._send_exc)

    def flush(self, timeout: float | None = None) -> None:
        return None


def test_the_relay_sends_the_expected_payload() -> None:
    producer = _FakeAutoPromoteProducer()
    with patch.object(wc, "_get_promote_producer", return_value=producer):
        wc._publish_outbox_event(wc._PROMOTE_SILVER_TOPIC, {"data_id": "d1"}, "d1")
    assert producer.sent == [(wc._PROMOTE_SILVER_TOPIC, {"data_id": "d1"})]


def test_the_relay_raises_when_kafka_is_unavailable_so_the_event_stays_queued() -> None:
    with patch.object(wc, "_get_promote_producer", return_value=None), pytest.raises(RuntimeError):
        wc._publish_outbox_event(wc._PROMOTE_SILVER_TOPIC, {}, "d1")


def test_the_relay_raises_on_a_failure_the_future_reports() -> None:
    producer = _FakeAutoPromoteProducer(send_exc=RuntimeError("stale broker connection"))
    with patch.object(wc, "_get_promote_producer", return_value=producer), pytest.raises(RuntimeError):
        wc._publish_outbox_event(wc._PROMOTE_SILVER_TOPIC, {}, "d1")


def test_a_failed_publish_drops_the_cached_producer_so_the_next_call_rebuilds_it() -> None:
    wc._promote_producer = _FakeAutoPromoteProducer(send_exc=RuntimeError("stale"))  # type: ignore[assignment]
    try:
        with pytest.raises(RuntimeError):
            wc._publish_outbox_event(wc._PROMOTE_SILVER_TOPIC, {}, "d1")
        assert wc._promote_producer is None
    finally:
        wc._promote_producer = None
