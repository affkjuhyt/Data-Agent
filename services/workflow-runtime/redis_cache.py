import json
import logging
import os
from typing import Optional

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_REDIS_URL = os.environ.get("REDIS_URL") or (
    f"redis://{os.environ.get('REDIS_HOST', 'localhost')}:{os.environ.get('REDIS_PORT', '6379')}"
)
_TTL = int(os.environ.get("CONV_CACHE_TTL_SECONDS", str(24 * 3600)))  # 24h default

_client: Optional[aioredis.Redis] = None


async def init_redis():
    global _client
    try:
        _client = aioredis.from_url(_REDIS_URL, decode_responses=True)
        await _client.ping()
        logger.info("redis | connected %s", _REDIS_URL)
    except Exception as e:
        logger.warning("redis | unavailable, cache disabled: %s", e)
        _client = None


async def close_redis():
    global _client
    if _client:
        await _client.aclose()
        _client = None


def _key(conversation_id: str) -> str:
    return f"conv:{conversation_id}:messages"


async def cache_get_messages(conversation_id: str) -> Optional[list[dict]]:
    if not _client:
        return None
    try:
        items = await _client.lrange(_key(conversation_id), 0, -1)
        if not items:
            return None
        return [json.loads(item) for item in items]
    except Exception as e:
        logger.warning("redis | cache_get failed conv=%s: %s", conversation_id, e)
        return None


async def cache_append_messages(conversation_id: str, messages: list[dict]):
    if not _client:
        return
    try:
        key = _key(conversation_id)
        pipe = _client.pipeline()
        for msg in messages:
            pipe.rpush(key, json.dumps(msg, ensure_ascii=False))
        pipe.expire(key, _TTL)
        await pipe.execute()
    except Exception as e:
        logger.warning("redis | cache_append failed conv=%s: %s", conversation_id, e)


async def cache_set_messages(conversation_id: str, messages: list[dict]):
    """Ghi đè toàn bộ cache — dùng khi load từ DB lần đầu."""
    if not _client or not messages:
        return
    try:
        key = _key(conversation_id)
        pipe = _client.pipeline()
        pipe.delete(key)
        for msg in messages:
            pipe.rpush(key, json.dumps(msg, ensure_ascii=False))
        pipe.expire(key, _TTL)
        await pipe.execute()
    except Exception as e:
        logger.warning("redis | cache_set failed conv=%s: %s", conversation_id, e)
