"""An XLSX workbook read as tables, deterministically (DI-10, DOCUMENT_INTELLIGENCE_SPEC §4.5).

Each sheet is one `table` element (page_no = its position in the workbook, from 1) with its
doc_table (caption = the sheet's name) and table_cell rows: a cell's value as computed by the
workbook (formulas read with data_only, their cached result), its number from the cell's type
rather than its text, merged ranges as spans. The header rows are the leading rows that hold no
number. Each sheet gets a table summary chunk (chunker, §5.4); nothing of a sheet is embedded as
text, and no model reads it.

Bounded like a DOCX (a zip): the file at 50 MB, what it inflates to at 300 MB, a sheet at
200 000 cells; a sheet over that is left out with an issue (field_mismatch, reason
sheet_too_large), the others are read.
"""

from __future__ import annotations

import datetime
import decimal
import io
import zipfile
from collections.abc import Callable
from typing import Any

from services.worker_document import chunker
from services.worker_document.docling_adapter import Element
from services.worker_document.docling_path import Extraction
from services.worker_document.processing import ExtractedPage, LayoutBlock
from services.worker_document.tables import Cell, Issue, Table

EXTRACTOR_VERSION = "openpyxl-3.1.5+xlsx-1"
MAX_XLSX_BYTES = 50 * 1024 * 1024
MAX_XLSX_INFLATED = 300 * 1024 * 1024
MAX_SHEET_CELLS = 200_000
# The whole workbook is loaded to read merged ranges; past this many cells it is not.
MAX_BOOK_CELLS = 1_000_000


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
    # The sheets' sizes first, read cheaply (read_only reads each sheet's dimension), so a
    # workbook too large to load is refused before it is loaded.
    sizing = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
    try:
        sizes = [(s.max_row or 0) * (s.max_column or 0) for s in sizing.worksheets]
    finally:
        sizing.close()
    if sum(n for n in sizes if n <= MAX_SHEET_CELLS) > MAX_BOOK_CELLS:
        raise ValueError("The workbook holds more than 1 000 000 cells")
    book = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=False)
    elements: list[Element] = []
    tables: list[Table] = []
    pages: list[ExtractedPage] = []
    issues: list[Issue] = []
    for index, sheet in enumerate(book.worksheets, start=1):
        rows, cols = sheet.max_row or 0, sheet.max_column or 0
        if rows * cols > MAX_SHEET_CELLS:
            issues.append(
                Issue(
                    "field_mismatch",
                    "warning",
                    index,
                    {"reason": "sheet_too_large", "sheet": sheet.title, "cells": rows * cols},
                )
            )
            continue
        ref = f"#/sheets/{index - 1}"
        element = Element(
            ref=ref,
            order_idx=len(elements),
            kind="table",
            text="",
            page_no=index,
            extractor_version=EXTRACTOR_VERSION,
        )
        table = _table(sheet, ref, index, rows, cols, mask)
        if not table.cells:
            continue
        elements.append(element)
        tables.append(table)
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


def _table(
    sheet: Any, ref: str, page: int, rows: int, cols: int, mask: Callable[[str], str]
) -> Table:
    covered: dict[tuple[int, int], tuple[int, int]] = {}
    spans: dict[tuple[int, int], tuple[int, int]] = {}
    for merged in sheet.merged_cells.ranges:
        top, left = merged.min_row - 1, merged.min_col - 1
        spans[(top, left)] = (
            merged.max_row - merged.min_row + 1,
            merged.max_col - merged.min_col + 1,
        )
        for r in range(merged.min_row - 1, merged.max_row):
            for c in range(merged.min_col - 1, merged.max_col):
                if (r, c) != (top, left):
                    covered[(r, c)] = (top, left)
    cells: list[Cell] = []
    for r, row in enumerate(sheet.iter_rows(min_row=1, max_row=rows, max_col=cols)):
        for c, cell in enumerate(row):
            if (r, c) in covered or cell.value is None:
                continue
            text, number = _value(cell.value)
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
        caption=mask(sheet.title),
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
