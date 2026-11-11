"""Load active relationships of a fact and shape a mart's dimensions / suggestions / relations."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db.models import catalog
from libs.dataagent_db.models import context
from libs.mart_model import propose_from_relationships, relations_match_active


async def _datasets(
    session: AsyncSession, data_space: uuid.UUID, ids: set[uuid.UUID]
) -> dict[uuid.UUID, dict[str, Any]]:
    if not ids:
        return {}
    rows = (
        await session.scalars(
            select(catalog.Dataset).where(
                catalog.Dataset.id.in_(ids),
                catalog.Dataset.data_space_id == data_space,
                catalog.Dataset.deleted_at.is_(None),
            )
        )
    ).all()
    return {
        d.id: {
            "id": d.id,
            "table_name": d.table_name,
            "schema_name": d.schema_name,
            "display_name": d.display_name,
        }
        for d in rows
    }


async def active_fact_relationships(
    session: AsyncSession, data_space: uuid.UUID, fact_dataset_id: uuid.UUID
) -> tuple[list[dict[str, Any]], dict[uuid.UUID, dict[str, Any]]]:
    rows = (
        await session.scalars(
            select(context.Relationship).where(
                context.Relationship.data_space_id == data_space,
                context.Relationship.from_dataset_id == fact_dataset_id,
                context.Relationship.status == "active",
            )
        )
    ).all()
    payload = [
        {
            "from_dataset_id": r.from_dataset_id,
            "to_dataset_id": r.to_dataset_id,
            "from_columns": list(r.from_columns or []),
            "to_columns": list(r.to_columns or []),
            "status": r.status,
        }
        for r in rows
    ]
    targets = {r["to_dataset_id"] for r in payload} | {fact_dataset_id}
    return payload, await _datasets(session, data_space, targets)


async def shape_mart(
    session: AsyncSession,
    *,
    data_space: uuid.UUID,
    fact_dataset_id: uuid.UUID,
    dimensions: list[Any] | None,
    relations: list[Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None, list[dict[str, Any]]]:
    """Dimensions (author's), suggested_dimensions (or None), relations to store on create."""
    relationships, datasets = await active_fact_relationships(session, data_space, fact_dataset_id)
    kept, suggested, proposed_relations = propose_from_relationships(
        fact_dataset_id=fact_dataset_id,
        relationships=relationships,
        datasets=datasets,
        dimensions=dimensions or [],
    )
    final_relations = list(relations) if relations else proposed_relations
    return kept, suggested or None, final_relations


async def check_mart_relations(
    session: AsyncSession,
    *,
    data_space: uuid.UUID,
    fact_dataset_id: uuid.UUID,
    relations: list[Any] | None,
) -> tuple[bool, list[str]]:
    relationships, datasets = await active_fact_relationships(session, data_space, fact_dataset_id)
    return relations_match_active(
        fact_dataset_id=fact_dataset_id,
        relations=relations or [],
        relationships=relationships,
        datasets=datasets,
    )
