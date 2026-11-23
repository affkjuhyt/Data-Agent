"""Docling as the extraction backend (DI-04, DOCUMENT_INTELLIGENCE_SPEC §3.2, §4.2).

`convert` runs Docling's PDF pipeline (layout, TableFormer, OCR) or its DOCX/HTML/image backends on
a file and returns the DoclingDocument; `elements_of` turns that document into the elements the
worker stores (§4.1): reading order, kind, heading level, page, bbox, text, the caption's parent.
Tables become `table`/`table_cell` in tables.py (DI-07); headings are re-levelled and chunked in
legal_headings.py (DI-08); the handler wires them (DI-09). Until the bake-off (DI-05) has its
numbers, the handler keeps the pdfminer pipeline: `extractor()` is `legacy` unless DOC_EXTRACTOR
says `docling` (§11: no Docling on the default path before the bake-off table exists).

The models are read from DOCLING_ARTIFACTS_PATH (the image, DI-03); the cluster has no egress.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

EXTRACTOR_VERSION = "docling-2.150.0+adapter-1"
LEGACY_VERSION = "legacy-pdfminer-1"

Extractor = Literal["legacy", "docling"]


def extractor() -> Extractor:
    """Which backend extracts documents: DOC_EXTRACTOR=docling, else the pdfminer pipeline."""
    return (
        "docling" if os.environ.get("DOC_EXTRACTOR", "").strip().lower() == "docling" else "legacy"
    )


@dataclass(frozen=True)
class Options:
    """What a collection's ingest profile says about reading its files (§3.2)."""

    ocr_engine: Literal["tesseract", "rapidocr"] = "tesseract"
    # Tesseract's language packs (the image installs vie and eng, Dockerfile OCR_LANGS).
    ocr_languages: tuple[str, ...] = ("vie", "eng")
    # A scan: OCR the whole page, not only the bitmap regions the layout finds.
    force_full_page_ocr: bool = False
    max_seconds: float = 1800.0
    threads: int = 4


def converter_for(options: Options) -> Any:
    from docling.backend.docling_parse_v4_backend import DoclingParseV4DocumentBackend
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        HeadingHierarchyOptions,
        OcrMode,
        PdfPipelineOptions,
        RapidOcrOptions,
        TableFormerMode,
        TesseractCliOcrOptions,
    )
    from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption

    mode = OcrMode.FULL_PAGE if options.force_full_page_ocr else OcrMode.DEFAULT
    ocr: Any = (
        RapidOcrOptions(mode=mode)
        if options.ocr_engine == "rapidocr"
        else TesseractCliOcrOptions(lang=list(options.ocr_languages), mode=mode)
    )
    # A scanned PDF often carries a garbage text layer (common in Vietnamese scans): read it again.
    ocr.replace_broken_text_layer = True
    pipeline = PdfPipelineOptions(
        do_ocr=True,
        do_table_structure=True,
        ocr_options=ocr,
        heading_hierarchy_options=HeadingHierarchyOptions(enabled=True, max_level=6),
        artifacts_path=os.environ.get("DOCLING_ARTIFACTS_PATH"),
        accelerator_options=AcceleratorOptions(
            num_threads=options.threads, device=AcceleratorDevice.CPU
        ),
        document_timeout=options.max_seconds,
        # The page images the viewer shows (144 dpi, as the legacy pipeline renders them).
        generate_page_images=True,
        images_scale=2.0,
    )
    pipeline.table_structure_options.mode = TableFormerMode.ACCURATE
    pipeline.table_structure_options.do_cell_matching = True
    pdf = PdfFormatOption(pipeline_options=pipeline, backend=DoclingParseV4DocumentBackend)
    image = ImageFormatOption(pipeline_options=pipeline)
    return DocumentConverter(format_options={InputFormat.PDF: pdf, InputFormat.IMAGE: image})


def convert(path: Path, options: Options) -> Any:
    """The DoclingDocument of a file; raises when Docling fails or gives up (the handler falls
    back to the legacy pipeline with a quality issue, §3.4)."""
    result = converter_for(options).convert(path, raises_on_error=True)
    status = getattr(result.status, "value", str(result.status))
    if status not in ("success", "partial_success"):
        raise RuntimeError(f"docling: {status}")
    return result.document


# --------------------------------------------------------------------- the element mapping

Kind = Literal[
    "text", "heading", "list", "table", "picture", "caption", "footnote", "formula",
    "header", "footer", "code", "page_number",
]  # fmt: skip

_KIND: dict[str, Kind] = {
    "title": "heading",
    "section_header": "heading",
    "text": "text",
    "paragraph": "text",
    "reference": "text",
    "handwritten_text": "text",
    "list_item": "list",
    "table": "table",
    "picture": "picture",
    "chart": "picture",
    "caption": "caption",
    "footnote": "footnote",
    "formula": "formula",
    "code": "code",
    "page_header": "header",
    "page_footer": "footer",
}
# Not elements: page furniture (counted, §4.2) and a table of contents, which is navigation.
_SKIPPED_KINDS = frozenset({"header", "footer", "page_number"})
_SKIPPED_LABELS = frozenset({"document_index"})
# A caption Docling read as plain text ("Bảng 1. …", "Hình 2: …", "Table 3 - …"), next to its table
# or picture: a caption of that element (§4.2; Docling links captions on PDFs, not on DOCX).
_CAPTION = re.compile(
    r"^(bảng|biểu|hình|sơ đồ|table|figure|chart|exhibit)\s+[\dIVXivx]+[.:\u2013\-\s]", re.I
)


@dataclass
class Element:
    ref: str  # the DoclingDocument item (#/texts/3): tables.py ties a table to its element by it
    order_idx: int
    kind: Kind
    text: str
    page_no: int | None = None  # None for a format without pages (DOCX, HTML)
    level: int | None = None  # 1..6 for a heading
    bbox: dict[str, Any] | None = None  # {l, t, r, b, coord_origin}
    parent_ref: str | None = None  # a caption's table or picture
    confidence: float | None = None  # the page's OCR confidence when the page was OCRed
    extractor_version: str = EXTRACTOR_VERSION


@dataclass
class Extracted:
    elements: list[Element]
    # What was left out by kind (page headers and footers, a table of contents), for debugging.
    skipped: Counter[str] = field(default_factory=Counter)


def elements_of(doc: Any, *, ocr_pages: Mapping[int, float] | None = None) -> Extracted:
    """The document's elements in reading order (the body as Docling iterates it).

    `ocr_pages` maps a page that was OCRed to its mean OCR confidence: its elements carry it."""
    elements: list[Element] = []
    skipped: Counter[str] = Counter()
    for item, _depth in doc.iterate_items(with_groups=False):
        label = getattr(item.label, "value", str(item.label))
        kind = _KIND.get(label, "text")
        if label in _SKIPPED_LABELS or kind in _SKIPPED_KINDS:
            skipped[label] += 1
            continue
        prov = item.prov[0] if getattr(item, "prov", None) else None
        page = prov.page_no if prov is not None else None
        elements.append(
            Element(
                ref=item.self_ref,
                order_idx=len(elements),
                kind=kind,
                text=_text(item),
                page_no=page,
                level=_level(item, label) if kind == "heading" else None,
                bbox=_bbox(prov.bbox) if prov is not None else None,
                confidence=(ocr_pages or {}).get(page) if page is not None else None,
            )
        )
    _link_captions(doc, elements)
    return Extracted(elements, skipped)


def _text(item: Any) -> str:
    text = getattr(item, "text", None)
    return text.strip() if isinstance(text, str) else ""


def _level(item: Any, label: str) -> int:
    # A document's title is its top level; a section header carries the level Docling inferred
    # (its numbering, style or outline), 1-based. legal_headings.py re-levels them (DI-08).
    if label == "title":
        return 1
    level = getattr(item, "level", None)
    return min(max(int(level), 1), 6) if isinstance(level, int) else 1


def _bbox(box: Any) -> dict[str, Any]:
    origin = getattr(box.coord_origin, "value", str(box.coord_origin))
    return {"l": box.l, "t": box.t, "r": box.r, "b": box.b, "coord_origin": origin}


def _link_captions(doc: Any, elements: Sequence[Element]) -> None:
    by_ref = {e.ref: e for e in elements}
    # The captions Docling linked on a table or picture.
    for owner in [*getattr(doc, "tables", []), *getattr(doc, "pictures", [])]:
        for caption in getattr(owner, "captions", []) or []:
            element = by_ref.get(getattr(caption, "cref", ""))
            if element is not None and owner.self_ref in by_ref:
                element.kind, element.parent_ref = "caption", owner.self_ref
    # A caption it read as text: the "Bảng 1." just before or after a table or picture.
    for i, element in enumerate(elements):
        if element.kind != "text" or element.parent_ref or not _CAPTION.match(element.text):
            continue
        for near in (elements[i + 1] if i + 1 < len(elements) else None,
                     elements[i - 1] if i > 0 else None):  # fmt: skip
            if near is not None and near.kind in ("table", "picture") and _same_page(element, near):
                element.kind, element.parent_ref = "caption", near.ref
                break


def _same_page(a: Element, b: Element) -> bool:
    return a.page_no is None or b.page_no is None or a.page_no == b.page_no
