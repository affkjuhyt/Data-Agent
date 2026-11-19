"""Data Space corpus for the agentic planner: tables, metrics, documents and pages the asker may read."""

from __future__ import annotations

from dataclasses import dataclass

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
