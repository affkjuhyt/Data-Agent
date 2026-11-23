"""Docling's reading of one file, ready for the handler to write (DI-09, DOCUMENT_INTELLIGENCE_SPEC §3 to §5).

`extract` runs the stages the handler does not: Docling (docling_adapter), the legal re-levelling
and the furniture left out (legal_headings), the tables and their numbers (tables), the masking
of personal data in every element and cell under the collection's policy, and the chunks
(chunker). It returns the pages as the legacy pipeline does (text, image, layout blocks, OCR
confidence), so the page rows, their low_ocr issues and the derived images are written the
same way whichever extractor read the file, plus the elements, tables and chunks.

It runs in a thread: Docling is CPU work that would hold the event loop for seconds a page.
"""

from __future__ import annotations

import io
import math
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from services.worker_document import chunker, docling_adapter, legal_headings, quality, tables
from services.worker_document.docling_adapter import Element
from services.worker_document.processing import ExtractedPage, LayoutBlock

# The media Docling reads (§2); XLSX, text and the rest stay on their own path.
DOCLING_MEDIA = frozenset({"pdf", "docx", "html", "image"})
# The suffix Docling detects a format by.
_SUFFIX = {"pdf": ".pdf", "docx": ".docx", "html": ".html", "image": ".png"}


@dataclass
class Extraction:
    pages: list[ExtractedPage]
    elements: list[Element]
    tables: list[tables.Table]
    chunks: list[chunker.Chunk]
    extractor_version: str = docling_adapter.EXTRACTOR_VERSION
    dropped: dict[str, int] = field(default_factory=dict)
    # document.element.extractor: docling, or openpyxl for a workbook (xlsx_path).
    extractor: str = "docling"
    # Elements whose text is garbled (quality.garbled, DI-13): stored, kept out of the chunks,
    # each a garbled_text issue: (page, the start of its text).
    garbled: list[tuple[int | None, str]] = field(default_factory=list)


def handles(media: str) -> bool:
    return media in DOCLING_MEDIA


def extract(
    content: bytes,
    media: str,
    file_name: str,
    options: docling_adapter.Options,
    *,
    mask: Callable[[str], str],
    locale: str | None,
    decimal_mark: str | None,
    title: str | None,
    doc_no: str | None,
) -> Extraction:
    suffix = Path(file_name).suffix.lower() or _SUFFIX.get(media, "")
    # Docling reads a path; the file lives only in this pod's /tmp (an emptyDir) while it does.
    with tempfile.NamedTemporaryFile(suffix=suffix) as handle:
        handle.write(content)
        handle.flush()
        result = docling_adapter.convert_result(Path(handle.name), options)
    doc = result.document
    ocr = _ocr_confidence(result)
    heights = {n: float(p.size.height) for n, p in (doc.pages or {}).items()}
    read = docling_adapter.elements_of(doc, ocr_pages=ocr)
    elements = legal_headings.relevel(read.elements, legal_headings.language_of(locale))
    elements, dropped = legal_headings.drop_furniture(elements, heights)
    found = tables.tables_of(doc, elements, decimal_mark=decimal_mark)
    # Personal data is masked before anything is stored or chunked; a cell whose text the mask
    # changed is no figure any more (an ID number is not a quantity).
    elements = [replace(e, text=mask(e.text)) if e.text else e for e in elements]
    # Boxes are stored top-left, as the viewer draws a page (DI-12: a cited cell is outlined
    # where it stands): Docling gives a PDF's in PDF space, origin bottom left.
    elements = [replace(e, bbox=_top_left(e.bbox, heights.get(e.page_no or 0))) for e in elements]
    for table in found:
        for cell in table.cells:
            cell.bbox = _top_left(cell.bbox, heights.get(cell.page_no or 0))
        table.caption = mask(table.caption) if table.caption else table.caption
        for cell in table.cells:
            masked = mask(cell.text) if cell.text else cell.text
            if masked != cell.text:
                cell.text, cell.numeric_value, cell.unit = masked, None, None
    bad = {
        e.ref
        for e in elements
        if e.kind in ("text", "heading", "list", "caption") and quality.garbled(e.text)
    }
    chunks = chunker.chunks_of(
        [e for e in elements if e.ref not in bad], found, title=title, doc_no=doc_no
    )
    return Extraction(
        pages=_pages(doc, elements, ocr),
        elements=elements,
        tables=found,
        chunks=chunks,
        dropped={**read.skipped, **dropped},
        garbled=[(e.page_no, e.text[:120]) for e in elements if e.ref in bad],
    )


def _top_left(bbox: dict[str, Any] | None, height: float | None) -> dict[str, Any] | None:
    if bbox is None or not height or str(bbox.get("coord_origin", "")).upper() != "BOTTOMLEFT":
        return bbox
    top, bottom = height - float(bbox["t"]), height - float(bbox["b"])
    return {
        "l": float(bbox["l"]),
        "t": min(top, bottom),
        "r": float(bbox["r"]),
        "b": max(top, bottom),
        "coord_origin": "TOPLEFT",
    }


def _ocr_confidence(result: Any) -> dict[int, float]:
    """Each OCRed page's OCR score (Docling reports NaN for a page it did not OCR)."""
    pages = getattr(getattr(result, "confidence", None), "pages", None) or {}
    out: dict[int, float] = {}
    for page_no, scores in pages.items():
        score = getattr(scores, "ocr_score", None)
        if isinstance(score, int | float) and not math.isnan(score):
            out[int(page_no)] = float(score)  # 1-based, as the document's pages
    return out


def _pages(doc: Any, elements: list[Element], ocr: dict[int, float]) -> list[ExtractedPage]:
    """The pages in the legacy shape. A format without pages (DOCX, HTML) is one page."""
    numbers = sorted(doc.pages) if doc.pages else [1]
    out = []
    for n in numbers:
        page = doc.pages.get(n) if doc.pages else None
        height = float(page.size.height) if page is not None else 0.0
        width = float(page.size.width) if page is not None else 0.0
        on_page = [e for e in elements if (e.page_no or 1) == n]
        out.append(
            ExtractedPage(
                text="\n\n".join(e.text for e in on_page if e.text and e.kind != "table"),
                image=_png(page),
                text_layer=n not in ocr,
                ocr_conf=ocr.get(n),
                blocks=[b for e in on_page if (b := _block(e, height)) is not None],
                images=[],
                width=width,
                height=height,
            )
        )
    return out


def _png(page: Any) -> bytes | None:
    image = getattr(getattr(page, "image", None), "pil_image", None)
    if image is None:
        return None
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _block(e: Element, height: float) -> LayoutBlock | None:
    """An element as the viewer draws a block: top-left origin, as the legacy layout is."""
    if e.bbox is None:
        return None
    left, right = float(e.bbox["l"]), float(e.bbox["r"])
    top, bottom = float(e.bbox["t"]), float(e.bbox["b"])
    if str(e.bbox.get("coord_origin", "")).upper() == "BOTTOMLEFT":
        top, bottom = height - top, height - bottom
    kind = "heading" if e.kind == "heading" else "picture" if e.kind == "picture" else (
        "table" if e.kind == "table" else "paragraph"
    )  # fmt: skip
    return LayoutBlock(kind, e.text, (left, min(top, bottom), right, max(top, bottom)), e.level)
