"""Replayable progress events for the V3 question stream.

The API owns the HTTP SSE connection; worker-agent owns the question lifecycle. Redis
Streams bridge those processes and retain a short replay window for reconnects.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_MAX_EVENTS = 100
_redis: Any = None


def _client() -> Any:
    global _redis
    if _redis is None:
        url = os.environ.get("REDIS_URL")
        if not url:
            return None
        import redis.asyncio as redis

        _redis = redis.from_url(url, decode_responses=True)
    return _redis


async def publish(question_id: str, event: str, data: dict[str, Any]) -> str | None:
    """Append one lifecycle event, failing open when Redis is unavailable.

    The database remains the source of truth for the question and answer. Redis only
    improves live progress and reconnect latency, so a Redis outage must not roll back
    a committed answer or make the worker retry it.
    """
    client = _client()
    if client is None:
        return None
    try:
        entry_id = await client.xadd(
            f"sse:{question_id}",
            {"event": event, "data": json.dumps(data, ensure_ascii=False, default=str)},
            maxlen=_MAX_EVENTS,
            approximate=True,
        )
        return str(entry_id)
    except Exception:  # noqa: BLE001 - fail open by design (docstring): Redis is not the record
        logger.warning(
            "worker-agent | progress publish failed question=%s", question_id, exc_info=True
        )
        return None


async def close() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
