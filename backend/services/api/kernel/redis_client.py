"""Async Redis client for the api (rate limit, Idempotency-Key, SSE progress, private channel).

Redis closes idle TCP clients after ``timeout`` seconds (300 in deploy/k8s/*/database/redis.yaml).
``health_check_interval`` alone still leaves a race: a pooled connection can be closed between
the check and the command. Retries on connection errors reopen a socket so a brief outage or
idle kill does not surface as ``internal_error`` on an otherwise successful POST.

PL-08: the guards that keep a write from running twice or too often (Idempotency-Key, the rate
limit, a route's quota) fail closed when Redis is missing or down: 503 source_unavailable with
Retry-After, before anything is written. ``API_REDIS_FAIL_OPEN=true`` turns that back into the
earlier fail-open (the guard is skipped with a warning), for a dev box or a test suite with no
Redis; manifests set it ``false``.
"""

from __future__ import annotations

import os
from typing import Any

from services.api.kernel.problems import Problem

FAIL_OPEN_ENV = "API_REDIS_FAIL_OPEN"
# Long enough for the client's own retry (redis_from_url) and a pod restart to be over.
RETRY_AFTER_SECONDS = 5


def fail_open() -> bool:
    """Whether a guard Redis backs is skipped (true) or refuses (false) when Redis is not there.
    Read on every call, so a test can switch it."""
    return os.environ.get(FAIL_OPEN_ENV, "").strip().lower() in ("1", "true", "yes")


def unavailable(guard: str) -> Problem:
    """The 503 a write gets when the store behind `guard` cannot be reached."""
    return Problem(
        503,
        "source_unavailable",
        f"The store behind {guard} is unavailable, so this write was not run; retry shortly.",
        headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
    )


def redis_from_url(url: str, **kwargs: Any) -> Any:
    """``redis.asyncio`` client with keepalive and retries on connection loss."""
    import redis.asyncio as redis
    from redis.backoff import ExponentialBackoff
    from redis.exceptions import ConnectionError as RedisConnectionError
    from redis.exceptions import TimeoutError as RedisTimeoutError
    from redis.retry import Retry

    options = {
        "decode_responses": True,
        "health_check_interval": 30,
        "socket_keepalive": True,
        "retry": Retry(ExponentialBackoff(cap=0.5, base=0.05), retries=3),
        "retry_on_error": [RedisConnectionError, RedisTimeoutError],
        **kwargs,
    }
    return redis.from_url(url, **options)
