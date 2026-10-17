"""Loading the Data Space's queryable tables for the exploratory SQL path, as svc_qa.

Its enabled datasets with their catalogued columns, the approved many-to-one relationships (the
joins), known values of text columns (the semantic layer's value_index of a dimension that is a
plain column, and the latest column profile's most common values; never a personal-data column's)
and the verified example queries. A table with no catalogued column is left out: the model would
have to guess its columns.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db.models import catalog, context
from services.worker_agent.sqlgen.schema import Col, Fk, Schema, Tbl

MAX_SAMPLES = 20


@dataclass
class Space:
    schema: Schema
    # Where each table is queried in place: its source id, None for the workspace.
    source_of: dict[uuid.UUID, uuid.UUID | None] = field(default_factory=dict)


async def load_space(session: AsyncSession, space_id: uuid.UUID) -> Space:
    datasets = (
        await session.scalars(
            select(catalog.Dataset).where(
                catalog.Dataset.data_space_id == space_id,
                catalog.Dataset.enabled.is_(True),
                catalog.Dataset.deleted_at.is_(None),
            )
        )
    ).all()
    ids = [d.id for d in datasets]
    metas = (
        await session.scalars(
            select(catalog.ColumnMeta)
            .where(
                catalog.ColumnMeta.dataset_id.in_(ids),
                catalog.ColumnMeta.removed_in_version.is_(None),
            )
            .order_by(catalog.ColumnMeta.ordinal)
        )
    ).all()
    samples: dict[tuple[uuid.UUID, str], list[str]] = defaultdict(list)
    profiles = (
        await session.execute(
            select(
                catalog.ColumnMeta.dataset_id,
                catalog.ColumnMeta.name,
                catalog.ColumnProfile.top_values,
            )
            .join(catalog.ColumnProfile, catalog.ColumnProfile.column_id == catalog.ColumnMeta.id)
            .where(
                catalog.ColumnMeta.dataset_id.in_(ids),
                catalog.ColumnMeta.pii_label.is_(None),
                catalog.ColumnMeta.normalized_type == "string",
            )
        )
    ).all()
    for dataset_id, name, top in profiles:
        for item in top if isinstance(top, list) else []:
            value = item.get("value") if isinstance(item, dict) else item
            if isinstance(value, str) and value not in samples[(dataset_id, name)]:
                samples[(dataset_id, name)].append(value)
    # A dimension that is a plain column of its entity's table: its indexed values are that column's.
    dims = (
        await session.execute(
            select(
                context.SemanticEntity.primary_dataset_id,
                context.SemanticDimension.expression,
                context.ValueIndex.value_code,
            )
            .join(
                context.SemanticDimension,
                context.SemanticDimension.entity_id == context.SemanticEntity.id,
            )
            .join(
                context.ValueIndex, context.ValueIndex.dimension_id == context.SemanticDimension.id
            )
            .where(context.SemanticEntity.data_space_id == space_id)
        )
    ).all()
    for dataset_id, expression, value in dims:
        key = (dataset_id, expression.strip('"'))
        if value not in samples[key]:
            samples[key].append(value)
    pii = {(m.dataset_id, m.name) for m in metas if m.pii_label is not None}
    columns: dict[uuid.UUID, list[Col]] = defaultdict(list)
    for m in metas:
        key = (m.dataset_id, m.name)
        columns[m.dataset_id].append(
            Col(
                m.name,
                m.display_name or m.name,
                m.normalized_type,
                m.description,
                () if key in pii else tuple(samples.get(key, [])[:MAX_SAMPLES]),
            )
        )
    tables = [
        Tbl(
            d.id,
            d.schema_name,
            d.table_name,
            d.display_name or d.table_name,
            tuple(columns[d.id]),
            d.description,
        )
        for d in datasets
        if columns.get(d.id)
    ]
    joins = (
        await session.scalars(
            select(context.Relationship).where(
                context.Relationship.data_space_id == space_id,
                context.Relationship.status == "active",
                context.Relationship.cardinality == "many_to_one",
            )
        )
    ).all()
    examples = (
        await session.execute(
            select(context.ExampleQuery.question, context.ExampleQuery.sql).where(
                context.ExampleQuery.data_space_id == space_id,
                context.ExampleQuery.status == "active",
            )
        )
    ).all()
    return Space(
        Schema(
            tables,
            [
                Fk(j.from_dataset_id, tuple(j.from_columns), j.to_dataset_id, tuple(j.to_columns))
                for j in joins
            ],
            [(str(q), str(s)) for q, s in examples],
        ),
        {d.id: d.source_id for d in datasets},
    )
