"""Loading the Data Space's queryable tables for the exploratory SQL path, as svc_qa.

Its enabled datasets with their catalogued columns, the approved many-to-one relationships (the
joins), known values of text columns (the semantic layer's value_index of a dimension that is a
plain column, and the latest column profile's most common values) and the verified example
queries. A column labelled personal data is left out altogether. A table with no catalogued column is left out: the model would
have to guess its columns.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs import clock
from libs.dataagent_db.models import catalog, context, governance
from services.worker_agent.documents import extracted_dataset
from services.worker_agent.sqlgen.schema import Col, Definition, Fk, Schema, Tbl, readable
from services.worker_agent.sqlgen.verified import bind
from services.worker_agent.understand.text import find_period, norm

MAX_SAMPLES = 20


@dataclass
class Space:
    schema: Schema
    # Where each table is queried in place: its source id, None for the workspace.
    source_of: dict[uuid.UUID, uuid.UUID | None] = field(default_factory=dict)


async def load_space(
    session: AsyncSession,
    space_id: uuid.UUID,
    principal_id: uuid.UUID,
    *,
    purpose: str | None = None,
) -> Space:
    """Only what the asker may read is shown to the model: the rules query/scope.datasets_read
    applies when a query runs (no one else's personal table, BR-111; none an active access policy
    covers, BR-45) hold for the prompt too, so a table's names and values never reach the model
    when its rows could not. A table extracted from documents (DI-17) is read as its collection
    is: not once the collection or its schema is gone, nor by a question of another purpose."""
    today = clock.today(await clock.space_zone(session, space_id))
    cal = await clock.space_calendar(session, space_id)
    covered = select(governance.AccessPolicy.dataset_id).where(
        governance.AccessPolicy.status == "active"
    )
    datasets = (
        await session.scalars(
            select(catalog.Dataset).where(
                catalog.Dataset.data_space_id == space_id,
                catalog.Dataset.enabled.is_(True),
                catalog.Dataset.deleted_at.is_(None),
                or_(
                    catalog.Dataset.scope != "personal", catalog.Dataset.created_by == principal_id
                ),
                catalog.Dataset.id.not_in(covered),
                catalog.Dataset.id.not_in(
                    await extracted_dataset.hidden(session, space_id, purpose) or [uuid.UUID(int=0)]
                ),
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
    # The semantic layer's names: a table or column scanned with only its technical name
    # (AdventureWorks' fact_internet_sales) is labelled by the entity or dimension that names it,
    # for linking the question to it and in the prompt.
    entity_names: dict[uuid.UUID, str] = dict(
        (
            await session.execute(
                select(
                    context.SemanticEntity.primary_dataset_id, context.SemanticEntity.name
                ).where(context.SemanticEntity.data_space_id == space_id)
            )
        )
        .tuples()
        .all()
    )
    dimension_names: dict[tuple[uuid.UUID, str], str] = {
        (dataset_id, expression.strip('"')): name
        for dataset_id, expression, name in (
            await session.execute(
                select(
                    context.SemanticEntity.primary_dataset_id,
                    context.SemanticDimension.expression,
                    context.SemanticDimension.name,
                )
                .join(
                    context.SemanticDimension,
                    context.SemanticDimension.entity_id == context.SemanticEntity.id,
                )
                .where(context.SemanticEntity.data_space_id == space_id)
            )
        ).all()
    }
    columns: dict[uuid.UUID, list[Col]] = defaultdict(list)
    for m in metas:
        # A personal-data column is not part of what this path may read: never shown to the
        # model, so the checker rejects any candidate naming it (AC-04-02-02's spirit; the
        # semantic path exposes only metrics and approved dimensions).
        if m.pii_label is not None:
            continue
        columns[m.dataset_id].append(
            Col(
                m.name,
                m.display_name or dimension_names.get((m.dataset_id, m.name)) or m.name,
                m.normalized_type,
                m.description,
                tuple(samples.get((m.dataset_id, m.name), [])[:MAX_SAMPLES]),
            )
        )
    tables = [
        Tbl(
            d.id,
            d.schema_name,
            d.table_name,
            d.display_name or entity_names.get(d.id) or d.table_name,
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
    definitions = await _definitions(session, space_id, tables)
    return Space(
        readable(
            Schema(
                tables,
                [
                    Fk(
                        j.from_dataset_id,
                        tuple(j.from_columns),
                        j.to_dataset_id,
                        tuple(j.to_columns),
                    )
                    for j in joins
                ],
                # A verified query's period placeholders bound to its own question's period, so
                # the model is shown SQL that runs (sqlgen/verified.py).
                [
                    (str(q), bind(str(s), find_period(str(q), today, cal), today))
                    for q, s in examples
                ],
                definitions,
            )
        ),
        {d.id: d.source_id for d in datasets},
    )


_AGGREGATES = {"sum": "SUM({})", "count": "COUNT({})", "count_distinct": "COUNT(DISTINCT {})",
               "avg": "AVG({})", "min": "MIN({})", "max": "MAX({})"}  # fmt: skip


def _aggregated(expression: str, aggregation: str) -> str:
    """A measure as SQL: its expression under its aggregation, kept whole when it aggregates."""
    head = expression.strip().upper()
    if any(head.startswith(f"{a}(") for a in ("SUM", "COUNT", "AVG", "MIN", "MAX")):
        return expression.strip()
    return _AGGREGATES.get(aggregation, "SUM({})").format(expression.strip())


async def _definitions(
    session: AsyncSession, space_id: uuid.UUID, tables: list[Tbl]
) -> list[Definition]:
    """S2: the space's metrics (certified, and drafts) and measures as the SQL writer reads them:
    each measure's SQL on its table with the time column it is read along, a semi-additive one
    read at the period's end, a metric's formula over its measures with its filters. Only on
    the tables this asker may read (the caller's `tables`)."""
    ctx = context
    by_id = {t.dataset_id: t for t in tables}
    entities = {
        e.id: e
        for e in (
            await session.scalars(
                select(ctx.SemanticEntity).where(ctx.SemanticEntity.data_space_id == space_id)
            )
        ).all()
    }
    dims = (
        await session.scalars(
            select(ctx.SemanticDimension).where(ctx.SemanticDimension.data_space_id == space_id)
        )
    ).all()
    time_of = {d.entity_id: d.expression.strip('"') for d in dims if d.is_time}
    dim_name = {d.code: (d.name, d.expression) for d in dims}
    measures = {
        m.code: m
        for m in (
            await session.scalars(
                select(ctx.SemanticMeasure).where(ctx.SemanticMeasure.data_space_id == space_id)
            )
        ).all()
    }
    synonyms: dict[uuid.UUID, list[str]] = defaultdict(list)
    for target, phrase in (
        await session.execute(
            select(ctx.Synonym.target_id, ctx.Synonym.phrase_norm).where(
                ctx.Synonym.data_space_id == space_id,
                ctx.Synonym.status == "active",
                ctx.Synonym.target_kind == "metric",
            )
        )
    ).all():
        synonyms[target].append(phrase)

    def measure_sql(code: str) -> tuple[str, uuid.UUID] | None:
        m = measures.get(code)
        e = entities.get(m.entity_id) if m is not None else None
        if m is None or e is None or e.primary_dataset_id not in by_id:
            return None
        table = by_id[e.primary_dataset_id]
        when = time_of.get(e.id)
        text = f"{_aggregated(m.expression, m.aggregation)} FROM {table.qualified}"
        if when:
            text += f" (period on {when})"
        if m.additivity == "semi":
            text += " — a stock: read on the last date of the period, summed over that date only"
        return text, table.dataset_id

    out: list[Definition] = []
    for code, m in measures.items():
        found = measure_sql(code)
        if found is not None:
            text, dataset = found
            out.append(
                Definition(m.name, (norm(m.name),), frozenset({dataset}), f"{m.name} = {text}")
            )
    metrics = (
        await session.scalars(
            select(ctx.Metric).where(
                ctx.Metric.data_space_id == space_id, ctx.Metric.status != "retired"
            )
        )
    ).all()
    for metric in metrics:
        row = await session.scalar(
            select(ctx.MetricVersion)
            .where(ctx.MetricVersion.metric_id == metric.id, ctx.MetricVersion.status != "broken")
            .order_by(
                (ctx.MetricVersion.status == "certified").desc(), ctx.MetricVersion.version.desc()
            )
            .limit(1)
        )
        if row is None:
            continue
        parts: list[str] = []
        datasets: set[uuid.UUID] = set()
        ok = True
        for code in sorted({w for w in _words(row.expression) if w in measures}):
            found = measure_sql(code)
            if found is None:
                ok = False
                break
            parts.append(f"{code} = {found[0]}")
            datasets.add(found[1])
        if not ok or not parts:
            continue
        filters = [
            f"{dim_name.get(str(f.get('dimension')), (f.get('dimension'), ''))[1] or f.get('dimension')}"
            f" {f.get('op')} {f.get('values')}"
            for f in (row.filters or [])
            if isinstance(f, dict)
        ]
        text = f"{row.display_name} = {row.expression}, where " + "; ".join(parts)
        if filters:
            text += "; always filtered: " + "; ".join(filters)
        if row.period_semantics == "end_of_period":
            text += "; read at the period's last date"
        if row.unit:
            text += f"; unit {row.unit}"
        phrases = (norm(row.display_name), *synonyms.get(metric.id, []))
        out.append(Definition(row.display_name, phrases, frozenset(datasets), text))
    return out


def _words(expression: str) -> list[str]:
    import re

    return re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expression)
