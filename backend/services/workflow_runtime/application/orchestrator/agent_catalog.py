from __future__ import annotations

import logging
from typing import Any

from cachetools import TTLCache
from sqlalchemy import text

logger = logging.getLogger(__name__)

_GUARDRAIL_CACHE_TTL = 60.0  # giây — guardrail chạy đầu mọi request, cache
                             # để tránh 1 query DB/request (xem load test).
_GUARDRAIL_KEY = "guardrail"
# Fresh config (expires after the TTL) …
_guardrail_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=1, ttl=_GUARDRAIL_CACHE_TTL)
# … and the last config we ever loaded, served when the DB is down or the
# query fails so a transient outage never drops the guardrail rules.
_guardrail_last_known: dict[str, Any] = {}


async def fetch_guardrail_config(tenant_id: str | None = None) -> dict[str, Any]:
    """
    Load cấu hình guardrail platform-wide (regex patterns, allowed topics,
    model) từ bảng "Guardrails" — cache trong process TTL 60s.

    Trước bản fix này, orchestrator luôn truyền cfg={} cho run_guardrail()
    (routers/stream.py có `_CFG: dict = {}` không ai gán giá trị), nên các
    rule regex/allowed-topics đã seed trong DB (migrations/seed.sql) chưa
    từng có tác dụng — chỉ lớp LLM check chạy với model mặc định hardcode.
    """
    global _guardrail_last_known

    cached: dict[str, Any] | None = _guardrail_cache.get(_GUARDRAIL_KEY)
    if cached:
        return cached

    from libs.database_connector.postgres_connector import client

    if not client.is_connected():
        logger.warning("guardrail_config | DB not connected, using last known config")
        return _guardrail_last_known

    try:
        async with client.get_client(tenant_id=tenant_id) as session:
            row = (await session.execute(text("""
                SELECT g.id, g.conditions,
                       lp.model_id AS guardrail_model
                FROM "Guardrails" g
                LEFT JOIN "LLMProviders" lp ON lp.id = g.guardrail_model_id
                ORDER BY g.priority DESC
                LIMIT 1
            """))).fetchone()

        if row:
            cond = dict(row.conditions or {})
            cfg: dict[str, Any] = {
                "guardrail_id":             str(row.id),
                "guardrail_input_patterns": cond.get("input_patterns", []),
                "guardrail_llm_enabled":    cond.get("llm_enabled", True),
                "guardrail_allowed_topics": cond.get("allowed_topics", []),
                "guardrail_model":          row.guardrail_model or "openai/guardrail",
                "guardrail_max_tokens":     cond.get("max_tokens", 64),
                "guardrail_timeout":        cond.get("timeout", 15),
                "guardrail_num_retries":    cond.get("num_retries", 1),
                # Chặn hay cho qua khi CẢ hai lớp check đều không kết luận
                # được (LLM lỗi + regex không khớp). Không set → theo env
                # GUARDRAIL_FAIL_CLOSED của deployment.
                "guardrail_fail_closed":    cond.get("fail_closed"),
            }
        else:
            cfg = {}
        _guardrail_cache[_GUARDRAIL_KEY] = cfg
        _guardrail_last_known = cfg
        return cfg

    except Exception as e:
        logger.warning("guardrail_config DB query failed: %s — using last known config", e)
        return _guardrail_last_known


async def fetch_published_agents(tenant_id: str) -> list[dict[str, Any]]:
    """
    Lấy danh sách agents đã publish của tenant trực tiếp từ PostgreSQL.
    Trả về list[{id, name, description}].
    """
    from libs.database_connector.postgres_connector import client

    if not client.is_connected():
        logger.warning("catalog | DB not connected, returning empty agent list")
        return []

    try:
        async with client.get_client(tenant_id=tenant_id) as session:
            rows = (await session.execute(text("""
                SELECT a.id, a.name, a.description
                FROM "Agents" a
                WHERE a.tenant_id            = :tenant_id
                  AND a.is_active            = true
                  AND a.deleted_at           IS NULL
                  AND a.published_version_id IS NOT NULL
                ORDER BY a.created_at DESC
            """), {"tenant_id": tenant_id})).fetchall()

        agents = [
            {"id": str(r.id), "name": r.name, "description": r.description or ""}
            for r in rows
        ]
        logger.info("catalog | tenant=%s published_agents=%d", tenant_id, len(agents))
        return agents

    except Exception as e:
        logger.warning("catalog DB query failed: %s", e)
        return []
