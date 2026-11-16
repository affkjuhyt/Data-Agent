"""Prometheus metrics of the V3 deployables (PL-05): what each served and how long it took.

The api counts and times its requests by route template (`http_*`, `kernel/metrics.py` mounts it);
each worker counts and times the outbox events it handles (`events_*`). The time of a question is
the time of its `question.received` event in worker-agent, from claim to answer: what a person
waits once the relay picked it up, the poll interval aside.

Labels are bounded on purpose: a route template, an event type, an outcome, never an id or a raw
path, so a series is not made per request.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    start_http_server,
)

# Seconds, from a cached read to an agentic answer that runs for minutes.
BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 120, 300)

HTTP_REQUESTS = Counter(
    "dataagent_http_requests_total",
    "Requests the api answered, by route template, method and status class.",
    ["service", "method", "route", "status"],
)
HTTP_SECONDS = Histogram(
    "dataagent_http_request_seconds",
    "Time the api took to answer a request, by route template.",
    ["service", "method", "route"],
    buckets=BUCKETS,
)
HTTP_IN_FLIGHT = Gauge(
    "dataagent_http_requests_in_flight",
    "Requests the api is answering now.",
    ["service"],
)
EVENTS = Counter(
    "dataagent_events_total",
    "Outbox events a worker handled, by type and outcome (ok, error).",
    ["service", "event_type", "outcome"],
)
EVENT_SECONDS = Histogram(
    "dataagent_event_seconds",
    "Time a worker took to handle an outbox event, by type. question.received is the time of a question.",
    ["service", "event_type"],
    buckets=BUCKETS,
)
LAST_POLL = Gauge(
    "dataagent_worker_last_poll_timestamp_seconds",
    "When the worker last finished a pass over its sources: a stale value is a stuck loop.",
    ["service"],
)


def status_class(status: int) -> str:
    return f"{status // 100}xx"


async def timed_event(service: str, event_type: str, handle: Callable[[], Awaitable[Any]]) -> Any:
    """Run one event's handler and record it; the handler's error goes on to the caller."""
    started = time.perf_counter()
    try:
        result = await handle()
    except BaseException:
        EVENTS.labels(service, event_type, "error").inc()
        raise
    finally:
        EVENT_SECONDS.labels(service, event_type).observe(time.perf_counter() - started)
    EVENTS.labels(service, event_type, "ok").inc()
    return result


def polled(service: str) -> None:
    LAST_POLL.labels(service).set(time.time())


def serve(port: int | None) -> None:
    """A worker's /metrics, on its own port in a daemon thread; no port, no endpoint (tests)."""
    if port:
        start_http_server(port)


def exposition() -> tuple[bytes, str]:
    """The text format of every metric in this process, for the api's /metrics route."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
