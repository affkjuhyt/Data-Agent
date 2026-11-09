"""A PDF's pages: text blocks and headings from the text layer, boxes in the page's own frame,
embedded pictures, scanned pages read by OCR (pdfminer.six and pypdfium2)."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from services.worker_document import processing
from services.worker_document.processing import extract_pages
from tests.unit.worker_document.pdf_samples import Picture, make_pdf

pytestmark = pytest.mark.unit

BODY = "Each dealer returns a faulty frame within thirty days of the sale."


def _jpeg(width: int = 60, height: int = 40) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(out, format="JPEG")
    return out.getvalue()


def _picture(x: float, y: float, width: float, height: float) -> Picture:
    return {"x": x, "y": y, "width": width, "height": height, "jpeg": _jpeg(), "pixels": (60, 40)}


def _size(png: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(png)).size


def _policy() -> bytes:
    return make_pdf(
        [
            {
                "texts": [
                    {"x": 72, "y": 760, "size": 20, "text": "Dealer Return Policy"},
                    {"x": 72, "y": 720, "size": 12, "text": "Article 1. Scope"},
                    {"x": 72, "y": 690, "size": 10, "text": BODY},
                    {
                        "x": 72,
                        "y": 676,
                        "size": 10,
                        "text": "Frames bought online follow the same rule.",
                    },
                ],
                "picture": _picture(300, 300, 120, 80),
            }
        ]
    )


def test_a_text_page_is_read_as_headings_and_paragraphs_in_reading_order() -> None:
    [page] = extract_pages(_policy(), "pdf")
    assert page.text_layer and page.ocr_conf is None
    texts = [(b.kind, b.level, b.text.splitlines()[0]) for b in page.blocks if b.kind != "picture"]
    assert texts == [
        # 20 pt over a 10 pt body is a first-level heading; "Article" names the second level.
        ("heading", 1, "Dealer Return Policy"),
        ("heading", 2, "Article 1. Scope"),
        ("paragraph", None, BODY),
    ]
    assert page.text.startswith("Dealer Return Policy\n\nArticle 1. Scope\n\n" + BODY)
    assert (page.width, page.height) == (595, 842)


def test_boxes_are_in_the_page_frame_with_the_origin_top_left() -> None:
    [page] = extract_pages(_policy(), "pdf")
    title = page.blocks[0]
    x0, top, _, bottom = title.box
    # Written at y = 760 (from the bottom) in 20 pt: from about 62 below the top of an 842 page
    # to the baseline at 82 and the descenders a little under it.
    assert x0 == pytest.approx(72, abs=1)
    assert 55 < top < 82 < bottom < 90


def test_an_embedded_picture_is_kept_as_stored_with_its_place() -> None:
    [page] = extract_pages(_policy(), "pdf")
    [image] = page.images
    assert image.extension == "jpeg" and image.data.startswith(b"\xff\xd8\xff")
    assert image.box == pytest.approx((300, 842 - 380, 420, 842 - 300), abs=0.5)
    assert [b.box for b in page.blocks if b.kind == "picture"] == [image.box]


def test_the_page_image_is_kept_at_144_dpi() -> None:
    [page] = extract_pages(_policy(), "pdf")
    assert page.image is not None and _size(page.image) == (1190, 1684)


def test_a_page_with_no_text_layer_is_read_by_ocr_at_300_dpi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read: list[tuple[bytes, tuple[str, ...]]] = []

    def fake(png: bytes, languages: tuple[str, ...]) -> tuple[str, float]:
        read.append((png, tuple(languages)))
        return "Hợp đồng đại lý\nsố 12", 0.93

    monkeypatch.setattr(processing, "ocr_page", fake)
    scan = make_pdf([{"picture": _picture(50, 50, 495, 742)}])
    [page] = extract_pages(scan, "pdf", ocr_languages=("vie",))
    assert (page.text_layer, page.ocr_conf) == (False, 0.93)
    assert page.text == "Hợp đồng đại lý số 12"
    assert page.images == []
    assert [(b.kind, b.box) for b in page.blocks] == [("paragraph", (0, 0, 595, 842))]
    [(png, languages)] = read
    # 595 x 842 pt at 300 dpi, rounded up to whole pixels.
    assert _size(png) == (2480, 3509) and languages == ("vie",)


def test_a_cropped_page_measures_from_its_crop_box() -> None:
    cropped = make_pdf(
        [
            {
                "media": (0, 0, 612, 792),
                "crop": (50, 50, 562, 742),
                "texts": [{"x": 100, "y": 700, "size": 10, "text": BODY}],
                "picture": _picture(300, 300, 100, 50),
            }
        ]
    )
    [page] = extract_pages(cropped, "pdf")
    assert (page.width, page.height) == (512, 692)
    assert page.blocks[0].box[0] == pytest.approx(50, abs=1)
    assert page.images[0].box == pytest.approx((250, 742 - 350, 350, 742 - 300), abs=0.5)


def test_a_file_that_is_not_a_pdf_fails() -> None:
    with pytest.raises(Exception):  # noqa: B017 - the handler fails the document on any error
        extract_pages(b"%PDF-1.4 not really", "pdf")
