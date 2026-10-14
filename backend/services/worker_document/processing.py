from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from statistics import median

import pymupdf

MAX_CHUNK_CHARS = 4_000


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
                    try:
                        text = page.get_textpage_ocr(language="vie+eng", dpi=200, full=True).extractText().strip()
                        ocr_conf = 0.90 if text else 0.0
                    except Exception:  # OCR binaries vary by image/PDF; keep the page processable.  # noqa: BLE001
                        text = ""
                        ocr_conf = 0.0
                    if text:
                        blocks.append(LayoutBlock("paragraph", text, (0, 0, page.rect.width, page.rect.height)))
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
                            blocks.append(LayoutBlock("picture", "", (rect.x0, rect.y0, rect.x1, rect.y1)))
                    text = _layout_text(blocks)
                if not text_layer:
                    text = _join_wrapped_lines(text)
                image = page.get_pixmap(dpi=144, alpha=False).tobytes("png")
                result.append(ExtractedPage(text, image, text_layer, ocr_conf, blocks, images, page.rect.width, page.rect.height))
        return result
    if media == "html":
        text = re.sub(r"<[^>]+>", " ", content.decode("utf-8", errors="replace")).strip()
        return [ExtractedPage(text, None, True, None, [LayoutBlock("paragraph", text, (0, 0, 0, 0))], [], 595, 842)]
    raise ValueError(f"No text extractor is configured for media={media}")


def page_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def page_chunks(text: str) -> list[str]:
    return [_join_wrapped_lines(chunk) for chunk in _chunks(text)]


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
        spans = [span for line in lines for span in line.get("spans", []) if span.get("text", "").strip()]
        size = median([float(span["size"]) for span in spans]) if spans else body_size
        heading = _looks_like_heading(text, size, body_size)
        bbox = tuple(float(value) for value in block["bbox"])
        blocks.append(LayoutBlock("heading" if heading else "paragraph", text, bbox, _heading_level(text, size, body_size) if heading else None))
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
        if (
            merged
            and paragraph[:1].islower()
            and merged[-1][-1:] not in ".:;?!"
        ):
            merged[-1] = f"{merged[-1]} {paragraph}"
        else:
            merged.append(paragraph)
    return "\n\n".join(merged)


def _looks_like_heading(text: str, size: float, body_size: float) -> bool:
    marker = r"^(part|chapter|section|article|annex|appendix|phần|chương|mục|điều|khoản|phụ lục)\b"
    numbered = r"^(?:\d+(?:\.\d+)*|[IVX]+|[A-Z])(?:[.)]|\s)"
    return len(text) <= 240 and (size >= body_size * 1.16 or re.match(marker, text, re.IGNORECASE) is not None or re.match(numbered, text) is not None)


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
        item = {"kind": block.kind, "text": block.text, "box": list(block.box), "level": block.level}
        if block.kind == "picture":
            item["image_ref"] = next(images, None)
        blocks.append(item)
    return json.dumps({"width": width, "height": height, "blocks": blocks}, ensure_ascii=False).encode("utf-8")
