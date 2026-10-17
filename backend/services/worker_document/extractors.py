"""Page extraction for the media other than PDF (UC-05-02 step 2).

Each extractor returns ExtractedPage objects whose blocks carry the document's own structure
(title, headings by level, paragraphs, list items, table rows), and whose text is those blocks
joined by blank lines, so the structured chunker sees Điều and Khoản as it does for a PDF.

- html: the main content only (<main>, <article>, or an element named article/content), without
  scripts, styles, navigation, headers, footers and forms; entities decoded; the charset taken
  from the BOM or the <meta> tag, else UTF-8, else Windows-1252. Remote images are never fetched.
- docx: word/document.xml read directly (no Word runtime): heading styles, list items, tables and
  embedded pictures; a page break Word recorded (rendered or explicit) starts a new page.
- email (.eml): the headers, then the plain-text body, else the HTML body read as html; the names
  of the attachments are listed.
- image: one page per frame (a TIFF may hold several), read by OCR with its confidence.

Text-based media have no page image and no geometry: their blocks have empty boxes and the viewer
reflows them instead of drawing a page.
"""

from __future__ import annotations

import email
import email.policy
import io
import posixpath
import re
import zipfile
from email.message import EmailMessage
from html.parser import HTMLParser
from xml.etree.ElementTree import Element

import pymupdf
from defusedxml import ElementTree

from services.worker_document.processing import (
    ExtractedImage,
    ExtractedPage,
    LayoutBlock,
    normalize_text,
    ocr_page,
)

NO_BOX = (0.0, 0.0, 0.0, 0.0)
# A .docx is a zip: what it may inflate to is bounded, whatever its compressed size (zip bombs).
MAX_DOCX_INFLATED = 300 * 1024 * 1024
# The picture formats a page may show. A .docx names its pictures itself: anything else is not
# kept, so a crafted name never reaches a storage key or a content type.
PICTURE_TYPES = {
    "png": "png",
    "jpg": "jpeg",
    "jpeg": "jpeg",
    "gif": "gif",
    "bmp": "bmp",
    "webp": "webp",
    "tif": "tiff",
    "tiff": "tiff",
}
# What an upload marked as an image must start with, and the reader pymupdf is held to: without
# it pymupdf guesses, and an "image" that is really a PDF, XPS or EPUB is parsed as one.
IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"II*\x00", "tiff"),
    (b"MM\x00*", "tiff"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"BM", "bmp"),
)
# A4 in points: the frame the viewer gives a page that has no geometry of its own.
A4 = (595.0, 842.0)


def _text_page(
    blocks: list[LayoutBlock], images: list[ExtractedImage] | None = None
) -> ExtractedPage:
    blocks = [b for b in blocks if b.text.strip() or b.kind == "picture"]
    text = normalize_text("\n\n".join(b.text for b in blocks if b.text.strip()))
    return ExtractedPage(text, None, True, None, blocks, images or [], *A4)


def _clean(text: str) -> str:
    return normalize_text(
        re.sub(r"[ \t\r\f\v]*\n[ \t\r\f\v]*", "\n", re.sub(r"[ \t\r\f\v]+", " ", text))
    )


# --- html ---------------------------------------------------------------------------------------

_SKIP = {
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "nav",
    "header",
    "footer",
    "aside",
    "form",
    "button",
    "select",
    "option",
    "iframe",
    "object",
    "embed",
    "canvas",
    "video",
    "audio",
}
_VOID = {
    "br",
    "hr",
    "img",
    "meta",
    "link",
    "input",
    "source",
    "wbr",
    "area",
    "base",
    "col",
    "track",
}
_BLOCK = {
    "p",
    "div",
    "section",
    "article",
    "main",
    "blockquote",
    "pre",
    "dd",
    "dt",
    "figcaption",
    "address",
    "caption",
    "summary",
    "details",
    "ul",
    "ol",
    "dl",
    "body",
    "center",
}
_HEADING = {"h1": 1, "h2": 2, "h3": 3, "h4": 3, "h5": 3, "h6": 3}
_MAIN_IDS = {"article", "content", "main", "main-content", "maincontent"}


def decode_html(content: bytes) -> str:
    for bom, codec in (
        (b"\xef\xbb\xbf", "utf-8"),
        (b"\xff\xfe", "utf-16-le"),
        (b"\xfe\xff", "utf-16-be"),
    ):
        if content.startswith(bom):
            return content[len(bom) :].decode(codec, errors="replace")
    head = content[:4096].decode("ascii", errors="ignore")
    declared = re.search(
        r"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_\-]+)""", head, re.IGNORECASE
    )
    if declared:
        try:
            return content.decode(declared.group(1), errors="replace")
        except LookupError:
            pass
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("cp1252", errors="replace")


class _HtmlBlocks(HTMLParser):
    def __init__(self, main_only: bool) -> None:
        super().__init__(convert_charrefs=True)
        self.main_only = main_only
        self.blocks: list[LayoutBlock] = []
        self.title = ""
        # Open elements, each with whether it opened the main content area.
        self._stack: list[tuple[str, bool]] = []
        self._skip = 0
        self._main = 0
        self._buffer: list[str] = []
        self._kind: tuple[str, int | None] = ("paragraph", None)
        self._row: list[str] | None = None
        self._rows: list[list[str]] | None = None
        self._in_title = False

    def _capturing(self) -> bool:
        return self._skip == 0 and (not self.main_only or self._main > 0)

    def _flush(self) -> None:
        text = _clean("".join(self._buffer))
        self._buffer = []
        if not text:
            return
        if self._row is not None:
            self._row.append(text)
            return
        kind, level = self._kind
        self.blocks.append(LayoutBlock(kind, text, NO_BOX, level))
        self._kind = ("paragraph", None)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        if tag in _VOID:
            if tag == "br" and self._capturing():
                self._buffer.append("\n")
            return
        named = {k: (v or "").lower() for k, v in attrs}
        main = (
            tag in ("main", "article")
            or named.get("role") == "main"
            or named.get("id") in _MAIN_IDS
        )
        self._stack.append((tag, main))
        if main:
            self._main += 1
        if tag in _SKIP:
            self._skip += 1
            return
        if not self._capturing():
            return
        if tag in _HEADING:
            self._flush()
            self._kind = ("heading", _HEADING[tag])
        elif tag == "li":
            self._flush()
            self._buffer.append("• ")
        elif tag == "table":
            self._flush()
            self._rows = []
        elif tag == "tr":
            self._flush()
            self._row = []
        elif tag in ("td", "th") or tag in _BLOCK:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in _VOID or all(open_tag != tag for open_tag, _ in self._stack):
            return
        # Close whatever was left open inside this element (HTML in the wild is not balanced).
        while self._stack:
            open_tag, main = self._stack.pop()
            self._close(open_tag, main)
            if open_tag == tag:
                break

    def _close(self, tag: str, main: bool) -> None:
        if tag in _SKIP:
            self._skip -= 1
        capturing = self._capturing()
        if capturing and (tag in _HEADING or tag in _BLOCK or tag in ("li", "td", "th")):
            self._flush()
        if capturing and tag == "tr" and self._row is not None:
            if self._row and self._rows is not None:
                self._rows.append(self._row)
            self._row = None
        if capturing and tag == "table" and self._rows is not None:
            if self._rows:
                self.blocks.append(
                    LayoutBlock("paragraph", "\n".join(" | ".join(r) for r in self._rows), NO_BOX)
                )
            self._rows = None
        if main:
            self._main -= 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self._capturing():
            self._buffer.append(data)

    def close(self) -> None:
        super().close()
        while self._stack:
            self._close(*self._stack.pop())
        self._flush()


def html_blocks(content: bytes | str) -> list[LayoutBlock]:
    markup = decode_html(content) if isinstance(content, bytes) else content
    lowered = markup.lower()
    has_main = (
        "<main" in lowered
        or "<article" in lowered
        or re.search(
            r"""(?:id|role)\s*=\s*["']?(?:article|content|main|main-content|maincontent)\b""",
            lowered,
        )
        is not None
    )
    parser = _HtmlBlocks(main_only=has_main)
    parser.feed(markup)
    parser.close()
    blocks = parser.blocks
    if has_main and not blocks:
        # The page named a main area but left it empty (filled by script): read the whole body.
        parser = _HtmlBlocks(main_only=False)
        parser.feed(markup)
        parser.close()
        blocks = parser.blocks
    title = _clean(parser.title)
    if title and not any(b.kind == "heading" and b.level == 1 for b in blocks):
        blocks = [LayoutBlock("title", title, NO_BOX, 1), *blocks]
    return blocks


def html_pages(content: bytes) -> list[ExtractedPage]:
    return [_text_page(html_blocks(content))]


# --- docx ---------------------------------------------------------------------------------------

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _style_level(style: str) -> tuple[str, int | None]:
    s = style.lower().replace(" ", "")
    if s in ("title", "tiêuđề"):
        return "title", 1
    match = re.match(r"(?:heading|tiêuđề|đềmục)(\d)", s)
    if match:
        return "heading", min(int(match.group(1)), 3)
    return "paragraph", None


def docx_pages(content: bytes) -> list[ExtractedPage]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
        if sum(info.file_size for info in archive.infolist()) > MAX_DOCX_INFLATED:
            raise ValueError("The Word document inflates past 300 MB")
        body_xml = archive.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ValueError("The file is not a readable Word document (.docx)") from exc
    rels: dict[str, str] = {}
    try:
        for rel in ElementTree.fromstring(archive.read("word/_rels/document.xml.rels")).iter(
            f"{_REL}Relationship"
        ):
            if rel.get("TargetMode") == "External":
                continue  # a linked picture lives elsewhere and is never fetched
            # Targets are relative to word/; one that climbs out of it, or is absolute, is dropped.
            target = posixpath.normpath(posixpath.join("word", rel.get("Target", "")))
            if target.startswith("word/"):
                rels[rel.get("Id", "")] = target
    except KeyError:
        pass
    body = ElementTree.fromstring(body_xml).find(f"{_W}body")
    if body is None:
        raise ValueError("The Word document has no body")
    pages: list[tuple[list[LayoutBlock], list[ExtractedImage]]] = [([], [])]

    def new_page() -> None:
        if pages[-1][0]:
            pages.append(([], []))

    def paragraph(p: Element) -> None:
        style = p.find(f"{_W}pPr/{_W}pStyle")
        kind, level = _style_level(style.get(f"{_W}val", "") if style is not None else "")
        listed = p.find(f"{_W}pPr/{_W}numPr") is not None
        parts: list[str] = []
        for node in p.iter():
            if node.tag == f"{_W}lastRenderedPageBreak" or (
                node.tag == f"{_W}br" and node.get(f"{_W}type") == "page"
            ):
                if parts:
                    _emit(pages, kind, level, listed, "".join(parts))
                    parts = []
                new_page()
            elif node.tag == f"{_W}t":
                parts.append(node.text or "")
            elif node.tag == f"{_W}tab":
                parts.append("\t")
            elif node.tag == f"{_W}br":
                parts.append("\n")
            elif node.tag == f"{_A}blip":
                target = rels.get(node.get(f"{_R}embed", ""), "")
                extension = PICTURE_TYPES.get(posixpath.splitext(target)[1].lstrip(".").lower())
                try:
                    data = archive.read(target) if target and extension else b""
                except KeyError:
                    data = b""
                if data and extension:
                    pages[-1][1].append(ExtractedImage(data, extension, NO_BOX))
                    pages[-1][0].append(LayoutBlock("picture", "", NO_BOX))
        if parts:
            _emit(pages, kind, level, listed, "".join(parts))

    def table(t: Element) -> None:
        rows = []
        for tr in t.iter(f"{_W}tr"):
            cells = []
            for tc in tr.findall(f"{_W}tc"):
                cells.append(
                    _clean(
                        " ".join(
                            "".join(x.text or "" for x in p.iter(f"{_W}t"))
                            for p in tc.iter(f"{_W}p")
                        )
                    )
                )
            if any(cells):
                rows.append(" | ".join(cells))
        if rows:
            pages[-1][0].append(LayoutBlock("paragraph", "\n".join(rows), NO_BOX))

    for child in body:
        if child.tag == f"{_W}p":
            paragraph(child)
        elif child.tag == f"{_W}tbl":
            table(child)
    result = [_text_page(blocks, images) for blocks, images in pages if blocks]
    if not result:
        raise ValueError("The Word document has no text")
    return result


def _emit(pages: list, kind: str, level: int | None, listed: bool, text: str) -> None:
    text = _clean(text)
    if text:
        pages[-1][0].append(
            LayoutBlock(
                kind, f"• {text}" if listed and kind == "paragraph" else text, NO_BOX, level
            )
        )


# --- email --------------------------------------------------------------------------------------


def email_pages(content: bytes, file_name: str = "") -> list[ExtractedPage]:
    if file_name.lower().endswith(".msg") or content[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise ValueError("Outlook .msg files are not read yet; save the message as .eml")
    message = email.message_from_bytes(content, policy=email.policy.default)
    assert isinstance(message, EmailMessage)
    blocks: list[LayoutBlock] = []
    subject = _clean(str(message.get("Subject", "")))
    if subject:
        blocks.append(LayoutBlock("title", subject, NO_BOX, 1))
    header_lines = [
        f"{label}: {_clean(str(message[name]))}"
        for name, label in (("From", "Từ"), ("To", "Đến"), ("Cc", "Cc"), ("Date", "Ngày"))
        if message.get(name)
    ]
    if header_lines:
        blocks.append(LayoutBlock("paragraph", "\n".join(header_lines), NO_BOX))
    body = message.get_body(preferencelist=("plain", "html"))
    if body is not None:
        payload = body.get_content()
        if body.get_content_type() == "text/html":
            blocks.extend(b for b in html_blocks(payload) if b.kind != "title")
        else:
            blocks.extend(
                LayoutBlock("paragraph", text, NO_BOX)
                for text in (_clean(p) for p in re.split(r"\n\s*\n", payload))
                if text
            )
    attachments = [name for a in message.iter_attachments() if (name := a.get_filename())]
    if attachments:
        blocks.append(LayoutBlock("paragraph", "Tệp đính kèm: " + ", ".join(attachments), NO_BOX))
    if not any(b.text for b in blocks):
        raise ValueError("The email has no readable content")
    return [_text_page(blocks)]


# --- image --------------------------------------------------------------------------------------


def image_pages(content: bytes) -> list[ExtractedPage]:
    kind = next((name for magic, name in IMAGE_MAGIC if content.startswith(magic)), None)
    if kind is None and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        kind = "webp"
    if kind is None:
        raise ValueError("The file is not a PNG, JPEG, TIFF, GIF, BMP or WebP image")
    try:
        picture = pymupdf.open(stream=content, filetype=kind)
    except (RuntimeError, ValueError) as exc:
        raise ValueError("The image could not be opened") from exc
    result: list[ExtractedPage] = []
    with picture:
        for frame in picture:
            png = frame.get_pixmap(dpi=144, alpha=False).tobytes("png")
            text, conf = ocr_page(frame.get_pixmap(dpi=300, alpha=False).tobytes("png"))
            blocks = (
                [LayoutBlock("paragraph", text, (0, 0, frame.rect.width, frame.rect.height))]
                if text
                else []
            )
            result.append(
                ExtractedPage(
                    normalize_text(text),
                    png,
                    False,
                    conf,
                    blocks,
                    [],
                    frame.rect.width,
                    frame.rect.height,
                )
            )
    return result
