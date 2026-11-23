"""DI-10: libs/document_intake tells what a file is by its content first (§2.2): magic, a zip's
directory, an HTML start, then the name; what the platform does not read is Unsupported."""

from __future__ import annotations

import io
import zipfile

import pytest

from libs import document_intake as di

pytestmark = [pytest.mark.unit]


def _zip(*names: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, "<x/>")
    return buffer.getvalue()


DOCX = _zip("[Content_Types].xml", "word/document.xml")
XLSX = _zip("[Content_Types].xml", "xl/workbook.xml")
PPTX = _zip("[Content_Types].xml", "ppt/presentation.xml")


@pytest.mark.parametrize(
    ("content", "name", "media"),
    [
        (b"%PDF-1.7 ...", "qd.pdf", "pdf"),
        (b"\x89PNG\r\n\x1a\n...", "scan.png", "image"),
        (b"\xff\xd8\xff\xe0...", "scan.jpg", "image"),
        (b"II*\x00...", "scan.tif", "image"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "scan.webp", "image"),
        (DOCX, "quy-che.docx", "docx"),
        (XLSX, "so-lieu.xlsx", "xlsx"),
        (b"\xef\xbb\xbf<!DOCTYPE html><html>", "trang.html", "html"),
        (b"  <html><body>", "trang.htm", "html"),
        ("Quy chế tín dụng\n".encode(), "ghi-chu.txt", "text"),
        (b"# Tieu de\n", "README.md", "text"),
        (b"From: a@b.vn\nSubject: x\n", "thu.eml", "email"),
    ],
)
def test_a_file_is_what_its_content_says(content: bytes, name: str, media: str) -> None:
    assert di.detect(content, name) == di.Detected(media)


@pytest.mark.parametrize(
    ("content", "name"),
    [
        (PPTX, "slide.pptx"),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1...", "thu.msg"),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1...", "cu.doc"),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1...", "cu.xls"),
        (b"ID3\x04...", "goi.mp3"),
        (b"just bytes", "file.bin"),
        (b"just bytes", "no-extension"),
        # A name that claims a kind its content is not.
        (b"just bytes", "fake.pdf"),
        (_zip("other.txt"), "archive.docx"),
    ],
)
def test_what_the_platform_does_not_read_is_unsupported(content: bytes, name: str) -> None:
    found = di.detect(content, name)
    assert isinstance(found, di.Unsupported) and found.reason


def test_a_name_that_says_another_kind_is_read_as_the_content_and_reported() -> None:
    assert di.detect(b"%PDF-1.4", "bao-cao.docx") == di.Detected("pdf", declared="docx")
    assert di.detect(DOCX, "bao-cao.pdf") == di.Detected("docx", declared="pdf")
    # A name that says nothing known: read as the content, nothing to report.
    assert di.detect(b"%PDF-1.4", "bao-cao.dat") == di.Detected("pdf")


def test_a_file_object_is_read_without_moving_it() -> None:
    stream = io.BytesIO(XLSX)
    stream.seek(7)
    assert di.detect(stream, "x.xlsx") == di.Detected("xlsx")
    assert stream.tell() == 0  # left at its start, ready to be stored
