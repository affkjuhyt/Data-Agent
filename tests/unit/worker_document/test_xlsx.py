"""DI-10: an XLSX workbook read as tables (xlsx_path, §4.5): one table per sheet, numbers from the
cells' types, merged ranges as spans, the header rows, the limits that keep a workbook bounded."""

from __future__ import annotations

import decimal
import io

import pytest

openpyxl = pytest.importorskip("openpyxl")

from services.worker_document import xlsx_path  # noqa: E402

pytestmark = [pytest.mark.unit]


def _book(*sheets: tuple[str, list[list[object]]], merge: str | None = None) -> bytes:
    book = openpyxl.Workbook()
    book.remove(book.active)
    for title, rows in sheets:
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(row)
        if merge:
            sheet.merge_cells(merge)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _read(content: bytes) -> xlsx_path.Extraction:
    return xlsx_path.extract(content, mask=lambda t: t, title="Số liệu", doc_no=None)


def test_each_sheet_is_a_table_with_numbers_from_its_cells() -> None:
    got = _read(
        _book(
            ("Dư nợ", [["Năm 2024", None], ["Kế hoạch", "Thực hiện"], [1234.5, 1300]]),
            ("Ghi chú", [["Chỉ là văn bản"]]),
            merge="A1:B1",
        )
    )
    assert [e.kind for e in got.elements] == ["table", "table"]
    assert [e.page_no for e in got.elements] == [1, 2]
    first = got.tables[0]
    assert (first.caption, first.n_rows, first.n_cols, first.header_rows) == ("Dư nợ", 3, 2, 2)
    header = next(c for c in first.cells if (c.row_idx, c.col_idx) == (0, 0))
    assert (header.text, header.col_span, header.is_header) == ("Năm 2024", 2, True)
    figures = sorted(c.numeric_value for c in first.cells if c.numeric_value is not None)
    assert figures == [decimal.Decimal("1234.5"), decimal.Decimal(1300)]
    assert got.extractor == "openpyxl" and got.extractor_version == xlsx_path.EXTRACTOR_VERSION
    # One summary chunk per sheet, never a figure in it.
    assert [c.kind for c in got.chunks] == ["table", "table"]
    assert not any("1234.5" in c.text or "1300" in c.text for c in got.chunks)


def test_a_masked_cell_is_no_figure() -> None:
    content = _book(("Liên hệ", [["Số CCCD", "Dư nợ"], ["012345678901", 50]]))
    got = xlsx_path.extract(
        content,
        mask=lambda t: "[đã che]" if t == "012345678901" else t,
        title=None,
        doc_no=None,
    )
    cells = {(c.row_idx, c.col_idx): c for c in got.tables[0].cells}
    assert (cells[(1, 0)].text, cells[(1, 0)].numeric_value) == ("[đã che]", None)
    assert cells[(1, 1)].numeric_value == decimal.Decimal(50)


def test_a_sheet_over_the_cell_limit_is_left_out_with_an_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(xlsx_path, "MAX_SHEET_CELLS", 4)
    got = _read(_book(("Nhỏ", [["a", 1]]), ("Lớn", [[1, 2, 3], [4, 5, 6]])))
    assert [t.caption for t in got.tables] == ["Nhỏ"]
    (issue,) = got.tables[0].issues
    assert issue.detail == {"reason": "sheet_too_large", "sheet": "Lớn", "cells": 6}


def test_a_workbook_that_is_too_large_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    content = _book(("A", [[1, 2], [3, 4]]))
    monkeypatch.setattr(xlsx_path, "MAX_BOOK_CELLS", 3)
    with pytest.raises(ValueError, match="1 000 000 cells"):
        _read(content)
    monkeypatch.setattr(xlsx_path, "MAX_BOOK_CELLS", 10**6)
    monkeypatch.setattr(xlsx_path, "MAX_XLSX_INFLATED", 10)
    with pytest.raises(ValueError, match="inflates"):
        _read(content)
