"""Durable, tenant-scoped human approval records."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from services.workflow_runtime.infrastructure.db import client


async def put_pending_review(
    conversation_id: str,
    tenant_id: str,
    agent_id: str,
    query: str,
    payload: Any,
) -> None:
    async with client.get_client(tenant_id=tenant_id) as session:
        await session.execute(
            text(
                """
                INSERT INTO runtime.pending_review
                    (conversation_id, tenant_id, agent_id, query, payload, suspended_at, version)
                VALUES (:conversation_id, :tenant_id, :agent_id, :query, :payload, :suspended_at, 1)
                ON CONFLICT (conversation_id) DO UPDATE SET
                    tenant_id = EXCLUDED.tenant_id,
                    agent_id = EXCLUDED.agent_id,
                    query = EXCLUDED.query,
                    payload = EXCLUDED.payload,
                    suspended_at = EXCLUDED.suspended_at,
                    version = runtime.pending_review.version + 1
                """
            ),
            {
                "conversation_id": conversation_id,
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "query": query,
                "payload": payload,
                "suspended_at": datetime.now(UTC),
            },
        )
        await session.commit()


async def list_pending_reviews(tenant_id: str) -> list[dict[str, Any]]:
    async with client.get_client(tenant_id=tenant_id) as session:
        result = await session.execute(
            text(
                """
                SELECT conversation_id, agent_id, query, payload, suspended_at, version
                FROM runtime.pending_review
                ORDER BY suspended_at ASC
                """
            )
        )
        return [dict(row) for row in result.mappings().all()]


async def get_pending_review(conversation_id: str, tenant_id: str) -> dict[str, Any] | None:
    async with client.get_client(tenant_id=tenant_id) as session:
        result = await session.execute(
            text(
                """
                SELECT conversation_id, agent_id, query, payload, suspended_at, version
                FROM runtime.pending_review
                WHERE conversation_id = :conversation_id
                """
            ),
            {"conversation_id": conversation_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None


async def delete_pending_review(conversation_id: str, tenant_id: str) -> bool:
    async with client.get_client(tenant_id=tenant_id) as session:
        result = await session.execute(
            text(
                "DELETE FROM runtime.pending_review WHERE conversation_id = :conversation_id"
            ),
            {"conversation_id": conversation_id},
        )
        await session.commit()
        return bool(result.rowcount)
