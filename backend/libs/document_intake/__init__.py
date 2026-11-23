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
import struct
from dataclasses import dataclass
from typing import BinaryIO

# The bytes read to decide. A zip's central directory sits at its end: it is read by seeking to
# its end record (_zip_kind), never by loading the whole upload.
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
        return _zip_kind(stream)
    text = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if text.startswith((b"<!doctype html", b"<html")):
        return "html"
    return None


# A zip's central directory is read by hand, bounded: this runs on the api's request path
# (security review), so a directory of millions of entries is not walked, nor built into a set.
MAX_ZIP_ENTRIES = 10_000
MAX_DIRECTORY_BYTES = 4 * 1024 * 1024
_EOCD = b"PK\x05\x06"
_ENTRY = b"PK\x01\x02"


def _zip_kind(stream: BinaryIO) -> str | None:
    """docx, xlsx, unsupported (a PowerPoint) or None, from the zip's central directory: the
    end record found in the last 64 KiB, at most MAX_ZIP_ENTRIES entries and MAX_DIRECTORY_BYTES
    read, stopping at the first name that decides. A ZIP64 archive (over 65 535 entries or 4 GiB,
    far past an upload) or a malformed one is None."""
    size = stream.seek(0, os.SEEK_END)
    tail_size = min(size, 22 + 65_535)
    stream.seek(size - tail_size)
    tail = stream.read(tail_size)
    at = tail.rfind(_EOCD)
    if at < 0 or len(tail) - at < 22:
        return None
    _sig, _disk, _cd_disk, _here, entries, cd_size, cd_offset, _comment = struct.unpack(
        "<4s4H2LH", tail[at : at + 22]
    )
    if entries == 0xFFFF or cd_size == 0xFFFFFFFF or cd_offset == 0xFFFFFFFF:
        return None
    if entries > MAX_ZIP_ENTRIES or cd_size > MAX_DIRECTORY_BYTES or cd_offset + cd_size > size:
        return None
    stream.seek(cd_offset)
    directory = stream.read(cd_size)
    pos = 0
    for _ in range(entries):
        if directory[pos : pos + 4] != _ENTRY or pos + 46 > len(directory):
            return None
        name_len, extra_len, comment_len = struct.unpack("<3H", directory[pos + 28 : pos + 34])
        name = directory[pos + 46 : pos + 46 + name_len].decode("utf-8", "replace")
        if name == "word/document.xml":
            return "docx"
        if name == "xl/workbook.xml":
            return "xlsx"
        if name.startswith("ppt/"):
            return "unsupported"
        pos += 46 + name_len + extra_len + comment_len
    return None
