"""maybe_reconnect_kafka (main()'s periodic Kafka-recovery check) — extracted
from the loop so the retry-cadence logic is testable without a real broker,
same rationale as test_ddl_debounce.py for poll_ddl_events/process_due_ddl_events.

Review context: try_kafka_consumer() used to run once at boot only — if
Kafka was down then, the worker stayed cron-only forever even after Kafka
came back. maybe_reconnect_kafka() is what main() now calls every loop
iteration to retry establishing the consumer every
KAFKA_RECONNECT_INTERVAL_SECONDS while it's down.
"""
from __future__ import annotations

import pathlib
import sys
from unittest.mock import MagicMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "schema-crawler-worker")


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    sys.modules.pop("main", None)
    yield
    sys.modules.pop("main", None)
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


@pytest.mark.unit
def test_existing_consumer_is_left_untouched():
    import main as worker

    consumer = MagicMock()
    result_consumer, result_attempt = worker.maybe_reconnect_kafka(consumer, now=1000.0, last_attempt=0.0)

    assert result_consumer is consumer
    assert result_attempt == 0.0


@pytest.mark.unit
def test_no_consumer_but_interval_not_elapsed_does_not_retry(monkeypatch):
    import main as worker

    spy = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(worker, "try_kafka_consumer", spy)

    result_consumer, result_attempt = worker.maybe_reconnect_kafka(
        None, now=100.0, last_attempt=100.0 - worker.KAFKA_RECONNECT_INTERVAL_SECONDS + 1
    )

    assert result_consumer is None
    spy.assert_not_called()
    assert result_attempt == 100.0 - worker.KAFKA_RECONNECT_INTERVAL_SECONDS + 1


@pytest.mark.unit
def test_no_consumer_and_interval_elapsed_retries_and_recovers(monkeypatch):
    import main as worker

    recovered = MagicMock()
    spy = MagicMock(return_value=recovered)
    monkeypatch.setattr(worker, "try_kafka_consumer", spy)

    now = 100.0 + worker.KAFKA_RECONNECT_INTERVAL_SECONDS
    result_consumer, result_attempt = worker.maybe_reconnect_kafka(None, now=now, last_attempt=100.0)

    spy.assert_called_once_with(retries=1, delay=0)
    assert result_consumer is recovered
    assert result_attempt == now


@pytest.mark.unit
def test_retry_still_failing_keeps_consumer_none_but_stamps_attempt(monkeypatch):
    import main as worker

    spy = MagicMock(return_value=None)
    monkeypatch.setattr(worker, "try_kafka_consumer", spy)

    now = 100.0 + worker.KAFKA_RECONNECT_INTERVAL_SECONDS
    result_consumer, result_attempt = worker.maybe_reconnect_kafka(None, now=now, last_attempt=100.0)

    spy.assert_called_once_with(retries=1, delay=0)
    assert result_consumer is None
    assert result_attempt == now  # stamped so we don't retry again until the next full interval
