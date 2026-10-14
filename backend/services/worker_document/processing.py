from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass

import pymupdf

MAX_CHUNK_CHARS = 4_000


@dataclass(frozen=True)
class ExtractedPage:
    text: str
    image: bytes | None
    text_layer: bool
    ocr_conf: float | None


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
        candidate = f"{current}\n{paragraph}" if current else paragraph
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
                if not text_layer:
                    try:
                        text = page.get_textpage_ocr(language="vie+eng", dpi=200, full=True).extractText().strip()
                        ocr_conf = 0.90 if text else 0.0
                    except Exception:  # OCR binaries vary by image/PDF; keep the page processable.  # noqa: BLE001
                        text = ""
                        ocr_conf = 0.0
                image = page.get_pixmap(dpi=144, alpha=False).tobytes("png")
                result.append(ExtractedPage(text, image, text_layer, ocr_conf))
        return result
    if media == "html":
        return [ExtractedPage(re.sub(r"<[^>]+>", " ", content.decode("utf-8", errors="replace")).strip(), None, True, None)]
    raise ValueError(f"No text extractor is configured for media={media}")


def page_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def page_chunks(text: str) -> list[str]:
    return list(_chunks(text))
