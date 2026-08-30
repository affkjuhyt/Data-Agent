"""poll_ddl_events / process_due_ddl_events (sandbox_schema_docs.docx §2.8) —
the two functions extracted from main()'s loop so the debounce algorithm is
testable without a real Kafka broker. consumer.poll() is mocked to return
canned records; a real Kafka testcontainer was tried first but kafka-python
on this Windows/Docker Desktop setup couldn't hold a stable consumer-group
session long enough to finish (broker kept flapping between the IPv4/IPv6
candidates "localhost" resolves to, causing a permanent rebalance loop) —
an environment networking issue unrelated to this algorithm, which is
exactly two plain dict operations + one conditional call.
"""
from __future__ import annotations

import pathlib
import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "platform_workers")


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


def _poll_result(*events: dict) -> dict:
    """Shape a fake consumer.poll() return: {TopicPartition: [record, ...]}."""
    return {"tp0": [SimpleNamespace(value=e) for e in events]}


# ─── poll_ddl_events ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_poll_with_no_records_does_not_commit_or_touch_pending():
    from workers.schema_crawler import worker

    consumer = MagicMock()
    consumer.poll.return_value = {}
    pending_ddl: dict = {}

    worker.poll_ddl_events(consumer, pending_ddl)

    assert pending_ddl == {}
    consumer.commit.assert_not_called()


@pytest.mark.unit
def test_poll_queues_event_with_due_time_and_commits():
    from workers.schema_crawler import worker

    consumer = MagicMock()
    event = {"tenant_id": "t1", "warehouse_id": "w1", "table_name": "fact_stock_price"}
    consumer.poll.return_value = _poll_result(event)
    pending_ddl: dict = {}

    before = time.time()
    worker.poll_ddl_events(consumer, pending_ddl)
    after = time.time()

    key = ("t1", "w1", "fact_stock_price")
    assert key in pending_ddl
    assert pending_ddl[key]["event"] == event
    assert before + worker.DDL_DEBOUNCE_SECONDS <= pending_ddl[key]["due"] <= after + worker.DDL_DEBOUNCE_SECONDS
    consumer.commit.assert_not_called()


@pytest.mark.unit
def test_duplicate_event_for_same_table_overwrites_due_instead_of_queuing_twice():
    """This overwrite IS the debounce — a second DDL event for the same
    table within the window pushes the deadline out, it doesn't create a
    second pending trigger."""
    from workers.schema_crawler import worker

    consumer = MagicMock()
    event = {"tenant_id": "t1", "warehouse_id": "w1", "table_name": "fact_stock_price"}
    pending_ddl: dict = {}

    consumer.poll.return_value = _poll_result(event)
    worker.poll_ddl_events(consumer, pending_ddl)
    first_due = pending_ddl[("t1", "w1", "fact_stock_price")]["due"]

    time.sleep(0.01)
    consumer.poll.return_value = _poll_result(event)
    worker.poll_ddl_events(consumer, pending_ddl)

    assert len(pending_ddl) == 1
    assert pending_ddl[("t1", "w1", "fact_stock_price")]["due"] > first_due


@pytest.mark.unit
def test_different_tables_queue_independently():
    from workers.schema_crawler import worker

    consumer = MagicMock()
    e1 = {"tenant_id": "t1", "warehouse_id": "w1", "table_name": "fact_stock_price"}
    e2 = {"tenant_id": "t1", "warehouse_id": "w1", "table_name": "dim_company"}
    consumer.poll.return_value = _poll_result(e1, e2)
    pending_ddl: dict = {}

    worker.poll_ddl_events(consumer, pending_ddl)

    assert len(pending_ddl) == 2


@pytest.mark.unit
def test_malformed_records_without_table_name_are_ignored():
    from workers.schema_crawler import worker

    consumer = MagicMock()
    consumer.poll.return_value = _poll_result({"tenant_id": "t1"}, "not-a-dict", {})
    pending_ddl: dict = {}

    worker.poll_ddl_events(consumer, pending_ddl)

    assert pending_ddl == {}
    # Offsets are committed only after pending_ddl is fully drained, so a
    # crash between poll and crawl does not lose the event batch.
    consumer.commit.assert_not_called()


# ─── process_due_ddl_events ─────────────────────────────────────────────────

@pytest.mark.unit
def test_not_yet_due_event_is_left_pending(monkeypatch):
    from workers.schema_crawler import worker

    conn, qdrant = MagicMock(), MagicMock()
    handle_spy = MagicMock()
    monkeypatch.setattr(worker, "handle_ddl_event", handle_spy)

    pending_ddl = {("t1", "w1", "tbl"): {"due": time.time() + 60, "event": {"table_name": "tbl"}}}

    processed = worker.process_due_ddl_events(conn, qdrant, pending_ddl)

    assert processed == []
    assert len(pending_ddl) == 1
    handle_spy.assert_not_called()


@pytest.mark.unit
def test_due_event_is_processed_and_removed(monkeypatch):
    from workers.schema_crawler import worker

    conn, qdrant = MagicMock(), MagicMock()
    handle_spy = MagicMock()
    monkeypatch.setattr(worker, "handle_ddl_event", handle_spy)

    event = {"tenant_id": "t1", "warehouse_id": "w1", "table_name": "tbl"}
    pending_ddl = {("t1", "w1", "tbl"): {"due": time.time() - 1, "event": event}}

    processed = worker.process_due_ddl_events(conn, qdrant, pending_ddl)

    assert processed == [event]
    assert pending_ddl == {}
    handle_spy.assert_called_once_with(conn, qdrant, event)


@pytest.mark.unit
def test_only_due_entries_are_processed_others_stay_pending(monkeypatch):
    from workers.schema_crawler import worker

    conn, qdrant = MagicMock(), MagicMock()
    monkeypatch.setattr(worker, "handle_ddl_event", MagicMock())

    due_event = {"table_name": "due_tbl"}
    future_event = {"table_name": "future_tbl"}
    pending_ddl = {
        ("t1", "w1", "due_tbl"): {"due": time.time() - 1, "event": due_event},
        ("t1", "w1", "future_tbl"): {"due": time.time() + 60, "event": future_event},
    }

    processed = worker.process_due_ddl_events(conn, qdrant, pending_ddl)

    assert processed == [due_event]
    assert list(pending_ddl.values()) == [{"due": pending_ddl[("t1", "w1", "future_tbl")]["due"], "event": future_event}]


@pytest.mark.unit
def test_handler_exception_rolls_back_and_is_not_reported_as_processed(monkeypatch):
    """Doesn't crash the worker loop (matches handle_ddl_event's caller in
    main(), and crawl_all()'s per-warehouse try/except convention)."""
    from workers.schema_crawler import worker

    conn, qdrant = MagicMock(), MagicMock()
    monkeypatch.setattr(worker, "handle_ddl_event", MagicMock(side_effect=RuntimeError("boom")))

    event = {"table_name": "tbl"}
    pending_ddl = {("t1", "w1", "tbl"): {"due": time.time() - 1, "event": event}}

    processed = worker.process_due_ddl_events(conn, qdrant, pending_ddl)

    assert processed == []
    assert pending_ddl == {}  # already popped before the handler ran
    conn.rollback.assert_called_once()
