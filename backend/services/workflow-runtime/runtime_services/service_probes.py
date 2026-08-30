"""Live dependency probes behind GET /api/platform/services.

Each probe is a real round-trip to the thing it reports on, bounded by
PROBE_TIMEOUT_S and run concurrently. A probe NEVER raises: a failure is
reported as status "down" with the error text in `detail`; a dependency whose
address is not configured in this process is "unknown" rather than guessed.

Postgres / Redis / Mongo / Kafka reuse the connections workflow-runtime already
holds (db.client, redis_cache._client, mongo_client, kafka_producer._producer),
so what is reported is what the runtime itself can reach.
"""
from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

PROBE_TIMEOUT_S = 2.0

_KINDS = {
    "postgres": "database",
    "redis": "cache",
    "mongo": "database",
    "kafka": "queue",
    "litellm": "llm",
    "sandbox-executor": "service",
    "orchestrator": "service",
    "kb-backend": "service",
}


def _result(name: str, status: str, latency_ms: float | None = None, detail: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "kind": _KINDS.get(name, "service"),
        "status": status,
        "latency_ms": round(latency_ms, 1) if latency_ms is not None else None,
        "detail": detail,
    }


async def _timed(name: str, fn: Callable[[], Awaitable[str]]) -> dict[str, Any]:
    """Run `fn` under the probe timeout; it returns a detail string on success."""
    started = time.monotonic()
    try:
        detail = await asyncio.wait_for(fn(), timeout=PROBE_TIMEOUT_S)
        return _result(name, "ok", (time.monotonic() - started) * 1000, detail)
    except _Unknown as e:
        return _result(name, "unknown", None, str(e))
    except TimeoutError:
        return _result(name, "down", (time.monotonic() - started) * 1000, f"timeout after {PROBE_TIMEOUT_S:g}s")
    except _Degraded as e:
        return _result(name, "degraded", (time.monotonic() - started) * 1000, str(e))
    except Exception as e:  # noqa: BLE001 — a probe must report, not raise
        return _result(name, "down", (time.monotonic() - started) * 1000, f"{type(e).__name__}: {e}"[:300])


class _Unknown(Exception):
    """The dependency's address is not configured in this process."""


class _Degraded(Exception):
    """Raised inside a probe when the dependency answered but not healthily."""


# ─── Individual probes ────────────────────────────────────────────────────────

async def _probe_postgres() -> str:
    from sqlalchemy import text

    from libs.database_connector.postgres_connector import client

    if not client.is_connected():
        raise RuntimeError("not connected (RUNTIME_DB_URL/DATABASE_URL unset or open() failed)")
    async with client.get_client() as session:
        await session.execute(text("SELECT 1"))
    return "SELECT 1 ok"


async def _probe_redis() -> str:
    import redis_cache

    if redis_cache._client is not None:
        await redis_cache._client.ping()
        return "PING ok"
    if not (os.environ.get("REDIS_URL") or os.environ.get("REDIS_HOST")):
        raise _Unknown("REDIS_URL not set")
    import redis.asyncio as aioredis

    conn = aioredis.from_url(redis_cache._REDIS_URL, decode_responses=True)
    try:
        await conn.ping()
    finally:
        await conn.aclose()
    return "PING ok (one-off connection; runtime cache client not initialised)"


async def _probe_mongo() -> str:
    from libs.database_connector.mongo_connector import client

    if not client.is_connected():
        raise RuntimeError("not connected (MONGODB_URL unreachable at startup)")
    await client.get_client().command("ping")
    return "ping ok"


async def _probe_kafka() -> str:
    import kafka_producer

    if not os.environ.get("KAFKA_BOOTSTRAP_SERVERS"):
        raise _Unknown("KAFKA_BOOTSTRAP_SERVERS not set")
    producer = kafka_producer._producer
    if producer is None:
        raise RuntimeError("producer not connected (broker unreachable at startup)")
    connected = await asyncio.to_thread(producer.bootstrap_connected)
    if not connected:
        raise _Degraded("producer exists but bootstrap connection is not established")
    return f"bootstrap connected ({kafka_producer._KAFKA_BOOTSTRAP})"


async def _http_get(url: str) -> str:
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_S) as http:
        resp = await http.get(url)
    if resp.status_code >= 500:
        raise RuntimeError(f"HTTP {resp.status_code} from {url}")
    if resp.status_code >= 400:
        raise _Degraded(f"HTTP {resp.status_code} from {url}")
    return f"HTTP {resp.status_code} {url}"


def _http_probe(env_var: str, path: str) -> Callable[[], Awaitable[str]]:
    async def _probe() -> str:
        base = os.environ.get(env_var, "").rstrip("/")
        if not base:
            raise _Unknown(f"{env_var} not set")
        return await _http_get(f"{base}{path}")
    return _probe


_PROBES: dict[str, Callable[[], Awaitable[str]]] = {
    "postgres": _probe_postgres,
    "redis": _probe_redis,
    "mongo": _probe_mongo,
    "kafka": _probe_kafka,
    "litellm": _http_probe("LITELLM_BASE_URL", "/health/liveliness"),
    "sandbox-executor": _http_probe("SANDBOX_EXECUTOR_URL", "/health"),
    "orchestrator": _http_probe("ORCHESTRATOR_URL", "/health"),
    "kb-backend": _http_probe("KB_BACKEND_URL", "/health"),
}


async def probe_all() -> list[dict[str, Any]]:
    """Every probe, concurrently; the list is in _PROBES order and never raises."""
    return list(await asyncio.gather(*(_timed(n, fn) for n, fn in _PROBES.items())))
