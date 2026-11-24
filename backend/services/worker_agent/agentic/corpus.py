"""Data Space corpus for the agentic planner: tables, metrics, documents and pages the asker may read."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db.models import context, document
from libs.dataagent_db.session import apply_app_settings, reader_settings_for
from services.worker_agent.semantic.loader import SpaceLayer
from services.worker_agent.sqlgen.space import Space, load_space
from services.worker_agent.understand import analyst

_MAX_TABLES = 30
_MAX_COLUMNS = 40
_MAX_DOCUMENTS = 20
_MAX_PAGES = 20


@dataclass(frozen=True)
class CorpusTable:
    qualified: str
    label: str
    description: str | None
    columns: tuple[str, ...]


@dataclass(frozen=True)
class CorpusMetric:
    code: str
    name: str
    certified: bool


@dataclass(frozen=True)
class CorpusDoc:
    name: str
    media_kinds: tuple[str, ...]


@dataclass(frozen=True)
class CorpusPage:
    title: str
    kind: str
    summary: str


@dataclass(frozen=True)
class CorpusBrief:
    tables: tuple[CorpusTable, ...]
    joins: tuple[str, ...]
    metrics: tuple[CorpusMetric, ...]
    dimensions: tuple[str, ...]
    documents: tuple[CorpusDoc, ...]
    pages: tuple[CorpusPage, ...]


def _section(heading: str, lines: list[str]) -> str:
    body = "\n".join(lines) if lines else "không có"
    return f"{heading}:\n{body}"


def _table_lines(table: CorpusTable) -> list[str]:
    lines = [f"{table.qualified} ({table.label})"]
    if table.description:
        lines.append(f"  {table.description}")
    for col in table.columns[:_MAX_COLUMNS]:
        lines.append(f"  {col}")
    return lines


def render_corpus(brief: CorpusBrief) -> str:
    table_lines: list[str] = []
    for table in brief.tables[:_MAX_TABLES]:
        table_lines.extend(_table_lines(table))

    certified = [f"{m.code}: {m.name}" for m in brief.metrics if m.certified]
    drafts = [f"{m.code}: {m.name} (chưa chứng nhận)" for m in brief.metrics if not m.certified]

    doc_lines = [
        f"{doc.name} ({', '.join(doc.media_kinds)})" for doc in brief.documents[:_MAX_DOCUMENTS]
    ]
    page_lines = [
        f"{page.title} [{page.kind}]: {page.summary}" for page in brief.pages[:_MAX_PAGES]
    ]

    sections = [
        _section("Bảng", table_lines),
        _section("Quan hệ", list(brief.joins)),
        _section("Chỉ số đã chứng nhận", certified),
        _section("Chỉ số nháp", drafts),
        _section("Chiều", list(brief.dimensions)),
        _section("Tài liệu", doc_lines),
        _section("Trang tri thức", page_lines),
    ]
    return "\n".join(sections)


def build_corpus(
    space: Space,
    *,
    certified: tuple[CorpusMetric, ...],
    drafts: tuple[CorpusMetric, ...],
    dimensions: tuple[str, ...],
    documents: tuple[CorpusDoc, ...],
    pages: tuple[CorpusPage, ...],
) -> CorpusBrief:
    by_id = {table.dataset_id: table for table in space.schema.tables}
    tables = tuple(
        CorpusTable(
            table.qualified,
            table.label,
            table.description,
            tuple(f"{col.name} ({col.kind}): {col.label}" for col in table.columns),
        )
        for table in space.schema.tables
    )
    joins: list[str] = []
    for fk in space.schema.fks:
        src = by_id.get(fk.from_table)
        dst = by_id.get(fk.to_table)
        # load_space already dropped a table the asker may not read (personal, policy, or only
        # pii columns). A join that still names it would put that table back into the brief.
        if src is None or dst is None:
            continue
        joins.append(
            f"{src.qualified}.{', '.join(fk.from_columns)} = "
            f"{dst.qualified}.{', '.join(fk.to_columns)}"
        )
    return CorpusBrief(
        tables=tables,
        joins=tuple(joins),
        metrics=certified + drafts,
        dimensions=dimensions,
        documents=documents,
        pages=pages,
    )


async def load_corpus(
    session: AsyncSession,
    space_id: uuid.UUID,
    principal_id: uuid.UUID,
    layer: SpaceLayer,
    *,
    purpose: str | None = None,
) -> CorpusBrief:
    # The question's purpose, as the SQL path's: an extracted table of a collection of another
    # purpose is not listed to the planner either (security review of 25/11).
    space = await load_space(session, space_id, principal_id, purpose=purpose)
    # _visible is a version in force that is not blocked. The loader can leave that version
    # uncertified, and a draft row is not in the set at all. Only entry.certified is the
    # certified shelf; both other kinds are listed chưa chứng nhận.
    visible = analyst._visible(layer)
    certified = tuple(
        CorpusMetric(code, entry.name, True) for code, entry in visible.items() if entry.certified
    )
    uncertified = tuple(
        CorpusMetric(code, entry.name, False)
        for code, entry in visible.items()
        if not entry.certified
    )
    latest_version = (
        select(
            context.MetricVersion.metric_id.label("metric_id"),
            func.max(context.MetricVersion.version).label("version"),
        )
        .group_by(context.MetricVersion.metric_id)
        .subquery()
    )
    draft_rows = (
        await session.execute(
            select(context.Metric.code, context.MetricVersion.display_name)
            .join(latest_version, latest_version.c.metric_id == context.Metric.id)
            .join(
                context.MetricVersion,
                (context.MetricVersion.metric_id == context.Metric.id)
                & (context.MetricVersion.version == latest_version.c.version),
            )
            .where(
                context.Metric.data_space_id == space_id,
                context.Metric.status == "draft",
            )
        )
    ).all()
    drafts = uncertified + tuple(
        CorpusMetric(code, display_name, False) for code, display_name in draft_rows
    )
    dimensions = tuple(f"{item.code}: {item.name}" for item in layer.vocabulary.dimensions)
    collections = (
        await session.scalars(
            select(document.Collection).where(
                document.Collection.data_space_id == space_id,
                document.Collection.status == "active",
                document.Collection.deleted_at.is_(None),
            )
        )
    ).all()
    # svc_qa is NOBYPASSRLS. knowledge_reader_allows is false until this pair is set, which
    # hides every page. The asked space is the one this transaction may read (prepare already
    # checked can_ask); clearance is the same internal rank reader_settings sets. The select
    # stays on this role. A page whose sources sit outside this space stays hidden.
    await apply_app_settings(session, reader_settings_for([space_id]))
    knowledge_pages = (
        await session.scalars(
            select(context.KnowledgePage).where(
                context.KnowledgePage.data_space_id == space_id,
                context.KnowledgePage.status == "active",
            )
        )
    ).all()
    return build_corpus(
        space,
        certified=certified,
        drafts=drafts,
        dimensions=dimensions,
        documents=tuple(CorpusDoc(row.name, tuple(row.media_kinds)) for row in collections),
        pages=tuple(CorpusPage(row.title, row.kind, row.index_summary) for row in knowledge_pages),
    )
