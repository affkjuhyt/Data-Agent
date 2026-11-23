"""An XLSX workbook read as tables, deterministically (DI-10, DOCUMENT_INTELLIGENCE_SPEC §4.5).

Each sheet is one `table` element (page_no = its position in the workbook, from 1) with its
doc_table (caption = the sheet's name) and table_cell rows: a cell's value as computed by the
workbook (formulas read with data_only, their cached result), its number from the cell's type
rather than its text, merged ranges as spans. The header rows are the leading rows that hold no
number. Each sheet gets a table summary chunk (chunker, §5.4); nothing of a sheet is embedded as
text, and no model reads it.

Bounded like a DOCX (a zip): the file at 50 MB, what it inflates to at 300 MB, a sheet at
200 000 cells; a sheet over that is left out with an issue (field_mismatch, reason
sheet_too_large), the others are read. The sizes a workbook states (<dimension>) are its own
claim, so nothing trusts them (security review): the sheets are streamed (openpyxl read_only, its
dimensions reset) with a counter of every row and cell walked that stops at the limit, and the
merged ranges are read from the sheet's XML here, at most MAX_MERGES of them and each clamped to
the cells the sheet holds; openpyxl never loads a workbook whole (it would make a cell of every
cell of a merged range).
"""

from __future__ import annotations

import datetime
import decimal
import io
import zipfile
from collections.abc import Callable
from typing import Any

from defusedxml import ElementTree

from services.worker_document import chunker
from services.worker_document.docling_adapter import Element
from services.worker_document.docling_path import Extraction
from services.worker_document.processing import ExtractedPage, LayoutBlock
from services.worker_document.tables import Cell, Issue, Table

EXTRACTOR_VERSION = "openpyxl-3.1.5+xlsx-1"
MAX_XLSX_BYTES = 50 * 1024 * 1024
MAX_XLSX_INFLATED = 300 * 1024 * 1024
MAX_SHEET_CELLS = 200_000
# Rows and cells walked in the whole workbook, the sheets over their own limit included.
MAX_BOOK_CELLS = 1_000_000
MAX_SHEETS = 200
MAX_MERGES = 10_000
_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


class _TooLarge(Exception):
    pass


def extract(
    content: bytes,
    *,
    mask: Callable[[str], str],
    title: str | None,
    doc_no: str | None,
) -> Extraction:
    import openpyxl

    if len(content) > MAX_XLSX_BYTES:
        raise ValueError("The workbook is larger than 50 MB")
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        if sum(info.file_size for info in archive.infolist()) > MAX_XLSX_INFLATED:
            raise ValueError("The workbook inflates past 300 MB")
    book = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    elements: list[Element] = []
    tables: list[Table] = []
    pages: list[ExtractedPage] = []
    issues: list[Issue] = []
    walked = 0
    try:
        if len(book.worksheets) > MAX_SHEETS:
            raise ValueError(f"The workbook has more than {MAX_SHEETS} sheets")
        for index, sheet in enumerate(book.worksheets, start=1):
            try:
                values, spent = _values(sheet)
            except _TooLarge as too_large:
                spent, values = int(str(too_large)), {}
                issues.append(
                    Issue(
                        "field_mismatch",
                        "warning",
                        index,
                        {"reason": "sheet_too_large", "sheet": sheet.title, "cells": spent},
                    )
                )
            # Every sheet's walk counts, one over its own limit included (security review).
            walked += spent
            if walked > MAX_BOOK_CELLS:
                raise ValueError("The workbook holds more than 1 000 000 cells")
            if not values:
                continue
            merges = _merges(content, getattr(sheet, "_worksheet_path", ""))
            ref = f"#/sheets/{index - 1}"
            elements.append(
                Element(
                    ref=ref,
                    order_idx=len(elements),
                    kind="table",
                    text="",
                    page_no=index,
                    extractor_version=EXTRACTOR_VERSION,
                )
            )
            tables.append(_table(sheet.title, values, merges, ref, index, mask))
            pages.append(
                ExtractedPage(
                    text=mask(sheet.title),
                    image=None,
                    text_layer=True,
                    ocr_conf=None,
                    blocks=[LayoutBlock("table", mask(sheet.title), (0.0, 0.0, 0.0, 0.0))],
                    images=[],
                    width=0.0,
                    height=0.0,
                )
            )
    finally:
        book.close()
    if tables:
        tables[0].issues.extend(issues)
    elif issues:
        raise ValueError("Every sheet of the workbook is over 200 000 cells")
    if not pages:
        raise ValueError("The workbook has no cells")
    return Extraction(
        pages=pages,
        elements=elements,
        tables=tables,
        chunks=chunker.chunks_of(elements, tables, title=title, doc_no=doc_no),
        extractor_version=EXTRACTOR_VERSION,
        extractor="openpyxl",
    )


def _values(sheet: Any) -> tuple[dict[tuple[int, int], Any], int]:
    """The sheet's cells with a value, by (row, column) from 0. Every row and cell walked counts,
    the empty ones openpyxl fills in included, and the walk stops past MAX_SHEET_CELLS."""
    sheet.reset_dimensions()  # the stated size is a claim: rows are not padded to it
    walked = 0
    out: dict[tuple[int, int], Any] = {}
    for r, row in enumerate(sheet.iter_rows(values_only=True)):
        walked += max(len(row), 1)
        if walked > MAX_SHEET_CELLS:
            raise _TooLarge(walked)
        for c, value in enumerate(row):
            if value is not None:
                out[(r, c)] = value
    return out, walked


def _merges(content: bytes, path: str) -> list[tuple[int, int, int, int]]:
    """The sheet's merged ranges as (top, left, bottom, right), 0-based and inclusive, read from
    its XML: at most MAX_MERGES, and never by openpyxl (it would make a cell of each)."""
    from openpyxl.utils.cell import range_boundaries

    out: list[tuple[int, int, int, int]] = []
    if not path:
        return out
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        try:
            source = archive.open(path.lstrip("/"))
        except KeyError:
            return out
        with source:
            parents: list[Any] = []
            for event, node in ElementTree.iterparse(source, events=("start", "end")):
                if event == "start":
                    parents.append(node)
                    continue
                parents.pop()
                if node.tag == f"{_MAIN}mergeCell":
                    try:
                        left, top, right, bottom = range_boundaries(node.get("ref", ""))
                    except (TypeError, ValueError):
                        left = None
                    if left is not None and None not in (top, right, bottom):
                        out.append((top - 1, left - 1, bottom - 1, right - 1))
                    if len(out) >= MAX_MERGES:
                        break
                elif node.tag == f"{_MAIN}mergeCells":
                    break  # one mergeCells per sheet: nothing after it is read
                # Removed from its parent as soon as read, so a sheet of millions of cells is
                # parsed in constant memory (clear() alone left the emptied nodes attached).
                if parents:
                    parents[-1].remove(node)
    return out


def _table(
    title: str,
    values: dict[tuple[int, int], Any],
    merges: list[tuple[int, int, int, int]],
    ref: str,
    page: int,
    mask: Callable[[str], str],
) -> Table:
    rows = max(r for r, _ in values) + 1
    cols = max(c for _, c in values) + 1
    covered: set[tuple[int, int]] = set()
    spans: dict[tuple[int, int], tuple[int, int]] = {}
    spent = 0
    for top, left, bottom, right in merges:
        # Clamped to the cells the sheet holds: a stated range may cover the whole sheet.
        bottom, right = min(bottom, rows - 1), min(right, cols - 1)
        if top >= rows or left >= cols or (bottom, right) == (top, left):
            continue
        area = (bottom - top + 1) * (right - left + 1)
        # The held cells bound the rows and columns, not their product, and overlapping ranges
        # would walk the same cells again: the cells walked for merges are bounded on their own,
        # a range past it is read as unmerged (security review).
        if spent + area > MAX_SHEET_CELLS:
            continue
        spent += area
        spans[(top, left)] = (bottom - top + 1, right - left + 1)
        for r in range(top, bottom + 1):
            for c in range(left, right + 1):
                if (r, c) != (top, left):
                    covered.add((r, c))
    cells: list[Cell] = []
    for (r, c), value in sorted(values.items()):
        if (r, c) in covered:
            continue
        text, number = _value(value)
        masked = mask(text)
        row_span, col_span = spans.get((r, c), (1, 1))
        cells.append(
            Cell(
                row_idx=r,
                col_idx=c,
                row_span=row_span,
                col_span=col_span,
                text=masked,
                is_header=False,
                # A masked value is no figure (an ID number is not a quantity).
                numeric_value=number if masked == text else None,
                page_no=page,
            )
        )
    header_rows = _header_rows(cells)
    for cell in cells:
        cell.is_header = cell.row_idx < header_rows
    body = [c for c in cells if not c.is_header and c.text]
    numeric = [c for c in body if c.numeric_value is not None]
    return Table(
        element_ref=ref,
        page_from=page,
        page_to=page,
        n_rows=rows,
        n_cols=cols,
        header_rows=header_rows,
        caption=mask(title),
        cells=cells,
        numeric_share=(
            (decimal.Decimal(len(numeric)) / len(body)).quantize(decimal.Decimal("0.0001"))
            if body
            else None
        ),
    )


def _value(value: Any) -> tuple[str, decimal.Decimal | None]:
    """A cell's text and, from its type, its number (a bool or a date is not one)."""
    if isinstance(value, bool):
        return ("TRUE" if value else "FALSE"), None
    if isinstance(value, int | float | decimal.Decimal):
        number = (
            decimal.Decimal(repr(value)) if isinstance(value, float) else decimal.Decimal(value)
        )
        return str(value), number if number.is_finite() else None
    if isinstance(value, datetime.datetime | datetime.date):
        return value.isoformat(), None
    return str(value).strip(), None


def _header_rows(cells: list[Cell]) -> int:
    """§4.5: the leading rows with no number, when a later row has one."""
    by_row: dict[int, list[Cell]] = {}
    for c in cells:
        by_row.setdefault(c.row_idx, []).append(c)
    order = sorted(by_row)
    count = 0
    for r in order:
        if any(c.numeric_value is not None for c in by_row[r]):
            break
        count += 1
    # The row index the body starts at (a sheet may open with empty rows).
    return order[count] if 0 < count < len(order) else 0
