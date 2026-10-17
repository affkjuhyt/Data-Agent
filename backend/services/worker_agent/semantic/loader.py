"""Loading a Data Space's semantic layer for one question, as svc_qa (context_read, catalog_read).

Builds what the Understander matches against (resolver.Vocabulary) and what the compiler compiles
with (compiler.Layer), plus each metric's version in force: the current version when it is
certified, otherwise the newest certified one, otherwise the current one uncertified (the answer
then says "chưa có định nghĩa chuẩn", AC-01-01-07). Only active rows count: a synonym pending
review, a rejected relationship or a disabled dataset is not part of what a question can use.

An `Overlay` is the exception, for evaluation only (UC-07-03): the layer as it would be once a
proposal is accepted, a draft metric version used in place of the one in force, a pending synonym
or relationship counted as active. Questions never load one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db.models import catalog, context
from services.worker_agent.semantic import compiler as c
from services.worker_agent.understand.resolver import (
    DimensionEntry,
    MetricEntry,
    ValueEntry,
    Vocabulary,
)


@dataclass(frozen=True)
class Overlay:
    metric_version_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)
    synonym_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)
    relationship_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)


@dataclass
class SpaceLayer:
    vocabulary: Vocabulary
    layer: c.Layer
    versions: dict[uuid.UUID, c.MetricVersion]  # metric id → version in force
    version_rows: dict[uuid.UUID, context.MetricVersion]  # metric id → its row (receipt fields)
    datasets: dict[uuid.UUID, catalog.Dataset]


async def load(
    session: AsyncSession, space_id: uuid.UUID, overlay: Overlay | None = None
) -> SpaceLayer:
    ctx = context
    overlay = overlay or Overlay()
    drafts = (
        {
            v.metric_id: v
            for v in (
                await session.scalars(
                    select(ctx.MetricVersion).where(
                        ctx.MetricVersion.id.in_(overlay.metric_version_ids)
                    )
                )
            ).all()
        }
        if overlay.metric_version_ids
        else {}
    )
    entities = (
        await session.scalars(
            select(ctx.SemanticEntity).where(
                ctx.SemanticEntity.data_space_id == space_id, ctx.SemanticEntity.status == "active"
            )
        )
    ).all()
    dataset_ids = {e.primary_dataset_id for e in entities}
    datasets = {
        d.id: d
        for d in (
            await session.scalars(
                select(catalog.Dataset).where(
                    catalog.Dataset.id.in_(dataset_ids), catalog.Dataset.enabled.is_(True)
                )
            )
        ).all()
    }
    tables = {
        e.id: c.Table(
            e.primary_dataset_id,
            datasets[e.primary_dataset_id].schema_name,
            datasets[e.primary_dataset_id].table_name,
        )
        for e in entities
        if e.primary_dataset_id in datasets
    }
    measures = (
        await session.scalars(
            select(ctx.SemanticMeasure).where(ctx.SemanticMeasure.data_space_id == space_id)
        )
    ).all()
    dims = (
        await session.scalars(
            select(ctx.SemanticDimension).where(ctx.SemanticDimension.data_space_id == space_id)
        )
    ).all()
    joins = (
        await session.scalars(
            select(ctx.Relationship).where(
                ctx.Relationship.data_space_id == space_id,
                or_(
                    ctx.Relationship.status == "active",
                    ctx.Relationship.id.in_(overlay.relationship_ids),
                ),
                ctx.Relationship.cardinality == "many_to_one",
            )
        )
    ).all()
    metrics = (
        await session.scalars(
            select(ctx.Metric).where(
                ctx.Metric.data_space_id == space_id,
                or_(ctx.Metric.status == "active", ctx.Metric.id.in_(list(drafts))),
            )
        )
    ).all()
    version_rows: dict[uuid.UUID, context.MetricVersion] = {}
    for m in metrics:
        rows = (
            await session.scalars(
                select(ctx.MetricVersion)
                .where(ctx.MetricVersion.metric_id == m.id)
                .order_by(ctx.MetricVersion.version.desc())
            )
        ).all()
        current = next((r for r in rows if r.version == m.current_version), None)
        certified = next((r for r in rows if r.status == "certified"), None)
        chosen = (
            current
            if current is not None and current.status == "certified"
            else certified or current
        )
        if m.id in drafts:
            chosen = drafts[m.id]
        if chosen is not None and chosen.status != "broken":
            version_rows[m.id] = chosen
    versions = {
        mid: c.MetricVersion(
            id=v.id,
            metric_code=next(m.code for m in metrics if m.id == mid),
            display_name=v.display_name,
            expression=v.expression,
            filters=tuple(v.filters or ()),
            unit=v.unit,
            decimals=v.decimals,
            period_semantics=v.period_semantics,
            allowed_dimension_ids=frozenset(v.allowed_dimensions or ()),
            certified=v.status == "certified",
            time_grain=v.time_grain,
        )
        for mid, v in version_rows.items()
    }
    dim_by_id = {d.id: d for d in dims}
    # The catalog type of each column a dimension names directly: a time column stored as text
    # is compared as text (compiler._text_bounds). An expression is not a column, so it has none.
    entity_dataset = {e.id: e.primary_dataset_id for e in entities}
    column_types = {
        (col.dataset_id, col.name): col.normalized_type
        for col in (
            await session.scalars(
                select(catalog.ColumnMeta).where(
                    catalog.ColumnMeta.dataset_id.in_(datasets),
                    catalog.ColumnMeta.removed_in_version.is_(None),
                )
            )
        ).all()
    }

    def value_type(d: context.SemanticDimension) -> str | None:
        dataset_id = entity_dataset.get(d.entity_id)
        if dataset_id is None:
            return None
        return column_types.get((dataset_id, d.expression.strip('"')))

    values = (
        await session.scalars(
            select(ctx.ValueIndex).where(ctx.ValueIndex.data_space_id == space_id)
        )
    ).all()
    synonyms = (
        await session.scalars(
            select(ctx.Synonym).where(
                ctx.Synonym.data_space_id == space_id,
                or_(ctx.Synonym.status == "active", ctx.Synonym.id.in_(overlay.synonym_ids)),
            )
        )
    ).all()
    targets: dict[str, tuple[str, ...]] = {}
    for s in synonyms:
        if s.target_kind == "metric":
            targets[s.phrase_norm] = ("metric", str(s.target_id))
        elif s.target_kind == "dimension":
            targets[s.phrase_norm] = ("dimension", str(s.target_id))
        elif s.target_kind == "value" and s.target_value and s.target_id in dim_by_id:
            targets[s.phrase_norm] = ("value", dim_by_id[s.target_id].code, s.target_value)
    vocabulary = Vocabulary(
        metrics=[
            MetricEntry(
                m.id,
                m.code,
                version_rows[m.id].display_name,
                version_rows[m.id].status == "certified",
            )
            for m in metrics
            if m.id in version_rows
        ],
        dimensions=[DimensionEntry(d.id, d.code, d.name, d.is_time) for d in dims],
        values=[
            ValueEntry(dim_by_id[v.dimension_id].code, v.value_code, v.value_label)
            for v in values
            if v.dimension_id in dim_by_id
        ],
        synonyms=targets,
    )
    layer = c.Layer(
        measures={m.code: c.Measure(m.code, m.expression, m.entity_id) for m in measures},
        dimensions={
            d.code: c.Dimension(
                d.id, d.code, d.name, d.expression, d.entity_id, d.is_time, value_type(d)
            )
            for d in dims
        },
        entity_tables=tables,
        joins=tuple(
            c.Join(j.from_dataset_id, tuple(j.from_columns), j.to_dataset_id, tuple(j.to_columns))
            for j in joins
        ),
    )
    return SpaceLayer(vocabulary, layer, versions, version_rows, datasets)
