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


def _forged(content: bytes, *, dimension: str, merge: str) -> bytes:
    """The workbook with its first sheet claiming another size and one huge merged range."""
    import re
    import zipfile

    source, out = zipfile.ZipFile(io.BytesIO(content)), io.BytesIO()
    with zipfile.ZipFile(out, "w") as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                xml = data.decode()
                xml = re.sub(r'<dimension ref="[^"]*"/>', f'<dimension ref="{dimension}"/>', xml)
                xml = re.sub(r"<mergeCells.*?</mergeCells>", "", xml, flags=re.S)
                xml = xml.replace(
                    "</sheetData>",
                    f'</sheetData><mergeCells count="1"><mergeCell ref="{merge}"/></mergeCells>',
                )
                data = xml.encode()
            target.writestr(item, data)
    return out.getvalue()


def test_a_stated_size_and_a_huge_merge_are_not_trusted() -> None:
    import time

    content = _forged(
        _book(("A", [["Tiêu đề", None], ["x", 1], ["y", 2]])),
        dimension="A1:XFD1048576",
        merge="A1:XFD1048576",
    )
    started = time.monotonic()
    got = _read(content)
    assert time.monotonic() - started < 5
    table = got.tables[0]
    # The sheet holds 3 rows and 2 columns, whatever it states; the merge is clamped to them.
    assert (table.n_rows, table.n_cols) == (3, 2)
    (only,) = table.cells
    assert (only.text, only.row_span, only.col_span) == ("Tiêu đề", 3, 2)


def test_the_walk_is_bounded_across_sheets_and_overlapping_merges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Many sheets each over its own limit still count against the workbook's (security review).
    monkeypatch.setattr(xlsx_path, "MAX_SHEET_CELLS", 3)
    monkeypatch.setattr(xlsx_path, "MAX_BOOK_CELLS", 10)
    big = [[1, 2], [3, 4]]
    with pytest.raises(ValueError, match="1 000 000 cells"):
        _read(_book(*[(f"S{n}", big) for n in range(4)]))
    monkeypatch.setattr(xlsx_path, "MAX_SHEETS", 2)
    monkeypatch.setattr(xlsx_path, "MAX_BOOK_CELLS", 10**6)
    with pytest.raises(ValueError, match="more than 2 sheets"):
        _read(_book(("A", [[1]]), ("B", [[1]]), ("C", [[1]])))


def test_the_same_merge_stated_many_times_is_walked_once_within_the_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(xlsx_path, "MAX_SHEET_CELLS", 6)
    values = {(r, c): "x" for r in range(2) for c in range(3)}
    # Six cells each time: the first is taken, the next would pass the limit and is skipped.
    table = xlsx_path._table("A", values, [(0, 0, 1, 2)] * 1000, "#/sheets/0", 1, lambda t: t)
    (only,) = table.cells
    assert (only.row_span, only.col_span) == (2, 3)
