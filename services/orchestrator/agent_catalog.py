from __future__ import annotations

import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


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
