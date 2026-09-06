import json
import logging
import os
from typing import Any, Optional, cast

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_REDIS_URL = os.environ.get("REDIS_URL") or (
    f"redis://{os.environ.get('REDIS_HOST', 'localhost')}:{os.environ.get('REDIS_PORT', '6379')}"
)
_TTL = int(os.environ.get("CONV_CACHE_TTL_SECONDS", str(30 * 60)))  # 30min default — doc §3.1 session timeout
_SCHEMA_TTL = int(os.environ.get("DB_SCHEMA_CACHE_TTL_SECONDS", str(10 * 60)))  # 10min default

_client: Optional[aioredis.Redis] = None


async def init_redis() -> None:
    global _client
    try:
        _client = aioredis.from_url(_REDIS_URL, decode_responses=True)
        await _client.ping()
        logger.info("redis | connected %s", _REDIS_URL)
    except Exception as e:
        logger.warning("redis | unavailable, cache disabled: %s", e)
        _client = None


async def close_redis() -> None:
    global _client
    if _client:
        await _client.aclose()
        _client = None


def _key(conversation_id: str) -> str:
    return f"conv:{conversation_id}:messages"


async def cache_get_messages(conversation_id: str) -> Optional[list[dict[str, Any]]]:
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


async def cache_append_messages(conversation_id: str, messages: list[dict[str, Any]]) -> None:
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


async def cache_set_messages(conversation_id: str, messages: list[dict[str, Any]]) -> None:
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


def _schema_key(tenant_id: str, host: str, port: int, database: str, db_schema: str = "public") -> str:
    # Cố ý KHÔNG dùng connection "name" (do người dùng tự đặt, đổi tên không
    # đổi DB thật) hay user/password (đổi tài khoản không đổi cấu trúc bảng)
    # — key theo đúng danh tính DB vật lý, để 2 connection khác tên trỏ cùng
    # 1 DB tự nhiên share chung cache.
    # v2: the cached value is now the RAW introspected table list (JSON), not a
    # pre-rendered schema string. Rendering became query-dependent (only the
    # tables relevant to this question reach the prompt), so caching the
    # rendered text would key one query's selection under every query. Bumping
    # the prefix retires v1 values instead of feeding a JSON parser prose.
    # v3: adds the Postgres schema (was implicitly always 'public') — two
    # connections to the same host/port/database but different schemas (e.g.
    # kb_structured vs public) must not share a cached table list.
    return f"db_schema_v3:{tenant_id}:{host}:{port}:{database}:{db_schema}"


async def cache_get_schema(tenant_id: str, host: str, port: int, database: str, db_schema: str = "public") -> Optional[str]:
    if not _client:
        return None
    try:
        return cast(Optional[str], await _client.get(_schema_key(tenant_id, host, port, database, db_schema)))
    except Exception as e:
        logger.warning("redis | cache_get_schema failed db=%s: %s", database, e)
        return None


async def cache_set_schema(tenant_id: str, host: str, port: int, database: str, schema_text: str, db_schema: str = "public") -> None:
    if not _client:
        return
    try:
        await _client.set(_schema_key(tenant_id, host, port, database, db_schema), schema_text, ex=_SCHEMA_TTL)
    except Exception as e:
        logger.warning("redis | cache_set_schema failed db=%s: %s", database, e)


# ── analytical slots (follow-up refinement) ────────────────────────────────
# Keyed by conversation, not by user or agent: a refinement like "chỉ lấy miền
# Bắc" only makes sense against the thread it was said in. Same TTL as the
# conversation cache — a thread nobody has touched in that long is not one
# anybody is refining.
def _slots_key(conversation_id: str) -> str:
    return f"analytical_slots:{conversation_id}"


async def cache_get_slots(conversation_id: str) -> Optional[dict[str, Any]]:
    if not _client or not conversation_id:
        return None
    try:
        raw = await _client.get(_slots_key(conversation_id))
        return cast(Optional[dict[str, Any]], json.loads(raw)) if raw else None
    except Exception as e:
        logger.warning("redis | cache_get_slots failed conv=%s: %s", conversation_id, e)
        return None


async def cache_set_slots(conversation_id: str, slots: dict[str, Any]) -> None:
    if not _client or not conversation_id:
        return
    try:
        await _client.set(_slots_key(conversation_id), json.dumps(slots, ensure_ascii=False), ex=_TTL)
    except Exception as e:
        logger.warning("redis | cache_set_slots failed conv=%s: %s", conversation_id, e)
