"""DI-08: chunker.py cuts chunks on headings (§5.3): the section path, a long Điều as a parent
with children at its Khoản, no table cell in any chunk, one summary per table (§5.4)."""

from __future__ import annotations

import decimal
import json
import pathlib
from typing import Any

import pytest

from services.worker_document import chunker
from services.worker_document.docling_adapter import Element

pytestmark = [pytest.mark.unit]

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "docling"


def _doc(*parts: tuple[str, str, int | None]) -> list[Element]:
    return [
        Element(ref=f"#/texts/{i}", order_idx=i, kind=kind, text=text, level=level, page_no=1)  # type: ignore[arg-type]
        for i, (kind, text, level) in enumerate(parts)
    ]


H = "heading"


def test_each_dieu_is_a_chunk_with_the_path_of_its_headings() -> None:
    elements = _doc(
        ("text", "Căn cứ Luật Các tổ chức tín dụng;", None),
        (H, "Chương I. Quy định chung", 2),
        (H, "Điều 1. Phạm vi điều chỉnh", 4),
        ("text", "Quy chế này áp dụng cho mọi khoản vay.", None),
        (H, "Điều 2. Đối tượng áp dụng", 4),
        (H, "1. Khách hàng cá nhân.", 5),
        (H, "2. Khách hàng doanh nghiệp.", 5),
        (H, "Chương II. Lãi suất", 2),
        (H, "Điều 3. Quy chế có hiệu lực từ ngày ký.", 4),
    )
    got = chunker.chunks_of(elements, title="Quy chế tín dụng", doc_no="12/QĐ")
    assert [(c.section_path, c.text) for c in got] == [
        (None, "Căn cứ Luật Các tổ chức tín dụng;"),
        (
            "Chương I. Quy định chung > Điều 1. Phạm vi điều chỉnh",
            "Điều 1. Phạm vi điều chỉnh\n\nQuy chế này áp dụng cho mọi khoản vay.",
        ),
        (
            "Chương I. Quy định chung > Điều 2. Đối tượng áp dụng",
            "Điều 2. Đối tượng áp dụng\n\n1. Khách hàng cá nhân.\n\n2. Khách hàng doanh nghiệp.",
        ),
        # A leaf heading with no body is a chunk of its own text.
        (
            "Chương II. Lãi suất > Điều 3. Quy chế có hiệu lực từ ngày ký.",
            "Điều 3. Quy chế có hiệu lực từ ngày ký.",
        ),
    ]
    assert got[1].context_header == (
        "Quy chế tín dụng | Số 12/QĐ | Chương I. Quy định chung > Điều 1. Phạm vi điều chỉnh"
    )
    assert all(c.kind == "section" and c.embed and c.parent is None for c in got)
    assert got[2].element_refs == ["#/texts/4", "#/texts/5", "#/texts/6"]


def test_a_long_dieu_is_a_parent_with_children_at_its_khoan() -> None:
    clause = "Bên vay phải trả nợ gốc và lãi đúng hạn theo lịch đã thỏa thuận. " * 9
    elements = _doc(
        (H, "Điều 5. Nghĩa vụ của bên vay", 4),
        (H, f"1. {clause}", 5),
        (H, f"2. {clause}", 5),
        (H, f"3. {clause}", 5),
    )
    got = chunker.chunks_of(elements)
    parent, *children = got
    assert len(parent.text) > chunker.MAX_CHARS and not parent.embed and parent.parent is None
    assert parent.section_path == "Điều 5. Nghĩa vụ của bên vay"
    assert [c.parent for c in children] == [0] * len(children)
    assert parent.children == list(range(1, len(got)))
    assert all(c.embed and len(c.text) <= chunker.MAX_CHARS + 100 for c in children)
    # Each child keeps the Điều heading and names the Khoản it starts at.
    # Two Khoản fit one child; the third starts the next (cut at a Khoản once half full).
    assert [c.section_path for c in children] == [
        "Điều 5. Nghĩa vụ của bên vay > Khoản 1-2",
        "Điều 5. Nghĩa vụ của bên vay > Khoản 3",
    ]
    assert all(c.text.startswith("Điều 5. Nghĩa vụ của bên vay\n\n") for c in children)


def test_a_short_last_piece_joins_the_one_before() -> None:
    body = [("text", "x" * 900, None), ("text", "y" * 900, None), ("text", "z" * 50, None)]
    got = chunker.chunks_of(_doc((H, "Điều 1. Dài", 4), *body))
    assert [len(c.text) for c in got[1:]] == [
        len("Điều 1. Dài") + 2 + 900,
        len("Điều 1. Dài") + 2 + 900 + 2 + 50,
    ]


class _Cell:
    def __init__(self, row: int, col: int, text: str, header: bool, value: Any = None) -> None:
        self.row_idx, self.col_idx, self.text, self.is_header = row, col, text, header
        self.numeric_value = value


class _Table:
    def __init__(self) -> None:
        self.element_ref = "#/tables/0"
        self.caption = "Bảng 1. Dư nợ theo kỳ (triệu đồng)"
        self.header_rows, self.n_rows, self.n_cols = 1, 4, 3
        self.page_from, self.page_to = 2, 3
        self.cells = [
            _Cell(0, 0, "Chỉ tiêu", True),
            _Cell(0, 1, "Kế hoạch", True),
            _Cell(0, 2, "Thực hiện", True),
            *[
                c
                for r, (label, a, b) in enumerate(
                    [
                        ("Dư nợ", "1.234", "1.300"),
                        ("Nợ xấu", "12", "15"),
                        ("Tổng", "1.246", "1.315"),
                    ],
                    start=1,
                )
                for c in (
                    _Cell(r, 0, label, False),
                    _Cell(r, 1, a, False, decimal.Decimal(1)),
                    _Cell(r, 2, b, False, decimal.Decimal(1)),
                )
            ],
        ]


def test_a_table_is_one_summary_chunk_and_no_chunk_holds_a_figure() -> None:
    elements = [
        Element(ref="#/texts/0", order_idx=0, kind="heading", text="Điều 4. Dư nợ", level=4, page_no=2),
        Element(ref="#/texts/1", order_idx=1, kind="caption", text="Bảng 1. Dư nợ theo kỳ (triệu đồng)",
                page_no=2, parent_ref="#/tables/0"),
        Element(ref="#/tables/0", order_idx=2, kind="table", text="", page_no=2),
        Element(ref="#/texts/2", order_idx=3, kind="text", text="Số liệu chưa kiểm toán.", page_no=3),
    ]  # fmt: skip
    table = _Table()
    got = chunker.chunks_of(elements, [table])  # type: ignore[list-item]
    section, summary = got
    # The caption goes with its table, not with the section's text.
    assert section.text == "Điều 4. Dư nợ\n\nSố liệu chưa kiểm toán."
    assert (summary.kind, summary.table, summary.element_refs) == ("table", 0, ["#/tables/0"])
    assert summary.text == (
        "Bảng 1. Dư nợ theo kỳ (triệu đồng)\nChỉ tiêu | Kế hoạch | Thực hiện\n"
        "Dư nợ; Nợ xấu; Tổng\nbảng 4x3, trang 2-3"
    )
    assert (summary.page_from, summary.page_to, summary.section_path) == (2, 3, "Điều 4. Dư nợ")
    figures = [c.text for c in table.cells if c.numeric_value is not None]
    assert not any(f in chunk.text for chunk in got for f in figures)


def test_the_docling_docx_fixture_chunks_without_its_table_cells() -> None:
    from docling_core.types.doc import DoclingDocument

    from services.worker_document import docling_adapter as da
    from services.worker_document import legal_headings as lh
    from services.worker_document import tables as t

    doc = DoclingDocument.model_validate(
        json.loads((FIXTURES / "rules_docx.json").read_text("utf-8"))
    )
    elements = lh.relevel(da.elements_of(doc).elements, "vi")
    tables = t.tables_of(doc, elements, decimal_mark=",")
    got = chunker.chunks_of(elements, tables, title="Quy chế tín dụng")
    assert [c.kind for c in got] == ["section", "table"]
    assert got[0].section_path == (
        "Quy chế tín dụng > Chương I. Quy định chung > Điều 1. Phạm vi điều chỉnh"
    )
    assert "1.234" not in got[0].text and "1.234" not in got[1].text
    assert got[0].page_from == got[0].page_to == 1  # a DOCX has no pages: cited as one
