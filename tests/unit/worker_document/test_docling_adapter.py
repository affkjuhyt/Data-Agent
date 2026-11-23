"""DI-04: docling_adapter.py turns a DoclingDocument into elements: kind, reading order, heading
level, page, bbox, a caption's parent; furniture and a table of contents are left out.

The fixtures are Docling 2.150.0's own output (fixtures/docling/): rules_docx.json from a DOCX
(title, Chương/Điều headings, a list, a "Bảng 1." caption, a table with a merged header),
scanned_table_pdf.json from a scanned page with a 4x4 table read by Tesseract and TableFormer."""

from __future__ import annotations

import json
import pathlib

import pytest

pytest.importorskip("docling_core")

from docling_core.types.doc import (  # noqa: E402
    BoundingBox,
    DocItemLabel,
    DoclingDocument,
    ProvenanceItem,
    TableData,
)

from services.worker_document import docling_adapter as da  # noqa: E402

pytestmark = [pytest.mark.unit]

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "docling"


def _doc(name: str) -> DoclingDocument:
    return DoclingDocument.model_validate(json.loads((FIXTURES / name).read_text("utf-8")))


def test_a_docx_becomes_its_elements_in_reading_order() -> None:
    got = da.elements_of(_doc("rules_docx.json")).elements
    assert [(e.kind, e.level, e.text) for e in got] == [
        ("heading", 1, "Quy chế tín dụng"),
        ("heading", 1, "Chương I. Quy định chung"),
        ("heading", 2, "Điều 1. Phạm vi điều chỉnh"),
        ("text", None, "Quy chế này áp dụng cho mọi khoản cho vay của ngân hàng."),
        ("list", None, "Khách hàng cá nhân"),
        ("list", None, "Khách hàng doanh nghiệp"),
        ("caption", None, "Bảng 1. Dư nợ theo kỳ (triệu đồng)"),
        ("table", None, ""),
        ("text", None, "Số liệu chưa kiểm toán."),
    ]
    assert [e.order_idx for e in got] == list(range(len(got)))
    # A DOCX has no pages: no page, no box.
    assert all(e.page_no is None and e.bbox is None for e in got)
    # The caption Docling read as text belongs to the table after it.
    table = next(e for e in got if e.kind == "table")
    assert got[6].parent_ref == table.ref and table.ref.startswith("#/tables/")
    assert {e.extractor_version for e in got} == {da.EXTRACTOR_VERSION}


def test_a_scanned_page_keeps_its_page_and_boxes() -> None:
    got = da.elements_of(_doc("scanned_table_pdf.json"), ocr_pages={1: 0.91}).elements
    assert [e.kind for e in got] == ["text", "picture", "table"]
    assert got[0].text == "Bao cao doanh thu 2013"
    for e in got:
        assert e.page_no == 1 and e.confidence == 0.91
        assert e.bbox is not None and set(e.bbox) == {"l", "t", "r", "b", "coord_origin"}
        assert e.bbox["coord_origin"] == "BOTTOMLEFT" and e.bbox["l"] < e.bbox["r"]
    # A page not OCRed carries no confidence.
    assert da.elements_of(_doc("scanned_table_pdf.json")).elements[0].confidence is None


def test_furniture_and_a_table_of_contents_are_left_out_and_counted() -> None:
    doc = DoclingDocument(name="x")
    prov = ProvenanceItem(page_no=2, bbox=BoundingBox(l=0, t=10, r=5, b=0), charspan=(0, 1))
    doc.add_text(label=DocItemLabel.PAGE_HEADER, text="Ngân hàng X — Quy chế", prov=prov)
    doc.add_text(label=DocItemLabel.TEXT, text="Nội dung", prov=prov)
    doc.add_table(data=TableData(num_rows=0, num_cols=0), label=DocItemLabel.DOCUMENT_INDEX)
    doc.add_text(label=DocItemLabel.PAGE_FOOTER, text="Trang 2", prov=prov)
    doc.add_text(label=DocItemLabel.FORMULA, text="x^2", prov=prov)
    got = da.elements_of(doc)
    assert [(e.kind, e.text, e.order_idx) for e in got.elements] == [
        ("text", "Nội dung", 0),
        ("formula", "x^2", 1),
    ]
    assert got.skipped == {"page_header": 1, "page_footer": 1, "document_index": 1}


def test_a_caption_docling_linked_names_its_table() -> None:
    doc = DoclingDocument(name="x")
    caption = doc.add_text(label=DocItemLabel.CAPTION, text="Kết quả kinh doanh")
    table = doc.add_table(data=TableData(num_rows=1, num_cols=1), caption=caption)
    got = {e.ref: e for e in da.elements_of(doc).elements}
    assert got[caption.self_ref].kind == "caption"
    assert got[caption.self_ref].parent_ref == table.self_ref


def test_a_text_that_only_looks_like_a_caption_stays_text() -> None:
    doc = DoclingDocument(name="x")
    doc.add_text(label=DocItemLabel.TEXT, text="Bảng 1. Dư nợ")
    doc.add_text(label=DocItemLabel.TEXT, text="không có bảng ở cạnh")
    assert [e.kind for e in da.elements_of(doc).elements] == ["text", "text"]


def test_headings_are_held_to_six_levels() -> None:
    doc = DoclingDocument(name="x")
    doc.add_heading("Điểm a", level=9)
    assert da.elements_of(doc).elements[0].level == 6


@pytest.mark.parametrize(
    ("value", "expected"), [(None, "legacy"), ("docling", "docling"), (" Docling ", "docling"),
                            ("legacy", "legacy"), ("other", "legacy")],
)  # fmt: skip
def test_the_legacy_pipeline_stays_the_default(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected: str
) -> None:
    # §11: Docling is not the default before the bake-off (DI-05) has its numbers.
    if value is None:
        monkeypatch.delenv("DOC_EXTRACTOR", raising=False)
    else:
        monkeypatch.setenv("DOC_EXTRACTOR", value)
    assert da.extractor() == expected


def test_a_docx_converts_end_to_end(tmp_path: pathlib.Path) -> None:
    # DOCX goes through Docling's own backend, no model: the whole path runs here.
    docx = pytest.importorskip("docx")
    path = tmp_path / "x.docx"
    d = docx.Document()
    d.add_heading("Điều 5. Lãi suất", level=1)
    d.add_paragraph("Lãi suất cho vay do Hội đồng quyết định.")
    d.save(str(path))
    got = da.elements_of(da.convert(path, da.Options())).elements
    assert [(e.kind, e.text) for e in got] == [
        ("heading", "Điều 5. Lãi suất"),
        ("text", "Lãi suất cho vay do Hội đồng quyết định."),
    ]


def test_the_pdf_pipeline_is_built_from_the_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCLING_ARTIFACTS_PATH", "/models/docling")
    converter = da.converter_for(
        da.Options(ocr_engine="rapidocr", force_full_page_ocr=True, threads=2, max_seconds=60)
    )
    from docling.datamodel.base_models import InputFormat

    pipeline = converter.format_to_options[InputFormat.PDF].pipeline_options
    assert pipeline.artifacts_path == "/models/docling" and pipeline.document_timeout == 60
    assert pipeline.do_table_structure and pipeline.accelerator_options.num_threads == 2
    assert type(pipeline.ocr_options).__name__ == "RapidOcrOptions"
    assert pipeline.ocr_options.mode.value == "full_page"
    tesseract = da.converter_for(da.Options()).format_to_options[InputFormat.PDF].pipeline_options
    assert (
        tesseract.ocr_options.lang == ["vie", "eng"]
        and tesseract.ocr_options.mode.value == "default"
    )
