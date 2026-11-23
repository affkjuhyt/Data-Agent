"""DI-07: tables.py reads a document's tables into cells with their numbers (§4.3, §4.4): spans,
headers, the scale a caption or header states, a table that goes on from the previous page, and
the issues it raises rather than guesses (two scales, a total that is not the sum)."""

from __future__ import annotations

import decimal
import json
import pathlib

import pytest

pytest.importorskip("docling_core")

from docling_core.types.doc import (  # noqa: E402
    BoundingBox,
    DocItemLabel,
    DoclingDocument,
    ProvenanceItem,
    TableCell,
    TableData,
)

from services.worker_document import docling_adapter as da  # noqa: E402
from services.worker_document import tables as t  # noqa: E402

pytestmark = [pytest.mark.unit]

D = decimal.Decimal
FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "docling"


def _fixture(name: str) -> DoclingDocument:
    return DoclingDocument.model_validate(json.loads((FIXTURES / name).read_text("utf-8")))


def _read(doc: DoclingDocument, **kw: object) -> list[t.Table]:
    return t.tables_of(doc, da.elements_of(doc).elements, **kw)  # type: ignore[arg-type]


def _grid(rows: list[list[str]], *, header_rows: int = 1) -> TableData:
    cells = [
        TableCell(
            text=text,
            start_row_offset_idx=r,
            end_row_offset_idx=r + 1,
            start_col_offset_idx=c,
            end_col_offset_idx=c + 1,
            column_header=r < header_rows,
        )
        for r, row in enumerate(rows)
        for c, text in enumerate(row)
    ]
    return TableData(table_cells=cells, num_rows=len(rows), num_cols=len(rows[0]))


def _prov(page: int) -> ProvenanceItem:
    return ProvenanceItem(page_no=page, bbox=BoundingBox(l=0, t=100, r=100, b=0), charspan=(0, 0))


def _cell(table: t.Table, row: int, col: int) -> t.Cell:
    return next(c for c in table.cells if (c.row_idx, c.col_idx) == (row, col))


def test_a_docx_table_keeps_its_merged_header_and_reads_millions_from_its_caption() -> None:
    (table,) = _read(_fixture("rules_docx.json"), decimal_mark=",")
    assert (table.n_rows, table.n_cols, table.header_rows) == (3, 3, 1)
    assert table.caption == "Bảng 1. Dư nợ theo kỳ (triệu đồng)"
    year = _cell(table, 0, 1)
    assert (year.text, year.col_span, year.row_span, year.is_header) == ("Năm 2024", 2, 1, True)
    # "1.234" in vi-VN is one thousand two hundred thirty-four, counted in millions of đồng.
    plan, done = _cell(table, 2, 1), _cell(table, 2, 2)
    assert (plan.numeric_value, plan.unit) == (D(1234) * 10**6, "VND")
    assert (done.numeric_value, done.unit) == (D(1300) * 10**6, "VND")
    assert _cell(table, 2, 0).numeric_value is None  # "Dư nợ" is a label
    assert table.page_from is None and table.issues == []
    assert table.numeric_share == D("0.4000")  # 2 figures of the 5 body cells with text


def test_a_scanned_table_finds_its_header_row_and_keeps_its_page_and_boxes() -> None:
    (table,) = _read(_fixture("scanned_table_pdf.json"), decimal_mark=",")
    # TableFormer marked no column header: the row without figures is the header.
    assert table.header_rows == 1 and _cell(table, 0, 2).is_header
    assert [c.numeric_value for c in table.cells if c.row_idx == 1] == [
        None,
        D(1234),
        D(1456),
        D(1789),
    ]
    assert table.page_from == table.page_to == 1
    figure = _cell(table, 3, 3)
    assert (figure.text, figure.numeric_value, figure.page_no) == ("787", D(787), 1)
    assert figure.bbox is not None and figure.bbox["coord_origin"] in ("TOPLEFT", "BOTTOMLEFT")


def test_a_table_on_the_next_page_with_the_same_columns_continues_the_first() -> None:
    doc = DoclingDocument(name="x")
    doc.add_table(data=_grid([["Chỉ tiêu", "2024"], ["A", "1"]]), prov=_prov(1))
    doc.add_table(data=_grid([["B", "2"], ["C", "3"]], header_rows=0), prov=_prov(2))
    first, second = _read(doc, decimal_mark=",")
    assert first.continues is None and second.continues == 0


@pytest.mark.parametrize(
    "between",
    ["heading", "other_columns", "page_after_next", "other_header"],
)
def test_a_table_does_not_continue_across_a_heading_other_columns_or_a_gap(between: str) -> None:
    doc = DoclingDocument(name="x")
    doc.add_table(data=_grid([["Chỉ tiêu", "2024"], ["A", "1"]]), prov=_prov(1))
    if between == "heading":
        doc.add_heading("Điều 2. Chi phí", prov=_prov(2))
    rows = [["B", "2"], ["C", "3"]]
    if between == "other_columns":
        rows = [["B", "2", "x"], ["C", "3", "y"]]
    header = 0
    if between == "other_header":
        rows, header = [["Khoản mục", "Số tiền"], ["B", "2"]], 1
    page = 3 if between == "page_after_next" else 2
    doc.add_table(data=_grid(rows, header_rows=header), prov=_prov(page))
    assert _read(doc, decimal_mark=",")[1].continues is None


def test_two_scales_for_one_column_multiply_nothing_and_raise_an_issue() -> None:
    doc = DoclingDocument(name="x")
    caption = doc.add_text(label=DocItemLabel.CAPTION, text="Kết quả (triệu đồng)")
    doc.add_table(
        data=_grid([["Chỉ tiêu", "Doanh thu (nghìn đồng)", "Lợi nhuận"], ["A", "5", "7"]]),
        caption=caption,
    )
    (table,) = _read(doc, decimal_mark=",")
    revenue, profit = _cell(table, 1, 1), _cell(table, 1, 2)
    assert (revenue.numeric_value, revenue.unit) == (D(5), None)
    assert (profit.numeric_value, profit.unit) == (D(7) * 10**6, "VND")
    (issue,) = table.issues
    assert (issue.kind, issue.severity, issue.detail["reason"], issue.detail["column"]) == (
        "field_mismatch",
        "warning",
        "scale_conflict",
        1,
    )


@pytest.mark.parametrize(("stated", "issues"), [("30", 0), ("31", 1), ("30,1", 0)])
def test_a_total_that_is_not_the_sum_of_its_rows_is_an_issue(stated: str, issues: int) -> None:
    doc = DoclingDocument(name="x")
    doc.add_table(
        data=_grid([["Khoản", "Số"], ["A", "10,0"], ["B", "20,0"], ["Tổng cộng", stated]])
    )
    (table,) = _read(doc, decimal_mark=",")
    found = [i for i in table.issues if i.detail["reason"] == "total_mismatch"]
    assert len(found) == issues
    if found:
        assert (found[0].detail["stated"], found[0].detail["summed"]) == ("31", "30.0")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.234", (D(1234), False)),
        ("(1.234)", (D(-1234), False)),
        ("\u22125", (D(-5), False)),
        ("12,5%", (D("12.5"), True)),
        ("1.234 đ", (D(1234), False)),
        ("-", None),
        ("—", None),
        ("", None),
        ("Q2", None),
    ],
)
def test_a_cell_reads_as_a_figure_or_as_none(
    text: str, expected: tuple[decimal.Decimal, bool] | None
) -> None:
    assert t.parse_number(text, ",") == expected


def test_a_figure_follows_the_decimal_mark_given_or_settled() -> None:
    assert t.parse_number("1,234.5", ".") == (D("1234.5"), False)
    doc = DoclingDocument(name="x")
    doc.add_table(data=_grid([["Item", "Value"], ["A", "1,234.5"], ["B", "2.25"]]))
    (table,) = _read(doc)  # no profile mark: the figures settle "."
    assert _cell(table, 1, 1).numeric_value == D("1234.5")


@pytest.mark.parametrize(
    ("text", "scale"),
    [
        ("Bảng 2. Dư nợ (triệu đồng)", (10**6, "VND")),
        ("Đơn vị: tỷ đồng", (10**9, "VND")),
        ("ĐVT: nghìn đồng", (10**3, "VND")),
        ("$ in billions", (10**9, "USD")),
        ("Revenue (USD thousands)", (10**3, "USD")),
        ("(in millions)", (10**6, None)),
        ("Tỷ lệ (%)", (1, "percent")),
        ("Giá (đồng)", (1, "VND")),
        ("Doanh thu năm 2024", None),
    ],
)
def test_a_scale_hint_is_read_in_vietnamese_and_english(
    text: str, scale: tuple[int, str | None] | None
) -> None:
    got = t.scale_of(text)
    assert (None if got is None else (got.factor, got.unit)) == (
        None if scale is None else (D(scale[0]), scale[1])
    )
