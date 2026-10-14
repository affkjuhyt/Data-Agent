from __future__ import annotations

import hashlib
import io
import re
from collections.abc import Iterable

from pypdf import PdfReader

MAX_CHUNK_CHARS = 4_000


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


def extract_pages(content: bytes, media: str) -> list[str]:
    """Extract deterministic page text for the first production document path.

    PDF text extraction is intentionally local and deterministic; OCR for scanned pages is a
    separate quality path and must not pretend an image was successfully indexed as text.
    """
    if media == "pdf":
        return [(page.extract_text() or "").strip() for page in PdfReader(io.BytesIO(content)).pages]
    if media == "html":
        return [re.sub(r"<[^>]+>", " ", content.decode("utf-8", errors="replace")).strip()]
    raise ValueError(f"No text extractor is configured for media={media}")


def page_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def page_chunks(text: str) -> list[str]:
    return list(_chunks(text))
