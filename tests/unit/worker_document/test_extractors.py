"""Extraction of the media other than PDF: the document's structure, not its markup."""

from __future__ import annotations

import io
import zipfile

import pymupdf
import pytest
from defusedxml import EntitiesForbidden

from services.worker_document import extractors
from services.worker_document.processing import document_chunks, extract_pages

pytestmark = pytest.mark.unit

FED = """<!DOCTYPE html><html><head><title>Opening Remarks - Bowman</title>
<style>body{font-family:Georgia}</style><script>var x = "<p>not text</p>";</script></head>
<body><header><nav><a href="/">Home</a> | <a href="/news">News</a></nav></header>
<div id="article">
  <h3>Opening Remarks</h3>
  <p class="speaker">Vice Chair for Supervision Michelle W. Bowman</p>
  <div class="video"><button>Watch Live</button><noscript>Accessible Keys for Video</noscript></div>
  <p>Good morning &mdash; thank you to the Federal Reserve Bank of Atlanta&#8217;s staff.<br>It&rsquo;s a pleasure.</p>
  <ul><li>Supervision</li><li>Regulation &amp; capital</li></ul>
  <table><tr><th>Year</th><th>Rate</th></tr><tr><td>2025</td><td>4,5%</td></tr></table>
</div>
<footer>Last Update: February 19, 2026</footer></body></html>"""


def kinds(blocks: list) -> list[tuple[str, str]]:
    return [(b.kind, b.text) for b in blocks]


def test_html_keeps_the_main_content_and_its_structure() -> None:
    blocks = extractors.html_blocks(FED.encode())
    assert kinds(blocks) == [
        ("title", "Opening Remarks - Bowman"),
        ("heading", "Opening Remarks"),
        ("paragraph", "Vice Chair for Supervision Michelle W. Bowman"),
        (
            "paragraph",
            "Good morning — thank you to the Federal Reserve Bank of Atlanta\u2019s staff.\nIt\u2019s a pleasure.",
        ),
        ("paragraph", "• Supervision"),
        ("paragraph", "• Regulation & capital"),
        ("paragraph", "Year | Rate\n2025 | 4,5%"),
    ]
    text = extract_pages(FED.encode(), "html")[0].text
    for junk in ("Home", "Watch Live", "Accessible Keys", "Last Update", "font-family", "not text"):
        assert junk not in text


def test_html_without_a_main_area_reads_the_body() -> None:
    blocks = extractors.html_blocks(
        b"<html><body><nav>Menu</nav><h1>Tin</h1><p>Noi dung</p></body></html>"
    )
    assert kinds(blocks) == [("heading", "Tin"), ("paragraph", "Noi dung")]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('<meta charset="windows-1252"><p>caf\xe9</p>'.encode("cp1252"), "café"),
        ("<p>Lãi suất</p>".encode(), "Lãi suất"),
        (b"\xef\xbb\xbf<p>L\xc3\xa3i</p>", "Lãi"),
        ("<p>caf\xe9</p>".encode("cp1252"), "café"),
    ],
)
def test_html_charset_is_read_from_the_page(raw: bytes, expected: str) -> None:
    assert extractors.html_blocks(raw)[-1].text == expected


def test_an_html_decree_is_chunked_by_article() -> None:
    page = (
        "<html><body><h2>Điều 1. Phạm vi</h2><p>Quy định về lãi suất.</p>"
        "<h2>Điều 2. Lãi suất</h2><p>1. Kỳ hạn 12 tháng 5,8%/năm.</p><p>2. Kỳ hạn 6 tháng 5,2%/năm.</p>"
        "</body></html>"
    ).encode()
    chunks = document_chunks([extract_pages(page, "html")[0].text])
    assert [c.section_path for c in chunks] == ["Điều 1. Phạm vi", "Điều 2. Lãi suất"]


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def docx(body: str, *, media: dict[str, bytes] | None = None, rels: str = "") -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr(
            "word/document.xml",
            f'<w:document {W} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
            f'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><w:body>{body}</w:body></w:document>',
        )
        z.writestr(
            "word/_rels/document.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{rels}</Relationships>",
        )
        for name, data in (media or {}).items():
            z.writestr(f"word/{name}", data)
    return out.getvalue()


def p(text: str, style: str | None = None, *, listed: bool = False, extra: str = "") -> str:
    props = ""
    if style or listed:
        props = "<w:pPr>" + (f'<w:pStyle w:val="{style}"/>' if style else "")
        props += ('<w:numPr><w:numId w:val="1"/></w:numPr>' if listed else "") + "</w:pPr>"
    return f"<w:p>{props}<w:r>{extra}<w:t>{text}</w:t></w:r></w:p>"


def test_docx_keeps_headings_lists_tables_and_page_breaks() -> None:
    body = (
        p("Quyết định lãi suất", "Title")
        + p("Điều 1. Phạm vi", "Heading1")
        + p("Áp dụng cho tiền gửi")
        + p("Cá nhân", listed=True)
        + "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Kỳ hạn</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Lãi suất</w:t></w:r></w:p></w:tc></w:tr>"
        + "<w:tr><w:tc><w:p><w:r><w:t>12 tháng</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>5,8%</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
        + p("Điều 2. Hiệu lực", "Heading1", extra='<w:br w:type="page"/>')
    )
    pages = extract_pages(docx(body), "docx")
    assert len(pages) == 2
    assert kinds(pages[0].blocks) == [
        ("title", "Quyết định lãi suất"),
        ("heading", "Điều 1. Phạm vi"),
        ("paragraph", "Áp dụng cho tiền gửi"),
        ("paragraph", "• Cá nhân"),
        ("paragraph", "Kỳ hạn | Lãi suất\n12 tháng | 5,8%"),
    ]
    assert kinds(pages[1].blocks) == [("heading", "Điều 2. Hiệu lực")]
    assert pages[0].image is None and pages[0].text_layer


def test_docx_pictures_are_kept() -> None:
    blip = '<w:drawing><a:blip r:embed="rId5"/></w:drawing>'
    page = extract_pages(
        docx(
            p("Hình 1", extra=blip),
            media={"media/image1.png": b"\x89PNG"},
            rels='<Relationship Id="rId5" Target="media/image1.png"/>',
        ),
        "docx",
    )[0]
    assert [b.kind for b in page.blocks] == ["picture", "paragraph"]
    assert page.images[0].data == b"\x89PNG" and page.images[0].extension == "png"


def test_docx_pictures_outside_word_or_of_odd_types_are_dropped() -> None:
    blips = "".join(
        f'<w:drawing><a:blip r:embed="{i}"/></w:drawing>' for i in ("up", "ext", "odd", "abs")
    )
    rels = (
        '<Relationship Id="up" Target="../docProps/secret.png"/>'
        '<Relationship Id="ext" Target="http://169.254.169.254/x.png" TargetMode="External"/>'
        '<Relationship Id="odd" Target="media/x.png/../../evil"/>'
        '<Relationship Id="abs" Target="/etc/passwd.png"/>'
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr(
            "word/document.xml",
            f'<w:document {W} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><w:body>'
            f"<w:p><w:r>{blips}<w:t>Hình</w:t></w:r></w:p></w:body></w:document>",
        )
        z.writestr(
            "word/_rels/document.xml.rels",
            f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>',
        )
        z.writestr("docProps/secret.png", b"\x89PNG")
        z.writestr("evil", b"\x89PNG")
    page = extract_pages(out.getvalue(), "docx")[0]
    assert page.images == [] and [b.kind for b in page.blocks] == ["paragraph"]


def test_an_image_upload_that_is_really_a_pdf_is_refused() -> None:
    doc = pymupdf.open()
    doc.new_page()
    with pytest.raises(ValueError, match="not a PNG"):
        extract_pages(doc.tobytes(), "image")


def test_a_docx_with_entities_is_refused_not_expanded() -> None:
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE w [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
        f"<w:document {W}><w:body><w:p><w:r><w:t>&b;</w:t></w:r></w:p></w:body></w:document>"
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("word/document.xml", bomb)
    with pytest.raises(EntitiesForbidden):
        extract_pages(out.getvalue(), "docx")


def test_not_a_docx_is_said_so() -> None:
    with pytest.raises(ValueError, match="not a readable Word document"):
        extract_pages(b"plain text", "docx")


EML = b"""From: Phong Phap che <phapche@nh.vn>
To: Tin dung <tindung@nh.vn>
Subject: =?utf-8?q?L=C3=A3i_su=E1=BA=A5t_th=C3=A1ng_10?=
Date: Fri, 16 Oct 2026 09:00:00 +0700
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="X"

--X
Content-Type: multipart/alternative; boundary="Y"

--Y
Content-Type: text/plain; charset=utf-8
Content-Transfer-Encoding: 8bit

K\xe1\xbb\xb3 h\xe1\xba\xa1n 12 th\xc3\xa1ng: 5,8%/n\xc4\x83m.

Tr\xc3\xa2n tr\xe1\xbb\x8dng.
--Y
Content-Type: text/html; charset=utf-8

<p>HTML version</p>
--Y--
--X
Content-Type: application/pdf; name="qd.pdf"
Content-Disposition: attachment; filename="qd.pdf"

JVBERi0=
--X--
"""


def test_email_reads_headers_body_and_names_attachments() -> None:
    page = extract_pages(EML, "email", "thu.eml")[0]
    assert kinds(page.blocks)[0] == ("title", "Lãi suất tháng 10")
    assert "Từ: Phong Phap che <phapche@nh.vn>" in page.blocks[1].text
    assert "Kỳ hạn 12 tháng: 5,8%/năm." in page.text and "HTML version" not in page.text
    assert page.blocks[-1].text == "Tệp đính kèm: qd.pdf"


def test_outlook_msg_is_refused_with_a_way_out() -> None:
    with pytest.raises(ValueError, match=r"\.eml"):
        extract_pages(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64, "email", "thu.msg")


def test_an_image_is_one_ocr_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extractors, "ocr_page", lambda png: ("Kỳ hạn 12 tháng", 0.91))
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 20), 0)
    pix.clear_with(255)
    page = extract_pages(pix.tobytes("png"), "image")[0]
    assert (page.text, page.ocr_conf, page.text_layer) == ("Kỳ hạn 12 tháng", 0.91, False)
    assert page.image is not None and page.image.startswith(b"\x89PNG")


def test_recordings_are_not_transcribed_yet() -> None:
    with pytest.raises(ValueError, match="not transcribed"):
        extract_pages(b"ID3", "audio")
