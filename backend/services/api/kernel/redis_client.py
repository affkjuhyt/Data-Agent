"""Async Redis client for the api (rate limit, Idempotency-Key, SSE progress, private channel).

Redis closes idle TCP clients after ``timeout`` seconds (300 in deploy/k8s/*/database/redis.yaml).
``health_check_interval`` alone still leaves a race: a pooled connection can be closed between
the check and the command. Retries on connection errors reopen a socket so a brief outage or
idle kill does not surface as ``internal_error`` on an otherwise successful POST.
"""

from __future__ import annotations

from typing import Any


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
