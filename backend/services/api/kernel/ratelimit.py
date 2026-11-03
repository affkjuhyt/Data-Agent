"""A1.8 rate limit: 120 calls a minute per person by default, with RateLimit-Limit,
RateLimit-Remaining and RateLimit-Reset on every /v1 response; over the limit answers 429
rate_limited with Retry-After.

Counted per organisation and token subject in fixed one-minute windows in a shared store (Redis
in deployment), so every api pod sees the same count. An api_client calling /mcp has its own
quotas (integration.api_client, mcp.py) and is not counted here. A request without a verified
token is not counted: its route answers 401 anyway. Without REDIS_URL the limit is off (a
warning at start), the same fail-open choice as Idempotency-Key.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Awaitable, Callable
from typing import Protocol

from fastapi import Request, Response

from services.api.kernel.auth import bearer_claims
from services.api.kernel.problems import Problem, problem_response

logger = logging.getLogger(__name__)

PER_MINUTE_ENV = "API_RATE_LIMIT_PER_MINUTE"
DEFAULT_PER_MINUTE = 120
WINDOW_SECONDS = 60
# AGC-03: a question costs a planner, its tools and a reporter (several model calls, queries on the
# warehouse), where most calls cost one read; 120 of them a minute from one person would hold the
# model servers for everyone else.
QUESTIONS_PER_MINUTE_ENV = "API_QUESTIONS_PER_MINUTE"
DEFAULT_QUESTIONS_PER_MINUTE = 10


class CounterStore(Protocol):
    async def hit(self, key: str, ttl_seconds: int) -> int:
        """Add one to `key` (created with `ttl_seconds` to live) and return the new count."""
        ...


class RedisCounterStore:
    def __init__(self, url: str) -> None:
        from services.api.kernel.redis_client import redis_from_url

        self._redis = redis_from_url(url)

    async def hit(self, key: str, ttl_seconds: int) -> int:
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, ttl_seconds, nx=True)
            count, _ = await pipe.execute()
        return int(count)


def counter_store_from_env() -> CounterStore | None:
    url = os.environ.get("REDIS_URL")
    if not url:
        logger.warning("api | REDIS_URL is not set: the per-user rate limit is off")
        return None
    return RedisCounterStore(url)


def per_minute() -> int:
    return int(os.environ.get(PER_MINUTE_ENV, DEFAULT_PER_MINUTE))


def questions_per_minute() -> int:
    return int(os.environ.get(QUESTIONS_PER_MINUTE_ENV, DEFAULT_QUESTIONS_PER_MINUTE))


async def within_quota(request: Request, name: str, caller: str, limit: int) -> None:
    """A route's own quota, under the per-person limit: `limit` calls of `name` a minute by
    `caller`, in the same store and windows; over it raises 429 rate_limited with Retry-After.
    Fails open like the middleware: no store, or a store that is down, counts nothing."""
    store: CounterStore | None = getattr(request.app.state, "counter_store", None)
    if store is None:
        return
    now = time.time()
    window = int(now // WINDOW_SECONDS)
    reset = WINDOW_SECONDS - int(now % WINDOW_SECONDS)
    try:
        count = await store.hit(f"quota:{name}:{caller}:{window}", reset + 1)
    except Exception:  # noqa: BLE001 - a store outage must not take the api down with it
        logger.warning("api | quota store unavailable; %s not counted", name, exc_info=True)
        return
    if count > limit:
        raise Problem(
            429,
            "rate_limited",
            f"More than {limit} {name} in a minute; retry after the window resets.",
            headers={"Retry-After": str(reset)},
        )


async def rate_limit_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
    store: CounterStore | None,
    limit: int,
) -> Response:
    if store is None or not request.url.path.startswith("/v1/"):
        return await call_next(request)
    claims = await bearer_claims(request)
    if claims is None or "org_id" not in claims or "sub" not in claims:
        return await call_next(request)
    now = time.time()
    window = int(now // WINDOW_SECONDS)
    reset = WINDOW_SECONDS - int(now % WINDOW_SECONDS)
    try:
        count = await store.hit(f"ratelimit:{claims['org_id']}:{claims['sub']}:{window}", reset + 1)
    except Exception:  # noqa: BLE001 - a store outage must not take the api down with it
        logger.warning("api | rate limit store unavailable; request not counted", exc_info=True)
        return await call_next(request)
    headers = {
        "RateLimit-Limit": str(limit),
        "RateLimit-Remaining": str(max(limit - count, 0)),
        "RateLimit-Reset": str(reset),
    }
    response: Response
    if count > limit:
        response = problem_response(
            Problem(
                429,
                "rate_limited",
                f"More than {limit} calls in a minute; retry after the window resets.",
                headers={"Retry-After": str(reset)},
            )
        )
    else:
        response = await call_next(request)
    response.headers.update(headers)
    return response
