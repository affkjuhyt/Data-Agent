"""A document's tables as cells with their numbers (DI-07, DOCUMENT_INTELLIGENCE_SPEC §4.3, §4.4).

`tables_of` reads every table of a DoclingDocument into the rows of document.doc_table and
document.table_cell (DI-06): each cell's position and span, text, header flag, page and bbox, and
its number, read deterministically with the collection's decimal mark and multiplied by the scale
the table states ("(triệu đồng)", "Đơn vị: tỷ đồng", "in millions"), with the unit that scale
names. Nothing here calls a model: a number in a document is data with a cell, and the answer
reads it by SQL (table_query, DI-12), never from a chunk.

Also deterministic, and reported rather than guessed:
- a table that goes on from the previous page (§4.3.1) points at it (`continues`);
- two scale hints that disagree (a caption in millions, a header in thousands) multiply nothing,
  leave the unit empty and raise a `field_mismatch` issue;
- a total row ("Tổng cộng", "Total") whose figure is not the sum of the rows above it raises one too.

The ids are the caller's (the handler writes them, DI-09): a table names its element by the
Docling item ref (docling_adapter.Element.ref) and the table it continues by its index here.
"""

from __future__ import annotations

import decimal
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from libs import tabular
from libs.language import fold
from services.worker_document.docling_adapter import Element

# A cell that says "no figure" rather than zero.
_NO_FIGURE = frozenset({"", "-", "\u2013", "\u2014", "n/a", "na", "x", "..."})
# Currency marks written next to a figure; the unit comes from the scale hint, not from them.
_CURRENCY = re.compile(r"^(?:vnd|vnđ|usd|đ|₫|\$|€|£)\s*|\s*(?:vnd|vnđ|usd|đ|₫|\$|€|£)$", re.I)


@dataclass(frozen=True)
class Scale:
    factor: decimal.Decimal
    unit: str | None


# What a caption, header or note says the figures are counted in, matched on folded text
# (no diacritics, lower case). Vietnamese and English; the first match of a text is its hint.
_UNIT_WORDS: tuple[tuple[str, str], ...] = (
    (r"(?:dong|vnd|vnđ)", "VND"),
    (r"(?:usd|us\$|\$|dollars?)", "USD"),
)
_SCALE_WORDS: tuple[tuple[str, decimal.Decimal], ...] = (
    (r"(?:nghin ty|ngan ty)", decimal.Decimal(10) ** 12),
    (r"ty", decimal.Decimal(10) ** 9),
    (r"trieu", decimal.Decimal(10) ** 6),
    (r"(?:nghin|ngan)", decimal.Decimal(10) ** 3),
    (r"(?:billions?|bn)", decimal.Decimal(10) ** 9),
    (r"(?:millions?|mn|mio)", decimal.Decimal(10) ** 6),
    (r"(?:thousands?|k)", decimal.Decimal(10) ** 3),
)
_SCALE_HINT = re.compile(
    r"(?:\(|don vi(?:\s+tinh)?\s*:?\s*|dvt\s*:?\s*|\bin\s+|unit\s*:?\s*)"
    r"(?P<body>[^()\n;]{1,40})"
)


def scale_of(text: str) -> Scale | None:
    """The scale and unit a text states, None when it states none.

    "(triệu đồng)" → 10^6 VND; "Đơn vị: tỷ đồng" → 10^9 VND; "$ in billions" → 10^9 USD;
    "(%)" → percent, no scale; "(đồng)" → VND, no scale."""
    folded = fold(text)
    for match in _SCALE_HINT.finditer(folded):
        body = match.group("body").strip().rstrip(")").strip()
        if body in ("%", "phan tram", "percent"):
            return Scale(decimal.Decimal(1), "percent")
        factor = next(
            (f for words, f in _SCALE_WORDS if re.search(rf"(?<![a-z]){words}(?![a-z])", body)),
            None,
        )
        unit = next(
            (u for words, u in _UNIT_WORDS if re.search(rf"(?<![a-z]){words}(?![a-z])", body)),
            None,
        )
        if unit is None:
            # "$ in billions": the currency sits before the "in".
            before = folded[: match.start()].rstrip()[-4:]
            unit = next((u for words, u in _UNIT_WORDS if re.search(rf"{words}\s*$", before)), None)
        if factor is not None or unit is not None:
            return Scale(factor or decimal.Decimal(1), unit)
    return None


@dataclass
class Cell:
    row_idx: int
    col_idx: int
    row_span: int
    col_span: int
    text: str
    is_header: bool
    numeric_value: decimal.Decimal | None = None
    unit: str | None = None
    page_no: int | None = None
    bbox: dict[str, Any] | None = None


@dataclass(frozen=True)
class Issue:
    """A quality issue for the handler to write (document.quality_issue)."""

    kind: str  # field_mismatch
    severity: str  # warning
    page_no: int | None
    detail: dict[str, Any]


@dataclass
class Table:
    element_ref: str
    page_from: int | None
    page_to: int | None
    n_rows: int
    n_cols: int
    header_rows: int
    caption: str | None
    cells: list[Cell]
    numeric_share: decimal.Decimal | None = None
    continues: int | None = None  # index in tables_of's result of the table this one goes on
    issues: list[Issue] = field(default_factory=list)


def tables_of(
    doc: Any,
    elements: Sequence[Element],
    *,
    decimal_mark: str | None = None,
) -> list[Table]:
    """Every table of the document, in reading order. `decimal_mark` is the collection's (its
    ingest profile); without one each table settles its own from its figures (libs.tabular)."""
    by_ref = {e.ref: e for e in elements}
    tables: list[Table] = []
    for item in getattr(doc, "tables", []):
        element = by_ref.get(item.self_ref)
        if element is None:  # furniture or a table of contents: not one of the document's tables
            continue
        table = _table(item, element, elements, decimal_mark)
        # A header-less continuation reads its columns from the first part (§4.3.1, at query time).
        if tables and _continues(tables[-1], table, by_ref, elements):
            table.continues = len(tables) - 1
        tables.append(table)
    return tables


def _table(
    item: Any, element: Element, elements: Sequence[Element], decimal_mark: str | None
) -> Table:
    data = item.data
    pages = [p.page_no for p in item.prov] if item.prov else []
    page = pages[0] if pages else None
    cells: dict[tuple[int, int], Cell] = {}
    for c in data.table_cells:
        key = (c.start_row_offset_idx, c.start_col_offset_idx)
        if key in cells:
            continue
        cells[key] = Cell(
            row_idx=c.start_row_offset_idx,
            col_idx=c.start_col_offset_idx,
            # Clamped to the table: a crafted file may claim any span (a DOCX gridSpan of 10^9),
            # and the spans are walked below (the columns a header's scale covers).
            row_span=min(
                max(c.end_row_offset_idx - c.start_row_offset_idx, 1),
                max(data.num_rows - c.start_row_offset_idx, 1),
            ),
            col_span=min(
                max(c.end_col_offset_idx - c.start_col_offset_idx, 1),
                max(data.num_cols - c.start_col_offset_idx, 1),
            ),
            text=(c.text or "").strip(),
            # TableFormer marks row headers loosely (whole scanned tables, figures included):
            # a row header is a label of the first column; a column header is taken as given.
            is_header=bool(c.column_header or (c.row_header and c.start_col_offset_idx == 0)),
            page_no=page,
            bbox=_bbox(c.bbox) if getattr(c, "bbox", None) is not None else None,
        )
    ordered = sorted(cells.values(), key=lambda c: (c.row_idx, c.col_idx))
    caption = _caption(item, element, elements)
    header_rows = _header_rows(ordered) or _text_rows(ordered, decimal_mark)
    table = Table(
        element_ref=element.ref,
        page_from=min(pages) if pages else None,
        page_to=max(pages) if pages else None,
        n_rows=data.num_rows,
        n_cols=data.num_cols,
        header_rows=header_rows,
        caption=caption,
        cells=ordered,
    )
    _read_numbers(table, element, elements, decimal_mark)
    _check_totals(table)
    return table


def _bbox(box: Any) -> dict[str, Any]:
    origin = getattr(box.coord_origin, "value", str(box.coord_origin))
    return {"l": box.l, "t": box.t, "r": box.r, "b": box.b, "coord_origin": origin}


def _caption(item: Any, element: Element, elements: Sequence[Element]) -> str | None:
    texts = [e.text for e in elements if e.kind == "caption" and e.parent_ref == element.ref]
    if not texts:
        try:
            texts = [item.caption_text(None)] if item.caption_text(None) else []
        except Exception:  # noqa: BLE001 - an older docling-core takes no argument here
            texts = []
    return " ".join(t for t in texts if t) or None


def _header_rows(cells: Sequence[Cell]) -> int:
    """The leading rows whose cells are all headers (a merged header spans several)."""
    rows: dict[int, list[Cell]] = {}
    for c in cells:
        rows.setdefault(c.row_idx, []).append(c)
    count = 0
    for row in sorted(rows):
        if rows[row] and all(c.is_header for c in rows[row]):
            count = row + 1
        else:
            break
    return count


def _text_rows(cells: Sequence[Cell], decimal_mark: str | None) -> int:
    """No column header marked (a scan often has none): the leading rows with no figure are the
    header when a later row has one, as an XLSX sheet's header is read (§4.5). They are marked."""
    rows: dict[int, list[Cell]] = {}
    for c in cells:
        rows.setdefault(c.row_idx, []).append(c)
    mark = decimal_mark or ","

    def figures(row: list[Cell]) -> bool:
        return any(c.col_idx > 0 and parse_number(c.text, mark) is not None for c in row)

    order = sorted(rows)
    count = 0
    for row in order:
        if figures(rows[row]):
            break
        count += 1
    if count == 0 or count == len(order):
        return 0
    for row in order[:count]:
        for c in rows[row]:
            c.is_header = True
    return count


# ------------------------------------------------------------------------------ numbers


def _read_numbers(
    table: Table, element: Element, elements: Sequence[Element], decimal_mark: str | None
) -> None:
    body = [c for c in table.cells if not c.is_header and c.row_idx >= table.header_rows]
    mark = decimal_mark or tabular.decimal_mark(c.text for c in body) or ","
    table_hint = _table_hint(table, element, elements)
    column_hints = _column_hints(table)
    conflicts: set[int] = set()
    for c in body:
        number = parse_number(c.text, mark)
        if number is None:
            continue
        value, percent = number
        if percent:
            c.numeric_value, c.unit = value, "percent"
            continue
        hints = {h for h in (table_hint, column_hints.get(c.col_idx)) if h is not None}
        if len(hints) > 1 and len({h.factor for h in hints}) > 1:
            # Two scales for one figure: multiply by neither, say so once per column (§4.4).
            conflicts.add(c.col_idx)
            c.numeric_value, c.unit = value, None
            continue
        hint = column_hints.get(c.col_idx) or table_hint
        if hint is not None and hint.unit == "percent":
            c.numeric_value, c.unit = value, "percent"
        elif hint is not None:
            c.numeric_value, c.unit = value * hint.factor, hint.unit
        else:
            c.numeric_value = value
    for col in sorted(conflicts):
        table.issues.append(
            Issue(
                "field_mismatch",
                "warning",
                table.page_from,
                {
                    "reason": "scale_conflict",
                    "table": table.element_ref,
                    "column": col,
                    "hints": sorted(
                        str(h.factor) + " " + (h.unit or "")
                        for h in (table_hint, column_hints.get(col))
                        if h is not None
                    ),
                },
            )
        )
    figures = [c for c in body if c.text]
    numeric = [c for c in figures if c.numeric_value is not None]
    table.numeric_share = (
        (decimal.Decimal(len(numeric)) / len(figures)).quantize(decimal.Decimal("0.0001"))
        if figures
        else None
    )


def parse_number(text: str, mark: str) -> tuple[decimal.Decimal, bool] | None:
    """A cell's figure and whether it is a percent; None for text or "no figure" ("-", "—").

    "(1.234)" → -1234; "12,5%" → 12.5, percent; "1.234 đ" → 1234 (the unit is the hint's)."""
    s = (text or "").strip()
    if fold(s) in _NO_FIGURE or s in _NO_FIGURE:
        return None
    percent = s.endswith("%")
    s = _CURRENCY.sub("", s.rstrip("%").strip()).strip()
    # Some tables write a negative with a Unicode minus or a dash.
    s = re.sub("^[\u2212\u2013\u2014]\\s*", "-", s)
    try:
        return tabular.to_number(s, mark), percent
    except ValueError:
        return None


def _table_hint(table: Table, element: Element, elements: Sequence[Element]) -> Scale | None:
    """The scale the caption states, else a note just before the table on its page."""
    if table.caption and (hint := scale_of(table.caption)):
        return hint
    before = [
        e
        for e in elements
        if e.order_idx < element.order_idx
        and e.kind in ("text", "caption")
        and (e.page_no is None or e.page_no == element.page_no)
    ][-2:]
    for e in reversed(before):
        if len(e.text) <= 80 and (hint := scale_of(e.text)):
            return hint
    return None


def _column_hints(table: Table) -> dict[int, Scale]:
    """A header cell's scale for the columns it covers ("Doanh thu (tỷ đồng)")."""
    hints: dict[int, Scale] = {}
    for c in table.cells:
        if c.is_header and (hint := scale_of(c.text)):
            for col in range(c.col_idx, min(c.col_idx + c.col_span, table.n_cols)):
                hints[col] = hint
    return hints


# ------------------------------------------------------------------------------ totals

_TOTAL = re.compile(r"^(tong cong|tong so|tong|cong|total|grand total)\b")


def _check_totals(table: Table) -> None:
    """A total row's figure is the sum of the body rows above it, column by column; one that
    is not (beyond the rounding of its own digits) is an issue, never corrected."""
    rows: dict[int, dict[int, Cell]] = {}
    for c in table.cells:
        if not c.is_header and c.row_idx >= table.header_rows:
            rows.setdefault(c.row_idx, {})[c.col_idx] = c
    above: list[dict[int, Cell]] = []
    for row in sorted(rows):
        cells = rows[row]
        label = next((c.text for col, c in sorted(cells.items()) if c.numeric_value is None), "")
        if not _TOTAL.match(fold(label)):
            above.append(cells)
            continue
        for col, total in cells.items():
            parts = [
                r[col].numeric_value for r in above if col in r and r[col].numeric_value is not None
            ]
            if total.numeric_value is None or len(parts) < 2 or total.unit == "percent":
                continue
            summed = sum(parts, decimal.Decimal(0))
            if abs(summed - total.numeric_value) > _rounding(total, parts):
                table.issues.append(
                    Issue(
                        "field_mismatch",
                        "warning",
                        total.page_no,
                        {
                            "reason": "total_mismatch",
                            "table": table.element_ref,
                            "row": total.row_idx,
                            "column": col,
                            "stated": str(total.numeric_value),
                            "summed": str(summed),
                        },
                    )
                )
        above = []


def _rounding(total: Cell, parts: Sequence[decimal.Decimal]) -> decimal.Decimal:
    """Each figure shown may be off by half a unit of its own last digit: the sum of those is
    how far a correct total may be from the rows' sum."""

    def half(value: decimal.Decimal | None) -> decimal.Decimal:
        exponent = value.as_tuple().exponent if value is not None else 0
        return decimal.Decimal(1).scaleb(exponent if isinstance(exponent, int) else 0) / 2

    return half(total.numeric_value) + sum((half(p) for p in parts), decimal.Decimal(0))


# ------------------------------------------------------------------------------ continuation


def _continues(
    prev: Table, cur: Table, by_ref: Mapping[str, Element], elements: Sequence[Element]
) -> bool:
    """§4.3.1: the next page, the same columns, no header or the same one, no heading between."""
    if prev.page_to is None or cur.page_from is None or cur.page_from != prev.page_to + 1:
        return False
    if cur.n_cols != prev.n_cols:
        return False
    if cur.header_rows and _jaccard(_header_words(prev), _header_words(cur)) < 0.8:
        return False
    start, end = by_ref[prev.element_ref].order_idx, by_ref[cur.element_ref].order_idx
    return not any(e.kind == "heading" and start < e.order_idx < end for e in elements)


def _header_words(table: Table) -> set[str]:
    return {
        word
        for c in table.cells
        if c.is_header and c.row_idx < max(table.header_rows, 1)
        for word in fold(c.text).split()
    }


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 1.0
