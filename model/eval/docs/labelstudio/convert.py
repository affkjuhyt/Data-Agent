"""Ground truth of the Docling bake-off corpus (PoC-1, DI-01) from Label Studio and table sheets.

The corpus lives outside the repo (BAKEOFF_DOCS, one directory per document: licences differ and
scans may hold personal data); the repo keeps its manifest and the gold this script writes
(docs/blueprint/DOCUMENT_INTELLIGENCE_SPEC.md §11). README.md beside this file is the guide.

    render   --docs DIR                       page images of <doc>/original.* and the tasks to import
    manifest --docs DIR --out FILE            sha256, size and pages of each original
    gold     --docs DIR --export FILE --annotator WHO --out DIR
                                              one annotator's export and sheets as gold
    agree    --a DIR --b DIR                  where two annotators' gold differ (exit 1 if anywhere)

A page is labelled in Label Studio (page.xml): every region a label, its text and its place in
the reading order. A table is typed into a sheet, one CSV per table, cell by cell as it looks on
the page; a cell that a merged cell covers holds `<` (merged with the cell to its left), `^`
(with the cell above) or `+` (both), the way OTSL writes spans. The table's region names its
sheet: `t1; header=1`.

Standard library only, except `render` (pypdfium2 and Pillow, in the worker-document image).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Label → (kind, heading level). The values are page.xml's.
LABELS: dict[str, tuple[str, int | None]] = {
    "Tiêu đề": ("title", 0),
    "Chương": ("heading", 1),
    "Mục": ("heading", 2),
    "Điều": ("heading", 3),
    "Khoản": ("heading", 4),
    "Heading 1": ("heading", 1),
    "Heading 2": ("heading", 2),
    "Heading 3": ("heading", 3),
    "Đoạn văn": ("text", None),
    "Mục liệt kê": ("list_item", None),
    "Bảng": ("table", None),
    "Hình": ("figure", None),
    "Chú thích hình/bảng": ("caption", None),
    "Chú thích cuối trang": ("footnote", None),
    "Đầu trang": ("page_header", None),
    "Chân trang": ("page_footer", None),
}
NO_TEXT = {"figure"}
TABLE_REF = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9_-]*)\s*;\s*header\s*=\s*(\d+)\s*$")
MARKERS = {"<", "^", "+"}
DOC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
PAGE_IMAGE = "page-{:03d}.png"
# Where `make labelstudio` mounts BAKEOFF_DOCS under its document root: Label Studio refuses a
# local storage on the root itself, and serves a file only under a storage.
SERVED_AS = "bakeoff"


class Invalid(Exception):
    """The export or a sheet does not make gold; each line says where and why."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text).strip()


# ─── Tables ──────────────────────────────────────────────────────────────────


@dataclass
class Cell:
    row: int
    col: int
    rowspan: int
    colspan: int
    text: str
    header: bool

    def as_json(self) -> dict[str, Any]:
        return {
            "row": self.row,
            "col": self.col,
            "rowspan": self.rowspan,
            "colspan": self.colspan,
            "text": self.text,
            "header": self.header,
        }


def read_sheet(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    # A spreadsheet often saves trailing empty rows; a table never ends in one.
    while rows and not any(v.strip() for v in rows[-1]):
        rows.pop()
    return rows


def table_cells(rows: list[list[str]], header_rows: int, where: str) -> list[Cell]:
    """The cells of a sheet; every marker must continue exactly one cell."""
    if not rows or not rows[0]:
        raise Invalid([f"{where}: empty sheet"])
    width = len(rows[0])
    problems = [
        f"{where}: row {r + 1} has {len(row)} columns, row 1 has {width}"
        for r, row in enumerate(rows)
        if len(row) != width
    ]
    if header_rows > len(rows):
        problems.append(f"{where}: header={header_rows} but the table has {len(rows)} rows")
    if problems:
        raise Invalid(problems)

    def raw(r: int, c: int) -> str:
        return rows[r][c].strip()

    owner: list[list[int | None]] = [[None] * width for _ in rows]
    cells: list[Cell] = []
    for r in range(len(rows)):
        for c in range(width):
            if raw(r, c) in MARKERS:
                continue
            colspan = 1
            while c + colspan < width and raw(r, c + colspan) == "<":
                colspan += 1
            rowspan = 1
            while r + rowspan < len(rows) and raw(r + rowspan, c) == "^":
                rowspan += 1
            for rr in range(r, r + rowspan):
                for cc in range(c, c + colspan):
                    if (rr, cc) != (r, c):
                        want = "<" if rr == r else "^" if cc == c else "+"
                        if raw(rr, cc) != want:
                            problems.append(
                                f"{where}: cell {_ref(rr, cc)} should be {want!r} inside the cell"
                                f" spanning from {_ref(r, c)}, found {raw(rr, cc)!r}"
                            )
                            continue
                    if owner[rr][cc] is not None:
                        problems.append(f"{where}: cell {_ref(rr, cc)} is covered twice")
                    owner[rr][cc] = len(cells)
            text = raw(r, c)
            if len(text) == 2 and text[0] == "\\" and text[1] in MARKERS:
                text = text[1]
            cells.append(Cell(r, c, rowspan, colspan, nfc(text), r < header_rows))
    problems += [
        f"{where}: {raw(r, c)!r} at {_ref(r, c)} continues no cell"
        for r in range(len(rows))
        for c in range(width)
        if owner[r][c] is None
    ]
    if problems:
        raise Invalid(problems)
    return cells


def _ref(r: int, c: int) -> str:
    """Spreadsheet name of a cell (A1), what an annotator sees."""
    letters = ""
    c += 1
    while c:
        c, rem = divmod(c - 1, 26)
        letters = chr(65 + rem) + letters
    return f"{letters}{r + 1}"


def table_html(cells: list[Cell]) -> str:
    """PubTabNet's form, which the TEDS evaluator reads: header rows in <thead>, all cells <td>."""
    rows: dict[int, list[Cell]] = {}
    for cell in cells:
        rows.setdefault(cell.row, []).append(cell)

    def tr(row: list[Cell]) -> str:
        tds = []
        for cell in sorted(row, key=lambda x: x.col):
            span = (f' rowspan="{cell.rowspan}"' if cell.rowspan > 1 else "") + (
                f' colspan="{cell.colspan}"' if cell.colspan > 1 else ""
            )
            tds.append(f"<td{span}>{html.escape(cell.text, quote=False)}</td>")
        return "<tr>" + "".join(tds) + "</tr>"

    head = [tr(rows[r]) for r in sorted(rows) if all(c.header for c in rows[r])]
    body = [tr(rows[r]) for r in sorted(rows) if not all(c.header for c in rows[r])]
    thead = f"<thead>{''.join(head)}</thead>" if head else ""
    return f"<html><body><table>{thead}<tbody>{''.join(body)}</tbody></table></body></html>"


# ─── Pages ───────────────────────────────────────────────────────────────────


@dataclass
class Element:
    order: int
    label: str
    kind: str
    level: int | None
    bbox: tuple[float, float, float, float]
    text: str
    table: dict[str, Any] | None = None

    def as_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "order": self.order,
            "label": self.label,
            "kind": self.kind,
            "bbox": [round(v, 1) for v in self.bbox],
            "text": self.text,
        }
        if self.level is not None:
            out["level"] = self.level
        if self.table is not None:
            out["table"] = self.table["id"]
        return out


@dataclass
class Page:
    doc_id: str
    page: int
    width: int
    height: int
    elements: list[Element] = field(default_factory=list)


def _who(annotation: dict[str, Any]) -> set[str]:
    by = annotation.get("completed_by")
    if isinstance(by, dict):
        return {str(v) for k, v in by.items() if k in ("id", "email", "username") and v}
    return {str(by)} if by is not None else set()


def pages_of(export: list[dict[str, Any]], annotator: str) -> tuple[list[Page], list[str]]:
    """The pages one annotator labelled; the problems found on the way."""
    pages: list[Page] = []
    problems: list[str] = []
    for task in export:
        data = task.get("data", {})
        doc_id, page_no = data.get("doc_id"), data.get("page")
        where = f"task {task.get('id')} ({doc_id} p.{page_no})"
        if not isinstance(doc_id, str) or not DOC_ID.match(doc_id) or not isinstance(page_no, int):
            problems.append(f"{where}: the task has no doc_id/page (import tasks.json from render)")
            continue
        mine = [
            a
            for a in task.get("annotations", [])
            if annotator in _who(a) and not a.get("was_cancelled")
        ]
        if len(mine) != 1:
            problems.append(f"{where}: {len(mine)} annotations by {annotator}, need exactly one")
            continue
        page, found = _page(doc_id, page_no, mine[0].get("result", []), where)
        problems += found
        if page is not None:
            pages.append(page)
    pages.sort(key=lambda p: (p.doc_id, p.page))
    return pages, problems


def _page(
    doc_id: str, page_no: int, result: list[dict[str, Any]], where: str
) -> tuple[Page | None, list[str]]:
    regions: dict[str, dict[str, Any]] = {}
    size = (0, 0)
    for item in result:
        region = regions.setdefault(str(item.get("id")), {})
        value = item.get("value", {})
        kind = item.get("type")
        if kind == "rectanglelabels":
            region["box"] = value
            region["labels"] = value.get("rectanglelabels", [])
            size = (int(item.get("original_width", 0)), int(item.get("original_height", 0)))
        elif kind == "textarea":
            region["text"] = "\n".join(value.get("text", []))
        elif kind == "number":
            region["order"] = value.get("number")
    if not size[0] or not size[1]:
        return None, [f"{where}: no labelled region"]
    page = Page(doc_id, page_no, *size)
    problems: list[str] = []
    for rid, region in regions.items():
        at = f"{where} region {rid}"
        box = region.get("box")
        if box is None:
            problems.append(f"{at}: text or order without a box")
            continue
        if region["labels"] == [] or len(region["labels"]) != 1:
            problems.append(f"{at}: needs exactly one label, has {region['labels']}")
            continue
        label = region["labels"][0]
        if label not in LABELS:
            problems.append(f"{at}: unknown label {label!r} (page.xml and LABELS differ)")
            continue
        if box.get("rotation"):
            problems.append(f"{at}: rotated box; draw it upright")
            continue
        order = region.get("order")
        if not isinstance(order, int | float) or order != int(order) or order < 1:
            problems.append(f"{at}: no reading order")
            continue
        kind, level = LABELS[label]
        text = nfc(region.get("text", ""))
        if kind not in NO_TEXT and not text:
            problems.append(f"{at}: {label} without its text")
            continue
        x0, y0 = box["x"] / 100 * size[0], box["y"] / 100 * size[1]
        bbox = (x0, y0, x0 + box["width"] / 100 * size[0], y0 + box["height"] / 100 * size[1])
        page.elements.append(Element(int(order), label, kind, level, bbox, text))
    orders = sorted(e.order for e in page.elements)
    if orders and orders != list(range(1, len(orders) + 1)):
        problems.append(f"{where}: reading order is {orders}, should be 1..{len(orders)} once each")
    page.elements.sort(key=lambda e: e.order)
    return page, problems


def gold(export: list[dict[str, Any]], docs: Path, annotator: str, out: Path) -> list[str]:
    """Write <out>/<doc_id>/{layout,tables,headings}.json and ocr/page-NNN.txt."""
    pages, problems = pages_of(export, annotator)
    by_doc: dict[str, list[Page]] = {}
    for page in pages:
        by_doc.setdefault(page.doc_id, []).append(page)
    written = []
    for doc_id, doc_pages in by_doc.items():
        sheets_dir = docs / doc_id / "tables" / annotator
        tables, found = _tables(doc_pages, sheets_dir, doc_id)
        problems += found
        if found:
            continue
        written.append((doc_id, doc_pages, tables))
    if problems:
        raise Invalid(problems)
    for doc_id, doc_pages, tables in written:
        target = out / doc_id
        (target / "ocr").mkdir(parents=True, exist_ok=True)
        _write(
            target / "layout.json",
            {
                "doc_id": doc_id,
                "annotator": annotator,
                "pages": [
                    {
                        "page": p.page,
                        "width": p.width,
                        "height": p.height,
                        "elements": [e.as_json() for e in p.elements],
                    }
                    for p in doc_pages
                ],
            },
        )
        _write(target / "tables.json", tables)
        _write(
            target / "headings.json",
            [
                {"level": e.level, "label": e.label, "text": e.text, "page": p.page}
                for p in doc_pages
                for e in p.elements
                if e.level is not None
            ],
        )
        for p in doc_pages:
            (target / "ocr" / f"page-{p.page:03d}.txt").write_text(
                "\n".join(_page_text(e) for e in p.elements if _page_text(e)) + "\n",
                encoding="utf-8",
            )
    return [doc_id for doc_id, _, _ in written]


def _tables(
    pages: list[Page], sheets_dir: Path, doc_id: str
) -> tuple[list[dict[str, Any]], list[str]]:
    problems: list[str] = []
    tables: list[dict[str, Any]] = []
    named: set[str] = set()
    for page in pages:
        for element in page.elements:
            if element.kind != "table":
                continue
            where = f"{doc_id} p.{page.page} table #{element.order}"
            ref = TABLE_REF.match(element.text)
            if ref is None:
                problems.append(
                    f"{where}: text {element.text!r} should name its sheet: t1; header=1"
                )
                continue
            sheet, header_rows = ref.group(1), int(ref.group(2))
            if sheet in named:
                problems.append(f"{where}: sheet {sheet} is named by two tables")
                continue
            named.add(sheet)
            path = sheets_dir / f"{sheet}.csv"
            if not path.is_file():
                problems.append(f"{where}: no sheet {path}")
                continue
            try:
                cells = table_cells(read_sheet(path), header_rows, f"{doc_id}/{sheet}.csv")
            except Invalid as e:
                problems += e.problems
                continue
            element.table = {
                "id": sheet,
                "page": page.page,
                "order": element.order,
                "bbox": [round(v, 1) for v in element.bbox],
                "header_rows": header_rows,
                "rows": max(c.row + c.rowspan for c in cells),
                "cols": max(c.col + c.colspan for c in cells),
                "cells": [c.as_json() for c in cells],
                "html": table_html(cells),
            }
            tables.append(element.table)
    if sheets_dir.is_dir():
        problems += [
            f"{doc_id}: sheet {p.name} is named by no table region"
            for p in sorted(sheets_dir.glob("*.csv"))
            if p.stem not in named
        ]
    return tables, problems


def _page_text(element: Element) -> str:
    """What OCR should read for the element: its text, a table row by row."""
    if element.table is not None:
        rows: dict[int, list[dict[str, Any]]] = {}
        for cell in element.table["cells"]:
            rows.setdefault(cell["row"], []).append(cell)
        return "\n".join(
            " ".join(c["text"] for c in sorted(rows[r], key=lambda c: c["col"]) if c["text"])
            for r in sorted(rows)
        )
    return element.text if element.kind not in NO_TEXT else ""


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# ─── Two annotators ──────────────────────────────────────────────────────────


def _iou(a: list[float], b: list[float]) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    area = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / area if area else 0.0


def edit_distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def agree(a: Path, b: Path, min_iou: float = 0.5) -> dict[str, Any]:
    """Where two annotators' gold differ: unmatched boxes, labels, texts, order, table cells."""
    report: dict[str, Any] = {"docs": {}, "differences": 0}
    docs = sorted(
        {p.name for p in a.iterdir() if p.is_dir()} | {p.name for p in b.iterdir() if p.is_dir()}
    )
    for doc_id in docs:
        found: list[str] = []
        la, lb = a / doc_id / "layout.json", b / doc_id / "layout.json"
        if not la.is_file() or not lb.is_file():
            found.append("labelled by one annotator only")
        else:
            found += _agree_layout(
                json.loads(la.read_text("utf-8")), json.loads(lb.read_text("utf-8")), min_iou
            )
            found += _agree_tables(
                json.loads((a / doc_id / "tables.json").read_text("utf-8")),
                json.loads((b / doc_id / "tables.json").read_text("utf-8")),
            )
        report["docs"][doc_id] = found
        report["differences"] += len(found)
    return report


def _agree_layout(a: dict[str, Any], b: dict[str, Any], min_iou: float) -> list[str]:
    found: list[str] = []
    pages_b = {p["page"]: p for p in b["pages"]}
    for pa in a["pages"]:
        pb = pages_b.pop(pa["page"], None)
        if pb is None:
            found.append(f"p.{pa['page']}: only in A")
            continue
        pairs, left = [], list(pb["elements"])
        for ea in pa["elements"]:
            best = max(left, key=lambda eb: _iou(ea["bbox"], eb["bbox"]), default=None)
            if best is None or _iou(ea["bbox"], best["bbox"]) < min_iou:
                found.append(f"p.{pa['page']} A#{ea['order']} {ea['label']}: no matching box in B")
                continue
            left.remove(best)
            pairs.append((ea, best))
        found += [
            f"p.{pa['page']} B#{eb['order']} {eb['label']}: no matching box in A" for eb in left
        ]
        for ea, eb in pairs:
            at = f"p.{pa['page']} A#{ea['order']}/B#{eb['order']}"
            if ea["label"] != eb["label"]:
                found.append(f"{at}: label {ea['label']} vs {eb['label']}")
            if ea["kind"] != "table" and ea["text"] != eb["text"]:
                d = edit_distance(ea["text"], eb["text"])
                found.append(
                    f"{at}: text differs ({d} edits: {ea['text'][:40]!r} vs {eb['text'][:40]!r})"
                )
        # Reading order: the matched boxes must come in the same sequence.
        ranked = sorted(pairs, key=lambda p: p[0]["order"])
        seq_b = [eb["order"] for _, eb in ranked]
        if seq_b != sorted(seq_b):
            found.append(
                f"p.{pa['page']}: reading order differs (B's order of A's sequence: {seq_b})"
            )
    found += [f"p.{p}: only in B" for p in pages_b]
    return found


def _agree_tables(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> list[str]:
    found: list[str] = []
    by_place = {(t["page"], tuple(t["bbox"])): t for t in b}
    for ta in a:
        tb = max(
            (t for t in b if t["page"] == ta["page"]),
            key=lambda t: _iou(ta["bbox"], t["bbox"]),
            default=None,
        )
        if tb is None or _iou(ta["bbox"], tb["bbox"]) < 0.5:
            found.append(f"table {ta['id']} p.{ta['page']}: no matching table in B")
            continue
        by_place.pop((tb["page"], tuple(tb["bbox"])), None)
        at = f"table {ta['id']}/{tb['id']} p.{ta['page']}"
        if (ta["rows"], ta["cols"], ta["header_rows"]) != (
            tb["rows"],
            tb["cols"],
            tb["header_rows"],
        ):
            found.append(
                f"{at}: {ta['rows']}x{ta['cols']} header={ta['header_rows']}"
                f" vs {tb['rows']}x{tb['cols']} header={tb['header_rows']}"
            )
            continue
        cells_b = {(c["row"], c["col"]): c for c in tb["cells"]}
        for ca in ta["cells"]:
            cb = cells_b.get((ca["row"], ca["col"]))
            ref = _ref(ca["row"], ca["col"])
            if cb is None or (ca["rowspan"], ca["colspan"]) != (cb["rowspan"], cb["colspan"]):
                found.append(f"{at} {ref}: spans differ")
            elif ca["text"] != cb["text"]:
                found.append(f"{at} {ref}: {ca['text']!r} vs {cb['text']!r}")
    found += [f"table {t['id']} p.{t['page']}: no matching table in A" for t in by_place.values()]
    return found


# ─── Corpus ──────────────────────────────────────────────────────────────────


def originals(docs: Path) -> dict[str, Path]:
    """<doc_id>/original.<ext> of each document; a DOCX is rendered from its original.pdf."""
    found: dict[str, Path] = {}
    for d in sorted(p for p in docs.iterdir() if p.is_dir()):
        if not DOC_ID.match(d.name):
            continue
        files = sorted(d.glob("original.*"))
        if files:
            found[d.name] = files[0]
    return found


def render(docs: Path, dpi: int = 150) -> list[dict[str, Any]]:
    """Page images under <doc>/pages/ and the Label Studio tasks that show them."""
    import pypdfium2 as pdfium
    from PIL import Image

    tasks: list[dict[str, Any]] = []
    for doc_id, original in originals(docs).items():
        pages_dir = docs / doc_id / "pages"
        pages_dir.mkdir(exist_ok=True)
        pdf = original.with_suffix(".pdf")
        images: list[Any] = []
        if pdf.is_file():
            document = pdfium.PdfDocument(str(pdf))
            images = [page.render(scale=dpi / 72).to_pil() for page in document]
        elif original.suffix.lower() in (".png", ".jpg", ".jpeg", ".tif", ".tiff"):
            with Image.open(original) as img:
                for frame in range(getattr(img, "n_frames", 1)):
                    img.seek(frame)
                    images.append(img.convert("RGB"))
        else:
            print(f"{doc_id}: export {original.name} to original.pdf to label it", file=sys.stderr)
            continue
        for n, image in enumerate(images, 1):
            name = PAGE_IMAGE.format(n)
            image.save(pages_dir / name)
            tasks.append(
                {
                    "data": {
                        "image": f"/data/local-files/?d={SERVED_AS}/{doc_id}/pages/{name}",
                        "doc_id": doc_id,
                        "page": n,
                    }
                }
            )
    return tasks


def manifest(docs: Path, previous: dict[str, Any]) -> dict[str, Any]:
    """The originals by sha256; source and licence are a person's to fill and are kept."""
    out: dict[str, Any] = {}
    for doc_id, original in originals(docs).items():
        data = original.read_bytes()
        pages_dir = docs / doc_id / "pages"
        kept = previous.get(doc_id, {})
        out[doc_id] = {
            "file": original.name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "pages": len(list(pages_dir.glob("page-*.png"))) if pages_dir.is_dir() else None,
            "kind": kept.get("kind", ""),
            "source": kept.get("source", ""),
            "licence": kept.get("licence", ""),
        }
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("render")
    p.add_argument("--docs", type=Path, required=True)
    p.add_argument("--dpi", type=int, default=150)
    p = sub.add_parser("manifest")
    p.add_argument("--docs", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("gold")
    p.add_argument("--docs", type=Path, required=True)
    p.add_argument("--export", type=Path, required=True)
    p.add_argument("--annotator", required=True, help="Label Studio user id or email")
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("agree")
    p.add_argument("--a", type=Path, required=True)
    p.add_argument("--b", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "render":
        tasks = render(args.docs, args.dpi)
        _write(args.docs / "tasks.json", tasks)
        print(f"{len(tasks)} pages; import {args.docs / 'tasks.json'}", file=sys.stderr)
    elif args.command == "manifest":
        previous = json.loads(args.out.read_text("utf-8")) if args.out.is_file() else {}
        _write(args.out, manifest(args.docs, previous))
    elif args.command == "gold":
        try:
            done = gold(
                json.loads(args.export.read_text("utf-8")), args.docs, args.annotator, args.out
            )
        except Invalid as e:
            print("\n".join(e.problems), file=sys.stderr)
            print(f"{len(e.problems)} problems; nothing written", file=sys.stderr)
            return 1
        print(f"gold for {len(done)} documents in {args.out}", file=sys.stderr)
    else:
        report = agree(args.a, args.b)
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 1 if report["differences"] else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
