from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from statistics import median

import pymupdf

MAX_CHUNK_CHARS = 4_000
# BR-19: a page read by OCR below this confidence is not used until a person reviews it.
OCR_THRESHOLD = 0.85
OCR_DPI = 300
OCR_TIMEOUT_S = 120

log = logging.getLogger(__name__)

# Structure of Vietnamese legal text (UC-05-02 step 5): an Điều is the unit a citation names, so a
# chunk never starts inside one; a Khoản ("1.", "2.") is the only place a long Điều may be cut.
_PART = re.compile(r"^(?:phần|chương)\s+(?:[ivxlc]+|\d+)\b", re.IGNORECASE)
_SECTION = re.compile(r"^mục\s+\d+\b", re.IGNORECASE)
_ARTICLE = re.compile(r"^điều\s+(\d+[a-zđ]?)\b", re.IGNORECASE)
_CLAUSE = re.compile(r"^(\d+)\.\s")

# UC-05-02 E2: sentences addressed to the model rather than the reader. The passage stays indexed
# (it may be the very rule a user asks about); the flag tells the answer step to treat it as data.
_INJECTION = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:bỏ qua|phớt lờ|quên)\s+(?:mọi|tất cả|toàn bộ|các)?\s*(?:hướng dẫn|chỉ dẫn|lệnh|quy tắc)",
        r"\bignore\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above)\s+(?:instructions|prompts?|rules)",
        r"\bdisregard\s+(?:all\s+|the\s+)?(?:previous|prior|above)\b",
        r"\b(?:system prompt|lời nhắc hệ thống)\b",
        r"\b(?:bạn là|you are now)\s+(?:một\s+)?(?:trợ lý|mô hình|assistant|model)\b",
        r"\b(?:trả|xuất|gửi|in ra)\s+(?:về\s+)?toàn bộ\s+(?:dữ liệu|thông tin)",
    )
]


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


def extract_pages(content: bytes, media: str) -> list[ExtractedPage]:
    """Extract deterministic page text for the first production document path.

    PDF text extraction is intentionally local and deterministic; OCR for scanned pages is a
    separate quality path and must not pretend an image was successfully indexed as text.
    """
    if media == "pdf":
        result: list[ExtractedPage] = []
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            for page in pdf:
                text = page.get_text("text", sort=True).strip()
                text_layer = len(text) >= 20
                ocr_conf = None
                blocks: list[LayoutBlock] = []
                images: list[ExtractedImage] = []
                if not text_layer:
                    text, ocr_conf = ocr_page(
                        page.get_pixmap(dpi=OCR_DPI, alpha=False).tobytes("png")
                    )
                    if text:
                        blocks.append(
                            LayoutBlock(
                                "paragraph", text, (0, 0, page.rect.width, page.rect.height)
                            )
                        )
                else:
                    blocks = _native_layout(page)
                    for image_info in page.get_images(full=True):
                        xref = image_info[0]
                        try:
                            extracted = pdf.extract_image(xref)
                            rects = page.get_image_rects(xref)
                        except (KeyError, RuntimeError, ValueError):
                            continue
                        for rect in rects:
                            images.append(
                                ExtractedImage(
                                    extracted["image"],
                                    extracted.get("ext", "bin"),
                                    (rect.x0, rect.y0, rect.x1, rect.y1),
                                )
                            )
                            blocks.append(
                                LayoutBlock("picture", "", (rect.x0, rect.y0, rect.x1, rect.y1))
                            )
                    text = _layout_text(blocks)
                if not text_layer:
                    text = _join_wrapped_lines(text)
                image = page.get_pixmap(dpi=144, alpha=False).tobytes("png")
                result.append(
                    ExtractedPage(
                        normalize_text(text),
                        image,
                        text_layer,
                        ocr_conf,
                        blocks,
                        images,
                        page.rect.width,
                        page.rect.height,
                    )
                )
        return result
    if media == "html":
        text = normalize_text(re.sub(r"<[^>]+>", " ", content.decode("utf-8", errors="replace")))
        return [
            ExtractedPage(
                text, None, True, None, [LayoutBlock("paragraph", text, (0, 0, 0, 0))], [], 595, 842
            )
        ]
    raise ValueError(f"No text extractor is configured for media={media}")


def page_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def source_hash(page: ExtractedPage) -> str:
    """What "the page did not change" is judged on when a document is processed again (BR-108):
    the text of a page with a text layer, the rendered image of a scanned one, since OCR of the
    same image may read a character differently from one run to the next."""
    if page.text_layer or page.image is None:
        return page_hash(page.text)
    return hashlib.sha256(page.image).hexdigest()


def ocr_page(png: bytes) -> tuple[str, float]:
    """Text and confidence (0..1) of a scanned page, from tesseract's TSV output.

    The confidence is the mean of the word confidences weighted by word length, so a page of
    long words read well is not dragged down by stray marks. A page tesseract cannot read at
    all comes back empty with confidence 0: it is then a low_ocr quality issue, never a page
    that silently indexes nothing.
    """
    try:
        done = subprocess.run(
            ["tesseract", "stdin", "stdout", "-l", "vie+eng", "--psm", "3", "tsv"],
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


def injection_suspected(text: str) -> bool:
    return any(pattern.search(text) for pattern in _INJECTION)


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


def _native_layout(page: pymupdf.Page) -> list[LayoutBlock]:
    raw = page.get_text("dict", sort=True)
    sizes = [
        float(span["size"])
        for block in raw.get("blocks", [])
        if block.get("type") == 0
        for line in block.get("lines", [])
        for span in line.get("spans", [])
        if span.get("text", "").strip()
    ]
    body_size = median(sizes) if sizes else 10.0
    blocks: list[LayoutBlock] = []
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        lines = block.get("lines", [])
        text = "\n".join(
            "".join(span.get("text", "") for span in line.get("spans", [])).strip()
            for line in lines
        ).strip()
        if not text:
            continue
        spans = [
            span for line in lines for span in line.get("spans", []) if span.get("text", "").strip()
        ]
        size = median([float(span["size"]) for span in spans]) if spans else body_size
        heading = _looks_like_heading(text, size, body_size)
        bbox = tuple(float(value) for value in block["bbox"])
        blocks.append(
            LayoutBlock(
                "heading" if heading else "paragraph",
                text,
                bbox,
                _heading_level(text, size, body_size) if heading else None,
            )
        )
    return blocks


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
