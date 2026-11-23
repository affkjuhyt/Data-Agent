"""DI-08: legal_headings.py re-levels the headings of legal text (§5.2) and leaves out running
headers, footers and a table of contents (§4.2)."""

from __future__ import annotations

from typing import Any

import pytest

from services.worker_document import legal_headings as lh
from services.worker_document.docling_adapter import Element

pytestmark = [pytest.mark.unit]


def _e(text: str, kind: str = "text", level: int | None = None, **kw: Any) -> Element:
    return Element(ref=f"#/texts/{text[:8]}", order_idx=0, kind=kind, text=text, level=level, **kw)  # type: ignore[arg-type]


def _levels(elements: list[Element]) -> list[tuple[str, int | None]]:
    return [(e.kind, e.level) for e in elements]


def test_vietnamese_legal_text_takes_the_levels_of_its_structure() -> None:
    got = lh.relevel(
        [
            _e("PHẦN THỨ I. QUY ĐỊNH CHUNG", "heading", 1),
            _e("Chương II. Cho vay", "heading", 1),
            _e("Mục 1. Điều kiện"),
            _e("Điều 5. Lãi suất cho vay"),
            _e("1. Lãi suất do Hội đồng quyết định."),
            _e("a) đối với khách hàng cá nhân;", "list"),
            _e("Quy định chung về lãi suất được áp dụng."),
        ],
        "vi",
    )
    assert _levels(got) == [
        ("heading", 1),
        ("heading", 2),
        ("heading", 3),
        ("heading", 4),
        ("heading", 5),
        ("heading", 6),
        ("text", None),
    ]


def test_a_long_paragraph_that_starts_with_a_number_stays_body_text() -> None:
    long = "1. " + "Khách hàng phải trả lãi đúng hạn theo hợp đồng. " * 6
    got = lh.relevel([_e("Điều 1. Phạm vi"), _e(long)], "vi")
    assert _levels(got) == [("heading", 4), ("text", None)]


def test_a_document_that_is_not_legal_text_keeps_its_levels() -> None:
    report = [
        _e("Báo cáo kết quả kinh doanh", "heading", 1),
        _e("1. Doanh thu tăng 12 %.", "list"),
        _e("Chương trình khuyến mãi kéo dài đến cuối năm."),
    ]
    assert lh.relevel(report, "vi") == report


def test_english_legal_text_is_re_levelled_with_its_own_words() -> None:
    got = lh.relevel(
        [_e("PART I"), _e("Chapter 2 Loans"), _e("Article 7 Interest"), _e("(a) for retail;")],
        "en",
    )
    assert _levels(got) == [("heading", 1), ("heading", 2), ("heading", 4), ("heading", 6)]
    assert lh.language_of("en-US") == "en" and lh.language_of("vi-VN") == "vi"
    assert lh.language_of(None) == "vi"


def _boxed(text: str, page: int, top: float, bottom: float) -> Element:
    # TOPLEFT boxes on a 1000-high page: y grows downward.
    return _e(
        text,
        page_no=page,
        bbox={"l": 0, "t": top, "r": 100, "b": bottom, "coord_origin": "TOPLEFT"},
    )


def test_running_headers_and_footers_and_a_table_of_contents_are_left_out() -> None:
    elements = []
    for page in (1, 2, 3):
        elements += [
            _boxed("NGÂN HÀNG X — QUY CHẾ TÍN DỤNG", page, 10, 40),
            _boxed(f"Nội dung trang {page}", page, 300, 340),
            _boxed(f"Trang {page}/3", page, 960, 990),
        ]
    elements.insert(1, _boxed("Chương I ........ 3\nChương II ....... 7", 1, 100, 160))
    kept, dropped = lh.drop_furniture(elements, {1: 1000.0, 2: 1000.0, 3: 1000.0})
    assert [e.text for e in kept] == ["Nội dung trang 1", "Nội dung trang 2", "Nội dung trang 3"]
    assert [e.order_idx for e in kept] == [0, 1, 2]
    assert dropped == {"running_header_footer": 6, "table_of_contents": 1}


def test_a_band_block_on_few_pages_and_a_bottom_left_box_are_read_right() -> None:
    # On one page of three: not running furniture. A BOTTOMLEFT box near y = height is the top.
    elements = [
        _boxed("Ghi chú riêng", 1, 10, 40),
        _e("Đầu trang", page_no=1, bbox={"l": 0, "t": 990, "r": 9, "b": 960, "coord_origin": "BOTTOMLEFT"}),
        _e("Đầu trang", page_no=2, bbox={"l": 0, "t": 990, "r": 9, "b": 960, "coord_origin": "BOTTOMLEFT"}),
        _e("Thân", page_no=3, bbox={"l": 0, "t": 500, "r": 9, "b": 400, "coord_origin": "BOTTOMLEFT"}),
    ]  # fmt: skip
    kept, dropped = lh.drop_furniture(elements, {1: 1000.0, 2: 1000.0, 3: 1000.0})
    assert [e.text for e in kept] == ["Ghi chú riêng", "Thân"]
    assert dropped == {"running_header_footer": 2}


def test_a_document_without_pages_keeps_everything() -> None:
    elements = [_e("Đầu trang"), _e("Nội dung")]
    kept, dropped = lh.drop_furniture(elements, {})
    assert [e.text for e in kept] == ["Đầu trang", "Nội dung"] and not dropped


def test_a_long_run_of_dots_is_read_in_linear_time() -> None:
    import time

    started = time.monotonic()
    line = "." * 200_000 + "x"
    assert not lh._toc_line(_e(line, page_no=1))
    assert lh._toc_line(_e("Chương I " + "." * 200_000 + " 3", page_no=1))
    assert time.monotonic() - started < 1.0
