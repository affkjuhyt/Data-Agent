"""A number asked of a document, read from its table cell and cited to that cell (DI-12, §7).

Retrieval finds a table by its summary chunk (chunk.kind = table); the number never comes from
that chunk, it comes from document.table_cell by SQL: the table and the tables that continue it
on the next pages are read as one logical table (§4.3.1), each body row named by its label (the
first column) and each column by its header path (the header rows that cover it, top to bottom:
"Năm 2024 > Thực hiện"). The question picks a row and a column by the words they share with it,
diacritics or not (rapidfuzz over folded text); the cell where both meet, holding a number, is
the answer. Two cells nearly as good (within 10 %) with different numbers are not guessed between:
the question is asked back with both. Nothing matching is "not found", never a guess.

The answer carries the cell: table, row, column, page and box, so the citation opens the page
with that cell outlined (DocumentViewerPage).
"""

from __future__ import annotations

import decimal
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db.models import document as doc
from libs.language import fold

MAX_TABLES = 5
# A row or a column is named by the question when this much of its words is in it (0..100).
MATCH = 80
# The runner-up within this share of the best, with another number: asked back, not guessed.
TIE = 0.9
_WORD = re.compile(r"\w+")


@dataclass(frozen=True)
class Cell:
    row: int
    col: int
    row_span: int
    col_span: int
    text: str
    value: decimal.Decimal | None
    unit: str | None
    header: bool
    page: int | None
    bbox: dict[str, Any] | None


@dataclass
class Grid:
    """A logical table: one or more doc_table rows on consecutive pages, read as one."""

    table_id: uuid.UUID
    document_id: uuid.UUID
    caption: str | None
    header_rows: int
    cells: list[Cell] = field(default_factory=list)


@dataclass(frozen=True)
class Found:
    value: decimal.Decimal
    unit: str | None
    table_id: uuid.UUID
    document_id: uuid.UUID
    cell: Cell
    row_label: str
    column_label: str
    caption: str | None
    score: float


@dataclass(frozen=True)
class Ambiguous:
    options: list[Found]


def _folded(text: str) -> str:
    return " ".join(_WORD.findall(fold(text)))


def _score(label: str, question: str) -> float:
    """How much of a label the question names, 0..1: rapidfuzz's partial token-set ratio over
    folded words, so "doanh thu" in a long question names the row "Doanh thu"."""
    words = _folded(label)
    if not words:
        return 0.0
    ratio = fuzz.token_set_ratio(words, question)
    return ratio / 100 if ratio >= MATCH else 0.0


def _path_score(path: str, question: str) -> float:
    """A column's header path scored level by level, the mean of its levels: a question naming
    "năm 2024" alone fits "Năm 2024 > Kế hoạch" and "Năm 2024 > Thực hiện" equally (asked back),
    one naming "thực hiện năm 2024" fits the second fully."""
    levels = [p for p in path.split(" > ") if p.strip()]
    if not levels:
        return 0.0
    return sum(_score(level, question) for level in levels) / len(levels)


def _row_labels(grid: Grid) -> dict[int, str]:
    """Each body row's label: its first non-numeric cell from the left (the row header)."""
    labels: dict[int, str] = {}
    for cell in sorted(grid.cells, key=lambda c: (c.row, c.col)):
        if cell.row < grid.header_rows or cell.row in labels:
            continue
        if cell.value is None and cell.text:
            labels[cell.row] = cell.text
    return labels


def _column_labels(grid: Grid) -> dict[int, str]:
    """Each column's header path: the header cells that cover it, top to bottom (§4.3.2)."""
    paths: dict[int, list[str]] = {}
    for cell in sorted(grid.cells, key=lambda c: (c.row, c.col)):
        if cell.row >= grid.header_rows or not cell.text:
            continue
        for col in range(cell.col, cell.col + cell.col_span):
            paths.setdefault(col, []).append(cell.text)
    return {col: " > ".join(dict.fromkeys(parts)) for col, parts in paths.items()}


def answer_from_grids(question: str, grids: Sequence[Grid]) -> Found | Ambiguous | None:
    asked = _folded(question)
    candidates: list[Found] = []
    for grid in grids:
        rows, cols = _row_labels(grid), _column_labels(grid)
        row_scores = {r: s for r, label in rows.items() if (s := _score(label, asked)) > 0}
        # A table with one value column needs no column named: "doanh thu Hà Nội" in a two-
        # column table (label, value) is the row alone.
        value_cols = {
            c.col for c in grid.cells if c.value is not None and c.row >= grid.header_rows
        }
        col_scores = {c: s for c, label in cols.items() if (s := _path_score(label, asked)) > 0}
        if not col_scores and len(value_cols) == 1:
            col_scores = {next(iter(value_cols)): 1.0}
        if not row_scores or not col_scores:
            continue
        for cell in grid.cells:
            if cell.value is None or cell.header:
                continue
            if cell.row in row_scores and cell.col in col_scores:
                candidates.append(
                    Found(
                        value=cell.value,
                        unit=cell.unit,
                        table_id=grid.table_id,
                        document_id=grid.document_id,
                        cell=cell,
                        row_label=rows[cell.row],
                        column_label=cols.get(cell.col, ""),
                        caption=grid.caption,
                        score=row_scores[cell.row] * col_scores[cell.col],
                    )
                )
    if not candidates:
        return None
    candidates.sort(key=lambda f: -f.score)
    best = candidates[0]
    rivals = [f for f in candidates[1:] if f.score >= best.score * TIE and f.value != best.value]
    if rivals:
        return Ambiguous([best, rivals[0]])
    return best


async def load_grids(session: AsyncSession, table_ids: Sequence[uuid.UUID]) -> list[Grid]:
    """The tables, each with the tables that continue it, as logical grids (rows renumbered
    after the first part's; its header rows are the whole grid's)."""
    grids: list[Grid] = []
    loaded: set[uuid.UUID] = set()
    for table_id in list(dict.fromkeys(table_ids))[:MAX_TABLES]:
        first = await session.get(doc.DocTable, table_id)
        # A continuation found by its own summary reads from the head of its chain.
        hops = 0
        while first is not None and first.continues_table_id is not None and hops < 20:
            first = await session.get(doc.DocTable, first.continues_table_id)
            hops += 1
        if first is None or first.id in loaded:
            continue
        loaded.add(first.id)
        parts = [first]
        seen = {first.id}
        while len(parts) < 20:
            nxt = await session.scalar(
                select(doc.DocTable).where(doc.DocTable.continues_table_id == parts[-1].id)
            )
            if nxt is None or nxt.id in seen:
                break
            parts.append(nxt)
            seen.add(nxt.id)
        grid = Grid(first.id, first.document_id, first.caption, first.header_rows)
        offset = 0
        for n, part in enumerate(parts):
            rows = (
                await session.scalars(
                    select(doc.TableCell).where(doc.TableCell.table_id == part.id)
                )
            ).all()
            # A continuation's own header rows repeat the first part's: left out.
            skip = part.header_rows if n > 0 else 0
            for c in rows:
                if c.row_idx < skip:
                    continue
                grid.cells.append(
                    Cell(
                        row=c.row_idx - skip + offset,
                        col=c.col_idx,
                        row_span=c.row_span,
                        col_span=c.col_span,
                        text=c.text,
                        value=c.numeric_value,
                        unit=c.unit,
                        header=c.is_header and n == 0,
                        page=c.page_no,
                        bbox=c.bbox,
                    )
                )
            offset += part.n_rows - skip
        grids.append(grid)
    return grids


def spoken(value: decimal.Decimal, unit: str | None) -> str:
    """A figure as the sentence says it, in vi-VN marks; the cited cell keeps its own text."""
    from libs.language import number

    exponent = value.normalize().as_tuple().exponent
    decimals = max(0, -exponent) if isinstance(exponent, int) else 0
    text = number(value, min(decimals, 4), "vi-VN")
    return {"percent": f"{text}%", "VND": f"{text} đồng", "USD": f"{text} USD"}.get(
        unit or "", text
    )
