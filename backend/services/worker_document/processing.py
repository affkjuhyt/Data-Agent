from __future__ import annotations

import functools
import hashlib
import io
import json
import logging
import re
import subprocess
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from statistics import median

import pypdfium2
from pdfminer.high_level import extract_pages as pdfminer_pages
from pdfminer.layout import LAParams, LTChar, LTPage, LTTextBox, LTTextLine

MAX_CHUNK_CHARS = 4_000
# BR-19: a page read by OCR below this confidence is not used until a person reviews it.
OCR_THRESHOLD = 0.85
OCR_DPI = 300
# The page image kept for review and the viewer.
PREVIEW_DPI = 144
OCR_TIMEOUT_S = 120
# The languages read when no ingest profile names any (libs/tabular.profile resolves one).
DEFAULT_OCR_LANGUAGES = ("vie", "eng")

log = logging.getLogger(__name__)

# Structure of Vietnamese legal text (UC-05-02 step 5): an Điều is the unit a citation names, so a
# chunk never starts inside one; a Khoản ("1.", "2.") is the only place a long Điều may be cut.
_PART = re.compile(r"^(?:phần|chương)\s+(?:[ivxlc]+|\d+)\b", re.IGNORECASE)
_SECTION = re.compile(r"^mục\s+\d+\b", re.IGNORECASE)
_ARTICLE = re.compile(r"^điều\s+(\d+[a-zđ]?)\b", re.IGNORECASE)
_CLAUSE = re.compile(r"^(\d+)\.\s")


@dataclass(frozen=True)
class ChunkDraft:
    text: str
    page_from: int
    page_to: int
    section_path: str | None


@dataclass(frozen=True)
class ExtractedPage:
    text: str
    image: bytes | None
    text_layer: bool
    ocr_conf: float | None
    blocks: list[LayoutBlock]
    images: list[ExtractedImage]
    width: float
    height: float


@dataclass(frozen=True)
class LayoutBlock:
    kind: str
    text: str
    box: tuple[float, float, float, float]
    level: int | None = None


@dataclass(frozen=True)
class ExtractedImage:
    data: bytes
    extension: str
    box: tuple[float, float, float, float]


def _chunks(text: str, limit: int = MAX_CHUNK_CHARS) -> Iterable[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    current = ""
    for paragraph in paragraphs:
        while len(paragraph) > limit:
            cut = paragraph.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            yield (f"{current}\n{paragraph[:cut]}" if current else paragraph[:cut]).strip()
            current = ""
            paragraph = paragraph[cut:].lstrip()
        if not paragraph:
            continue
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                yield current.strip()
            current = paragraph
    if current:
        yield current.strip()


def extract_pages(
    content: bytes,
    media: str,
    file_name: str = "",
    ocr_languages: Sequence[str] = DEFAULT_OCR_LANGUAGES,
) -> list[ExtractedPage]:
    """Extract deterministic page text for the first production document path.

    PDF text extraction is intentionally local and deterministic; OCR for scanned pages is a
    separate quality path and must not pretend an image was successfully indexed as text.
    """
    if media == "pdf":
        return _pdf_pages(content, ocr_languages)
    # extractors builds on this module's page types, hence the import here rather than at the top.
    from services.worker_document import extractors

    if media == "html":
        return extractors.html_pages(content)
    if media == "docx":
        return extractors.docx_pages(content)
    if media == "email":
        return extractors.email_pages(content, file_name)
    if media == "image":
        return extractors.image_pages(content, ocr_languages)
    if media == "audio":
        raise ValueError("Recordings are not transcribed yet")
    raise ValueError(f"No text extractor is configured for media={media}")


def _pdf_pages(content: bytes, ocr_languages: Sequence[str]) -> list[ExtractedPage]:
    """A PDF's pages: the text layer's blocks from pdfminer.six (lines, boxes and font sizes, from
    which headings are judged), the page image and its embedded pictures from pdfium. A page whose
    text layer holds almost nothing is a scan: it is read by OCR from the 300 dpi rendering.

    Both libraries are permissive (MIT; Apache-2.0/BSD-3). PyMuPDF, which this replaced, is AGPL.
    """
    result: list[ExtractedPage] = []
    pdf = pypdfium2.PdfDocument(content)
    try:
        layouts = pdfminer_pages(io.BytesIO(content), laparams=LAParams())
        for index, layout in enumerate(layouts):
            if index >= len(pdf):
                break
            page = pdf[index]
            try:
                result.append(_pdf_page(pdf, page, layout, ocr_languages))
            finally:
                page.close()
    finally:
        pdf.close()
    return result


def _pdf_page(
    pdf: pypdfium2.PdfDocument,
    page: pypdfium2.PdfPage,
    layout: LTPage,
    ocr_languages: Sequence[str],
) -> ExtractedPage:
    width, height = page.get_size()
    frame = _PageFrame(page, layout)
    blocks = _native_layout(layout, frame)
    text = "\n".join(block.text for block in blocks).strip()
    text_layer = len(text) >= 20
    ocr_conf = None
    images: list[ExtractedImage] = []
    if not text_layer:
        text, ocr_conf = ocr_page(_render_png(page, OCR_DPI), ocr_languages)
        blocks = [LayoutBlock("paragraph", text, (0, 0, width, height))] if text else []
        text = _join_wrapped_lines(text)
    else:
        for picture in page.get_objects(filter=[pypdfium2.raw.FPDF_PAGEOBJ_IMAGE]):
            embedded = _embedded_image(picture, frame)
            if embedded is not None:
                images.append(embedded)
                blocks.append(LayoutBlock("picture", "", embedded.box))
        text = _layout_text(blocks)
    return ExtractedPage(
        normalize_text(text),
        _render_png(page, PREVIEW_DPI),
        text_layer,
        ocr_conf,
        blocks,
        images,
        width,
        height,
    )


class _PageFrame:
    """From PDF coordinates (origin bottom left) to the page's own (origin top left of the area a
    viewer shows), as the boxes of the layout and the page image are drawn.

    pdfminer lays a page out in its media box, turned by /Rotate (the matrix of its
    PDFPageInterpreter.process_page); pdfium reports a picture's bounds in the unturned user
    space. Both go through that matrix, then are flipped and moved to the crop box, so a box sits
    on its text on a cropped or turned page too.
    """

    def __init__(self, page: pypdfium2.PdfPage, layout: LTPage) -> None:
        x0, y0, x1, y1 = page.get_mediabox()
        rotation = page.get_rotation() % 360
        self._ctm = {
            90: (0.0, -1.0, 1.0, 0.0, -y0, x1),
            180: (-1.0, 0.0, 0.0, -1.0, x1, y1),
            270: (0.0, 1.0, -1.0, 0.0, y1, -x0),
        }.get(rotation, (1.0, 0.0, 0.0, 1.0, -x0, -y0))
        self._top = layout.y1
        # The crop box's offset inside the media box; a turned page is read uncropped.
        crop_left, _, _, crop_top = page.get_cropbox()
        self._dx: float = crop_left - x0 if rotation == 0 else 0.0
        self._dy: float = y1 - crop_top if rotation == 0 else 0.0

    def layout_box(
        self, bbox: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        """A pdfminer box, already in the turned media frame."""
        x0, y0, x1, y1 = bbox
        return (x0 - self._dx, self._top - y1 - self._dy, x1 - self._dx, self._top - y0 - self._dy)

    def user_box(
        self, bounds: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        """A pdfium box (left, bottom, right, top) in user space."""
        a, b, c, d, e, f = self._ctm
        left, bottom, right, top = bounds
        xs, ys = [], []
        for x, y in ((left, bottom), (right, top)):
            xs.append(a * x + c * y + e)
            ys.append(b * x + d * y + f)
        return self.layout_box((min(xs), min(ys), max(xs), max(ys)))


def _render_png(page: pypdfium2.PdfPage, dpi: int) -> bytes:
    # pdfium fills the page white first, so a transparent page reads as paper, not black.
    image = page.render(scale=dpi / 72).to_pil()
    out = io.BytesIO()
    image.convert("RGB").save(out, format="PNG")
    return out.getvalue()


def _embedded_image(picture: pypdfium2.PdfImage, frame: _PageFrame) -> ExtractedImage | None:
    """A picture of the page as its own file: JPEG and JPEG 2000 as stored, anything else
    decoded and written as PNG. One pdfium cannot decode is left out, not failed."""
    out = io.BytesIO()
    try:
        picture.extract(out, fb_format="png")
    except (pypdfium2.PdfiumError, OSError, ValueError):
        log.warning("an embedded image could not be extracted", exc_info=True)
        return None
    data = out.getvalue()
    if not data:
        return None
    return ExtractedImage(data, _image_extension(data), frame.user_box(picture.get_bounds()))


def _image_extension(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x00\x00\x00\x0cjP  ") or data.startswith(b"\xff\x4f\xff\x51"):
        return "jpx"
    return "png"


def page_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def source_hash(page: ExtractedPage) -> str:
    """What "the page did not change" is judged on when a document is processed again (BR-108):
    the text of a page with a text layer, the rendered image of a scanned one, since OCR of the
    same image may read a character differently from one run to the next."""
    if page.text_layer or page.image is None:
        return page_hash(page.text)
    return hashlib.sha256(page.image).hexdigest()


@functools.cache
def installed_ocr_languages() -> frozenset[str]:
    """The tesseract language packs this image has (Dockerfile OCR_LANGS)."""
    try:
        done = subprocess.run(
            ["tesseract", "--list-langs"], capture_output=True, timeout=30, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    return frozenset(line.strip() for line in done.stdout.decode().splitlines()[1:] if line.strip())


def ocr_language_arg(languages: Sequence[str]) -> str:
    """The -l argument: the profile's languages this image can read. A missing pack would make
    tesseract fail the whole page, so it is left out, said in the log; none left reads English."""
    installed = installed_ocr_languages()
    usable = [code for code in languages if not installed or code in installed]
    missing = [code for code in languages if code not in usable]
    if missing:
        log.warning("OCR language packs not installed, read without them: %s", missing)
    return "+".join(usable or ["eng"])


def ocr_page(png: bytes, languages: Sequence[str] = DEFAULT_OCR_LANGUAGES) -> tuple[str, float]:
    """Text and confidence (0..1) of a scanned page, from tesseract's TSV output, in the
    collection's OCR languages (its ingest profile, GEN-11).

    The confidence is the mean of the word confidences weighted by word length, so a page of
    long words read well is not dragged down by stray marks. A page tesseract cannot read at
    all comes back empty with confidence 0: it is then a low_ocr quality issue, never a page
    that silently indexes nothing.
    """
    try:
        done = subprocess.run(
            [
                "tesseract",
                "stdin",
                "stdout",
                "-l",
                ocr_language_arg(languages),
                "--psm",
                "3",
                "tsv",
            ],
            input=png,
            capture_output=True,
            timeout=OCR_TIMEOUT_S,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        log.exception("tesseract failed on a scanned page")
        return "", 0.0
    return parse_tsv(done.stdout.decode("utf-8", errors="replace"))


def parse_tsv(tsv: str) -> tuple[str, float]:
    paragraphs: dict[tuple[str, str], dict[str, list[str]]] = {}
    weighted = chars = 0.0
    for line in tsv.splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) < 12 or cols[0] != "5":
            continue
        word = cols[11].strip()
        try:
            conf = float(cols[10])
        except ValueError:
            continue
        if not word or conf < 0:
            continue
        paragraphs.setdefault((cols[2], cols[3]), {}).setdefault(cols[4], []).append(word)
        weighted += conf * len(word)
        chars += len(word)
    text = "\n\n".join(
        "\n".join(" ".join(words) for words in lines.values()) for lines in paragraphs.values()
    )
    return text, round(weighted / chars / 100, 3) if chars else 0.0


def normalize_text(text: str) -> str:
    """NFC, as document_page.text promises: PDF text layers often carry decomposed Vietnamese
    (a + combining hook), which neither matches a typed query in full-text search nor hashes
    like the same page extracted again. Soft hyphens and zero-width marks go, NBSP is a space."""
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[\u00ad\u200b\u200c\u200d\ufeff]", "", text)
    return text.replace("\u00a0", " ").strip()


def context_header(title: str | None, doc_no: str | None, section_path: str | None) -> str:
    """Prefixed to a chunk before it is embedded (UC-05-02 step 6), so a passage that says only
    "mức lãi suất tại Khoản 2" still lands near questions about the document it belongs to."""
    parts = [title, f"Số {doc_no}" if doc_no else None, section_path]
    return " | ".join(part for part in parts if part)


def document_chunks(pages: list[str], limit: int = MAX_CHUNK_CHARS) -> list[ChunkDraft]:
    """Chunks over the whole document, cut on its structure rather than on page breaks.

    An Điều becomes one chunk even when it runs across pages; one longer than ``limit`` is cut
    between Khoản, and only a single Khoản longer than ``limit`` is cut inside (at a space).
    Text outside any Điều (preamble, plain reports) is packed by paragraph within a page, so its
    page citation stays exact.
    """
    drafts: list[ChunkDraft] = []
    for unit in _units(_paragraphs(pages)):
        groups = _clause_groups(unit.paragraphs) if unit.article else [[p] for p in unit.paragraphs]
        drafts.extend(_pack(groups, unit, limit))
    return drafts


@dataclass
class _Paragraph:
    text: str
    page_from: int
    page_to: int


@dataclass
class _Unit:
    article: str | None
    path: list[str]
    paragraphs: list[_Paragraph]


def _paragraphs(pages: list[str]) -> list[_Paragraph]:
    result: list[_Paragraph] = []
    for page_no, text in enumerate(pages, start=1):
        for paragraph in re.split(r"\n\s*\n", _join_wrapped_lines(text)):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            # A sentence broken by the page break continues in lower case on the next page.
            if (
                result
                and result[-1].page_to == page_no - 1
                and paragraph[:1].islower()
                and result[-1].text[-1:] not in ".:;?!"
            ):
                result[-1].text = f"{result[-1].text} {paragraph}"
                result[-1].page_to = page_no
                continue
            result.append(_Paragraph(paragraph, page_no, page_no))
    return result


def _heading_label(text: str, marker: re.Match[str]) -> str:
    # "Điều 5. Lãi suất huy động" when the heading is its own paragraph; when the PDF joined it to
    # the body, only "Điều 5".
    return text if len(text) <= 120 else marker.group(0)


def _units(paragraphs: list[_Paragraph]) -> list[_Unit]:
    units: list[_Unit] = []
    part: str | None = None
    section: str | None = None
    current: _Unit | None = None
    for paragraph in paragraphs:
        text = paragraph.text
        if marker := _PART.match(text):
            part, section, current = _heading_label(text, marker), None, None
            continue
        if marker := _SECTION.match(text):
            section, current = _heading_label(text, marker), None
            continue
        article = _ARTICLE.match(text)
        path = [x for x in (part, section) if x]
        if article:
            label = _heading_label(text, article)
            current = _Unit(label, [*path, label], [paragraph])
            units.append(current)
            continue
        if current is None:
            current = _Unit(None, path, [])
            units.append(current)
        current.paragraphs.append(paragraph)
    return [unit for unit in units if unit.paragraphs]


def _clause_groups(paragraphs: list[_Paragraph]) -> list[list[_Paragraph]]:
    # The Điều heading and any lead-in stay with the first Khoản; each later "N." opens a group.
    groups: list[list[_Paragraph]] = [[]]
    for paragraph in paragraphs:
        if (
            _CLAUSE.match(paragraph.text)
            and groups[-1]
            and any(_CLAUSE.match(p.text) for p in groups[-1])
        ):
            groups.append([])
        groups[-1].append(paragraph)
    return [group for group in groups if group]


def _clause_label(group: list[_Paragraph]) -> str | None:
    numbers = [m.group(1) for p in group if (m := _CLAUSE.match(p.text))]
    if not numbers:
        return None
    return f"Khoản {numbers[0]}" if len(numbers) == 1 else f"Khoản {numbers[0]}-{numbers[-1]}"


def _pack(groups: list[list[_Paragraph]], unit: _Unit, limit: int) -> list[ChunkDraft]:
    def draft(members: list[_Paragraph], text: str | None = None) -> ChunkDraft:
        path = list(unit.path)
        whole = sum(len(g) for g in groups) == len(members)
        if unit.article and not whole and (label := _clause_label(members)):
            path.append(label)
        return ChunkDraft(
            text or "\n\n".join(p.text for p in members),
            members[0].page_from,
            max(p.page_to for p in members),
            " > ".join(path) or None,
        )

    drafts: list[ChunkDraft] = []
    current: list[_Paragraph] = []

    def flush() -> None:
        if current:
            drafts.append(draft(list(current)))
            current.clear()

    for group in groups:
        size = len("\n\n".join(p.text for p in group))
        if size > limit:
            flush()
            # One Khoản (or one paragraph) over the limit: the last resort is a cut at a space.
            for piece in _chunks("\n\n".join(p.text for p in group), limit):
                drafts.append(draft(group, piece))
            continue
        joined = len("\n\n".join(p.text for p in [*current, *group]))
        # Outside an Điều a chunk does not cross a page, so its citation names one page.
        crosses_page = not unit.article and current and group[0].page_from != current[-1].page_to
        if current and (joined > limit or crosses_page):
            flush()
        current.extend(group)
    flush()
    return drafts


def _native_layout(layout: LTPage, frame: _PageFrame) -> list[LayoutBlock]:
    """The text blocks of a page in reading order (top to bottom, then left to right), each a
    heading or a paragraph judged on its font size against the page's body size."""
    boxes = [element for element in layout if isinstance(element, LTTextBox)]
    sizes = [size for box in boxes for size in _char_sizes(box)]
    body_size = median(sizes) if sizes else 10.0
    blocks: list[LayoutBlock] = []
    for box in boxes:
        text = "\n".join(
            line.get_text().strip() for line in box if isinstance(line, LTTextLine)
        ).strip()
        if not text:
            continue
        own = _char_sizes(box)
        size = median(own) if own else body_size
        heading = _looks_like_heading(text, size, body_size)
        blocks.append(
            LayoutBlock(
                "heading" if heading else "paragraph",
                text,
                frame.layout_box(box.bbox),
                _heading_level(text, size, body_size) if heading else None,
            )
        )
    blocks.sort(key=lambda block: (round(block.box[1], 1), block.box[0]))
    return blocks


def _char_sizes(box: LTTextBox) -> list[float]:
    return [
        float(char.size)
        for line in box
        if isinstance(line, LTTextLine)
        for char in line
        if isinstance(char, LTChar) and char.get_text().strip()
    ]


def _clean_block_text(text: str) -> str:
    return " ".join(line.strip() for line in text.splitlines() if line.strip())


def _layout_text(blocks: list[LayoutBlock]) -> str:
    return "\n\n".join(_clean_block_text(block.text) for block in blocks if block.text.strip())


def _join_wrapped_lines(text: str) -> str:
    paragraphs = re.split(r"\n\s*\n", text)
    cleaned = [
        " ".join(line.strip() for line in paragraph.splitlines() if line.strip())
        for paragraph in paragraphs
        if paragraph.strip()
    ]
    merged: list[str] = []
    for paragraph in cleaned:
        if merged and paragraph[:1].islower() and merged[-1][-1:] not in ".:;?!":
            merged[-1] = f"{merged[-1]} {paragraph}"
        else:
            merged.append(paragraph)
    return "\n\n".join(merged)


def _looks_like_heading(text: str, size: float, body_size: float) -> bool:
    marker = r"^(part|chapter|section|article|annex|appendix|phần|chương|mục|điều|khoản|phụ lục)\b"
    numbered = r"^(?:\d+(?:\.\d+)*|[IVX]+|[A-Z])(?:[.)]|\s)"
    return len(text) <= 240 and (
        size >= body_size * 1.16
        or re.match(marker, text, re.IGNORECASE) is not None
        or re.match(numbered, text) is not None
    )


def _heading_level(text: str, size: float, body_size: float) -> int:
    if re.match(r"^(part|phần|chapter|chương)\b", text, re.IGNORECASE) or size >= body_size * 1.5:
        return 1
    if re.match(r"^(article|section|điều|mục)\b", text, re.IGNORECASE) or size >= body_size * 1.3:
        return 2
    return 3


def layout_json(page: ExtractedPage, image_refs: list[str], width: float, height: float) -> bytes:
    images = iter(image_refs)
    blocks = []
    for block in page.blocks:
        item = {
            "kind": block.kind,
            "text": block.text,
            "box": list(block.box),
            "level": block.level,
        }
        if block.kind == "picture":
            item["image_ref"] = next(images, None)
        blocks.append(item)
    return json.dumps(
        {"width": width, "height": height, "blocks": blocks}, ensure_ascii=False
    ).encode("utf-8")
