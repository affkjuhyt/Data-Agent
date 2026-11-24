"""A quantity the semantic layer defines but no certified metric answers, read and compiled by the
layer's own definitions (S1, 24/11/2026), before any model writes SQL.

The exploratory SQL path was wrong on more than half of what it answered, and the trace showed
why (CONFIDENCE_SPEC §4, the 23/11 ablation): every candidate of a question saw the same tables
the lexical linker chose and made the same mistake. "Doanh thu năm 2013" summed the sales quota
table, "Doanh thu đại lý năm 2012" a reseller's self-declared annual revenue, "Biên lợi nhuận" came
back as an amount, an end-of-month stock as one row per product. Each of those definitions is in
the semantic layer: a measure's expression on its fact table, a metric's formula, a semi-additive
period semantics, the joins. The governed path reads them right (45/46); the SQL path never saw
them.

So a question the certified metrics do not place is read, by the same rules, against what else
the layer defines:
* a metric whose version is not certified yet (a draft: DS-TLC's 13 metrics on 24/11);
* a measure no metric is built on ("Giá vốn Internet", "Phí vận chuyển Internet"), as a metric of
  that one measure, summed over the period (or read at its end, for a semi-additive one).
What is placed is compiled by the compiler, like a certified metric, and answered with the
exploratory label: the definition is the layer's, the certification is not there.

Only what the asker may read is offered: a measure whose table is withheld from them is not.
"""

from __future__ import annotations

import datetime
import re
import uuid
from dataclasses import dataclass, replace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db.models import context
from services.worker_agent.semantic import compiler as c
from services.worker_agent.semantic.loader import SpaceLayer
from services.worker_agent.understand.resolver import (
    MetricEntry,
    Understanding,
    Vocabulary,
    understand,
)

# The code an uncertified measure is known by: never a metric code of the space.
MEASURE_PREFIX = "measure:"


@dataclass(frozen=True)
class Uncertified:
    entry: MetricEntry
    version: c.MetricVersion
    row: context.MetricVersion
    kind: str  # "draft" or "measure"


@dataclass(frozen=True)
class Placed:
    understanding: Understanding
    found: Uncertified

    @property
    def note(self) -> str:
        name = self.found.entry.name
        if self.found.kind == "draft":
            return f"Chỉ số “{name}” chưa được chứng nhận: số tính theo định nghĩa đang chờ duyệt."
        return (
            f"Chưa có chỉ số chuẩn cho “{name}”: số tính theo định nghĩa của lớp ngữ nghĩa "
            "(measure), chưa qua chứng nhận."
        )


async def load_uncertified(
    session: AsyncSession, space_id: uuid.UUID, layer: SpaceLayer
) -> list[Uncertified]:
    """The draft metrics and the measures of the space the asker may read, as metrics."""
    ctx = context
    known = set(layer.versions)
    out: list[Uncertified] = []
    drafts = (
        await session.scalars(
            select(ctx.Metric).where(
                ctx.Metric.data_space_id == space_id,
                ctx.Metric.status != "active",
                ctx.Metric.status != "retired",
            )
        )
    ).all()
    readable = {d.dimension_id for d in layer.vocabulary.dimensions}
    for m in drafts:
        if m.id in known:
            continue
        row = await session.scalar(
            select(ctx.MetricVersion)
            .where(ctx.MetricVersion.metric_id == m.id, ctx.MetricVersion.status != "broken")
            .order_by(ctx.MetricVersion.version.desc())
            .limit(1)
        )
        if row is None or not _readable(row.expression, layer):
            continue
        version = c.MetricVersion(
            id=row.id,
            metric_code=m.code,
            display_name=row.display_name,
            expression=row.expression,
            filters=tuple(row.filters or ()),
            unit=row.unit,
            decimals=row.decimals,
            period_semantics=row.period_semantics,
            # Never wider than what the asker may read, and a draft that allows none allows none.
            allowed_dimension_ids=frozenset(row.allowed_dimensions or ()) & frozenset(readable),
            certified=False,
            time_grain=row.time_grain,
            time_dimension_id=row.time_dimension_id,
        )
        out.append(
            Uncertified(MetricEntry(m.id, m.code, row.display_name, False), version, row, "draft")
        )
    used = {v.expression.strip() for v in layer.versions.values()}
    measure_rows = (
        await session.scalars(
            select(ctx.SemanticMeasure).where(ctx.SemanticMeasure.data_space_id == space_id)
        )
    ).all()
    for ms in measure_rows:
        # A measure a certified metric is exactly is that metric's to answer.
        if ms.code in used or ms.code not in layer.layer.measures or not _readable(ms.code, layer):
            continue
        mid = uuid.uuid5(uuid.NAMESPACE_URL, f"{space_id}/{MEASURE_PREFIX}{ms.code}")
        semantics = "end_of_period" if ms.additivity == "semi" else "sum_over_period"
        version = c.MetricVersion(
            id=mid,
            metric_code=f"{MEASURE_PREFIX}{ms.code}",
            display_name=ms.name,
            expression=ms.code,
            filters=(),
            unit=ms.unit,
            decimals=2,
            period_semantics=semantics,
            allowed_dimension_ids=frozenset(readable),
            certified=False,
            time_grain="month",
        )
        # Not stored: the receipt reads a version row's fields, a measure has none of its own.
        row = context.MetricVersion(
            id=mid,
            metric_id=mid,
            version=0,
            display_name=ms.name,
            expression=ms.code,
            unit=ms.unit,
            decimals=2,
            period_semantics=semantics,
            status="draft",
            filters=[],
            allowed_dimensions=list(readable),
        )
        out.append(
            Uncertified(
                MetricEntry(mid, version.metric_code, ms.name, False), version, row, "measure"
            )
        )
    return out


def _readable(expression: str, layer: SpaceLayer) -> bool:
    """Every measure the expression names is in the asker's layer, on a table they may read,
    reading no column masked for them."""
    names = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expression))
    measures = [layer.layer.measures[n] for n in names if n in layer.layer.measures]
    if not measures:
        return False
    for m in measures:
        table = layer.layer.entity_tables.get(m.entity_id)
        if table is None or table.dataset_id not in layer.datasets:
            return False
        # A masked column is withheld from this asker, as loader.blocked_metric_ids withholds a
        # certified metric that reads one (the security review of 24/11).
        masked = layer.restricted_columns.get(table.dataset_id)
        if masked and set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", m.expression.lower())) & masked:
            return False
    return True


def read(
    asked: str, layer: SpaceLayer, found: list[Uncertified], *, today: datetime.date
) -> Placed | None:
    """The rule reading of the question against the uncertified quantities, with the space's own
    dimensions, values and synonyms: one placed, or nothing (a tie is not guessed between)."""
    if not found:
        return None
    vocab = layer.vocabulary
    extended = Vocabulary(
        metrics=[f.entry for f in found],
        dimensions=vocab.dimensions,
        values=vocab.values,
        synonyms=vocab.synonyms,
        calendar=vocab.calendar,
    )
    u = understand(asked, extended, today=today)
    if u.decision != "proceed" or u.metric is None:
        return None
    chosen = next(f for f in found if f.entry.metric_id == u.metric.metric_id)
    return Placed(u, chosen)


def admit(layer: SpaceLayer, placed: Placed) -> None:
    """Put the placed quantity in the layer for this turn, as the compile and receipt read it."""
    f = placed.found
    layer.versions[f.entry.metric_id] = f.version
    layer.version_rows[f.entry.metric_id] = f.row
    if all(m.metric_id != f.entry.metric_id for m in layer.vocabulary.metrics):
        layer.vocabulary = replace(layer.vocabulary, metrics=[*layer.vocabulary.metrics, f.entry])
