"""What a document file is, by its content first and its name second (DI-10, DOCUMENT_INTELLIGENCE_SPEC §2.2).

An extension says what a file claims to be; its first bytes say what it is. `detect` reads the
magic of a PDF or an image, the central directory of a zip (word/document.xml is a DOCX,
xl/workbook.xml an XLSX), the start of an HTML page, and only then falls back to the extension
(plain text, Markdown, email). A kind the platform does not read (PowerPoint, Outlook .msg,
recordings, legacy .doc and .xls) is `Unsupported`, answered 415 at upload rather than accepted
and failed later. A file whose extension names another kind than its content is read as its
content says, and the mismatch is reported (a field_mismatch issue).

Standard library only: the api checks an upload with it, the worker reads with it.
"""

from __future__ import annotations

import io
import os
import zipfile
from dataclasses import dataclass
from typing import BinaryIO

# The bytes read to decide. A zip's central directory sits at its end: it is read by seeking in
# the file (zipfile), never by loading the whole upload.
HEAD_BYTES = 8192

_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "pdf"),
    (b"\x89PNG\r\n\x1a\n", "image"),
    (b"\xff\xd8\xff", "image"),
    (b"II*\x00", "image"),
    (b"MM\x00*", "image"),
)
# Old Office binaries (.doc, .xls, .ppt, .msg) are OLE compound files: none is read.
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

BY_EXTENSION: dict[str, str] = {
    ".pdf": "pdf",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".tif": "image",
    ".tiff": "image",
    ".webp": "image",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".html": "html",
    ".htm": "html",
    ".txt": "text",
    ".md": "text",
    ".eml": "email",
}
# Named here so the answer says which kind it is rather than "unknown".
UNSUPPORTED_EXTENSIONS = frozenset(
    {".pptx", ".ppt", ".msg", ".doc", ".xls", ".mp3", ".wav", ".m4a", ".ogg", ".mp4", ".zip"}
)


@dataclass(frozen=True)
class Detected:
    media: str
    # What the name claimed when it differs from the content (a field_mismatch issue).
    declared: str | None = None


@dataclass(frozen=True)
class Unsupported:
    extension: str
    reason: str


def extension(file_name: str) -> str:
    return os.path.splitext(file_name or "")[1].lower()


def detect(content: bytes | BinaryIO, file_name: str) -> Detected | Unsupported:
    """`content` is the file's bytes, or a seekable binary file (left at its start)."""
    ext = extension(file_name)
    by_name = BY_EXTENSION.get(ext)
    stream: BinaryIO = io.BytesIO(content) if isinstance(content, bytes | bytearray) else content
    try:
        kind = _by_content(stream)
    finally:
        stream.seek(0)
    if kind == "unsupported":
        return Unsupported(ext or "?", "an Office binary (doc, xls, ppt, msg) is not read")
    if kind is None:
        if ext in UNSUPPORTED_EXTENSIONS or by_name is None:
            return Unsupported(
                ext or "?", f"files of type {ext or 'without extension'} are not read"
            )
        # No magic: text, Markdown and email are known by their name only.
        if by_name not in ("text", "email", "html"):
            return Unsupported(ext, f"the content is not a {by_name} file")
        return Detected(by_name)
    return Detected(kind, declared=by_name if by_name is not None and by_name != kind else None)


def _by_content(stream: BinaryIO) -> str | None:
    stream.seek(0)
    head = stream.read(HEAD_BYTES)
    for magic, kind in _MAGIC:
        if head.startswith(magic):
            return kind
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image"
    if head.startswith(_OLE):
        return "unsupported"
    if head.startswith(b"PK\x03\x04"):
        names = _zip_names(stream)
        if "word/document.xml" in names:
            return "docx"
        if "xl/workbook.xml" in names:
            return "xlsx"
        if any(n.startswith("ppt/") for n in names):
            return "unsupported"
        return None
    text = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if text.startswith((b"<!doctype html", b"<html")):
        return "html"
    return None


def _zip_names(stream: BinaryIO) -> set[str]:
    """The member names of a zip: its central directory only, nothing is inflated."""
    try:
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            return set(archive.namelist())
    except zipfile.BadZipFile:
        return set()
