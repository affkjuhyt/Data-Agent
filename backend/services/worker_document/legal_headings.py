"""Headings of legal text, and the page furniture and contents left out (DI-08, §4.2, §5.2).

Docling levels a section header by its outline, numbering or font; a Vietnamese legal document
often has no outline, and its Điều sit in body text. `relevel` sets the level its structure
names, in the order the law writes it: Phần 1, Chương 2, Mục 3, Điều 4, Khoản 5, Điểm 6 (English:
Part, Chapter, Article/Section, numbered paragraph, lettered point). Only a document with at least
one Điều/Article is re-levelled: in a report, "1. Doanh thu tăng" is a list item, not a Khoản.

`drop_furniture` leaves out what Docling's layout model did not mark as furniture: a block repeated
on at least 60 % of the pages in the top or bottom 8 % of the page (a running header or footer,
compared with its digits masked, so "Trang 3" and "Trang 4" are one), and a table of contents in
the first five pages (lines ending in leader dots and a page number).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Literal

from libs.language import norm
from services.worker_document.docling_adapter import Element

Language = Literal["vi", "en"]

PATTERNS: dict[Language, tuple[tuple[re.Pattern[str], int], ...]] = {
    "vi": (
        (re.compile(r"^(?:PHẦN|Phần)\s+(?:THỨ\s+)?[IVXLC\d]+\b"), 1),
        (re.compile(r"^(?:CHƯƠNG|Chương)\s+[IVXLC\d]+\b"), 2),
        (re.compile(r"^(?:MỤC|Mục)\s+\d+\b"), 3),
        (re.compile(r"^Điều\s+\d+[a-zđ]?\s*[.:]"), 4),
        (re.compile(r"^\d+\.\s"), 5),  # Khoản
        (re.compile(r"^[a-zđ]\)\s"), 6),  # Điểm
    ),
    "en": (
        (re.compile(r"^(?:PART|Part)\s+[IVXLC\d]+\b"), 1),
        (re.compile(r"^(?:CHAPTER|Chapter)\s+[IVXLC\d]+\b"), 2),
        (re.compile(r"^(?:Article|Section|ARTICLE|SECTION)\s+\d+[a-z]?\b"), 4),
        (re.compile(r"^\d+\.\s"), 5),
        (re.compile(r"^\([a-z]\)\s"), 6),
    ),
}
# What makes a document legal text: an article. Without one nothing is re-levelled.
_ARTICLE: dict[Language, re.Pattern[str]] = {
    "vi": re.compile(r"^Điều\s+\d+[a-zđ]?\s*[.:]"),
    "en": re.compile(r"^(?:Article|ARTICLE|Section|SECTION)\s+\d+[a-z]?\b"),
}
# A paragraph longer than this is body text that happens to start with "1.", not a heading.
MAX_HEADING_CHARS = 200


def language_of(locale: str | None) -> Language:
    return "en" if (locale or "").lower().startswith("en") else "vi"


def relevel(elements: Sequence[Element], language: Language) -> list[Element]:
    """The elements with the heading kind and level their legal structure names."""
    texts = [e.text.strip() for e in elements if e.kind in ("text", "heading", "list")]
    if not any(_ARTICLE[language].match(t) for t in texts):
        return list(elements)
    out: list[Element] = []
    for e in elements:
        text = e.text.strip()
        level = (
            next((lvl for rx, lvl in PATTERNS[language] if rx.match(text)), None)
            if e.kind in ("text", "heading", "list")
            else None
        )
        if level is not None and (e.kind == "heading" or len(text) <= MAX_HEADING_CHARS):
            out.append(replace(e, kind="heading", level=level))
        else:
            out.append(e)
    return out


# ------------------------------------------------------------------------------ furniture

BAND = 0.08
REPEATED_SHARE = 0.6
TOC_PAGES = 5
_TOC_LINE = re.compile(r"\.{3,}\s*\d+\s*$")
_DIGITS = re.compile(r"\d+")


def drop_furniture(
    elements: Sequence[Element], page_heights: Mapping[int, float]
) -> tuple[list[Element], Counter[str]]:
    """The elements without running headers and footers and a table of contents; what was
    dropped, by reason. Elements without a page or a box (DOCX, HTML) are kept as they are."""
    pages = {e.page_no for e in elements if e.page_no is not None}
    banded: dict[int, str] = {}
    for i, e in enumerate(elements):
        if e.kind in ("text", "heading") and (band := _band(e, page_heights)):
            banded[i] = f"{band}:{_DIGITS.sub('#', norm(e.text))}"
    # On how many pages each banded text stands (once per page, however often it repeats there).
    on_pages = Counter(key for key, _page in {(banded[i], elements[i].page_no) for i in banded})
    repeated = {
        key for key, n in on_pages.items() if len(pages) >= 2 and n >= REPEATED_SHARE * len(pages)
    }
    kept: list[Element] = []
    dropped: Counter[str] = Counter()
    for i, e in enumerate(elements):
        if banded.get(i) in repeated:
            dropped["running_header_footer"] += 1
            continue
        if _toc_line(e):
            dropped["table_of_contents"] += 1
            continue
        kept.append(e)
    return [replace(e, order_idx=n) for n, e in enumerate(kept)], dropped


def _band(e: Element, page_heights: Mapping[int, float]) -> str | None:
    height = page_heights.get(e.page_no) if e.page_no is not None else None
    if e.bbox is None or not height:
        return None
    top, bottom = float(e.bbox["t"]), float(e.bbox["b"])
    if str(e.bbox.get("coord_origin", "")).upper() == "BOTTOMLEFT":
        # y grows upward: the top of the page is `height`.
        top, bottom = height - top, height - bottom
    if bottom <= height * BAND:
        return "top"
    if top >= height * (1 - BAND):
        return "bottom"
    return None


def _toc_line(e: Element) -> bool:
    if e.page_no is not None and e.page_no > TOC_PAGES:
        return False
    if e.kind not in ("text", "heading", "list"):
        return False
    lines = [line for line in e.text.splitlines() if line.strip()]
    return bool(lines) and all(_TOC_LINE.search(line) for line in lines)
