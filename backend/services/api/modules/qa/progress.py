"""Read worker-agent question progress from the replayable Redis stream."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from services.api.kernel.sse import Event

logger = logging.getLogger(__name__)


def _client() -> Any:
    url = os.environ.get("REDIS_URL")
    if not url:
        return None
    try:
        import redis.asyncio as redis
    except ImportError:
        logger.warning("api | Redis client is not installed; using database question stream")
        return None
    # Redis closes a client idle for 300 s (redis.conf `timeout`); the check pings a pooled
    # connection idle that long before using it, instead of failing the request on it.
    return redis.from_url(url, decode_responses=True, health_check_interval=30)


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


async def read(question_id: str, last_id: str | None = None) -> list[Event]:
    client = _client()
    if client is None:
        return []
    try:
        rows = await client.xread(
            {f"sse:{question_id}": last_id or "0-0"}, count=100, block=1_500
        )
        events: list[Event] = []
        for _stream, entries in rows:
            for entry_id, fields in entries:
                event_name = _text(fields.get("event", "progress"))
                raw_data = fields.get("data", "{}")
                try:
                    data = json.loads(_text(raw_data))
                except (TypeError, json.JSONDecodeError):
                    logger.warning("api | invalid Redis progress payload question=%s", question_id)
                    continue
                if isinstance(data, dict):
                    events.append(Event(_text(entry_id), event_name, data))
        return events
    except Exception:
        logger.warning("api | progress read failed question=%s", question_id, exc_info=True)
        return []
    finally:
        close = getattr(client, "aclose", None)
        if close is not None:
            await close()
