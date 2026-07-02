from __future__ import annotations

import logging
import time

from sqlalchemy import text

logger = logging.getLogger(__name__)

_GUARDRAIL_CACHE_TTL = 60.0  # giây — guardrail chạy đầu mọi request, cache
                             # để tránh 1 query DB/request (xem load test).
_guardrail_cfg: dict = {}
_guardrail_cfg_at: float = 0.0


async def fetch_guardrail_config(tenant_id: str | None = None) -> dict:
    """
    Load cấu hình guardrail platform-wide (regex patterns, allowed topics,
    model) từ bảng "Guardrails" — cache trong process TTL 60s.

    Trước bản fix này, orchestrator luôn truyền cfg={} cho run_guardrail()
    (routers/stream.py có `_CFG: dict = {}` không ai gán giá trị), nên các
    rule regex/allowed-topics đã seed trong DB (migrations/seed.sql) chưa
    từng có tác dụng — chỉ lớp LLM check chạy với model mặc định hardcode.
    """
    global _guardrail_cfg, _guardrail_cfg_at

    now = time.monotonic()
    if _guardrail_cfg and (now - _guardrail_cfg_at) < _GUARDRAIL_CACHE_TTL:
        return _guardrail_cfg

    from services.database_connector.postgres_connector import client

    if not client.is_connected():
        logger.warning("guardrail_config | DB not connected, using last known config")
        return _guardrail_cfg

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
            _guardrail_cfg = {
                "guardrail_id":             str(row.id),
                "guardrail_input_patterns": cond.get("input_patterns", []),
                "guardrail_llm_enabled":    cond.get("llm_enabled", True),
                "guardrail_allowed_topics": cond.get("allowed_topics", []),
                "guardrail_model":          row.guardrail_model or "openai/guardrail",
                "guardrail_max_tokens":     cond.get("max_tokens", 64),
                "guardrail_timeout":        cond.get("timeout", 15),
            }
        else:
            _guardrail_cfg = {}
        _guardrail_cfg_at = now

    except Exception as e:
        logger.warning("guardrail_config DB query failed: %s — using last known config", e)

    return _guardrail_cfg


async def fetch_published_agents(tenant_id: str) -> list[dict]:
    """
    Lấy danh sách agents đã publish của tenant trực tiếp từ PostgreSQL.
    Trả về list[{id, name, description}].
    """
    from services.database_connector.postgres_connector import client

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
