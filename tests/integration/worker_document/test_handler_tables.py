"""DI-09: doc_ingest with Docling (DOC_EXTRACTOR=docling) on the V3 database: a document's
elements, tables and cells, its chunks cut on headings with no figure in them, the extended
document.indexed payload, the fallback to the legacy pipeline when Docling fails, and a second
run that rewrites the structure without duplicating it. Docling runs for real, offline, on files
made here (the worker-document image carries it and its models, DI-03)."""

from __future__ import annotations

import io
import uuid
from typing import Any, ClassVar

import pytest

pytest.importorskip("docling")

from services.worker_document import docling_path, handler  # noqa: E402

from .conftest import Seed  # noqa: E402
from .test_handler import Uploaded, event, page  # noqa: E402

pytestmark = [pytest.mark.integration]


class Files:
    content: ClassVar[bytes] = b""
    uploads: ClassVar[list[str]] = []

    def set_url(self, url: str) -> None: ...

    async def open(self) -> None: ...

    async def close(self) -> None: ...

    async def download(self, bucket: str, key: str) -> bytes:
        return Files.content

    async def upload(self, bucket: str, key: str, data: bytes, content_type: str) -> None:
        Files.uploads.append(key)


@pytest.fixture(autouse=True)
def docling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOC_EXTRACTOR", "docling")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(handler, "MinIOClient", Files)
    Files.uploads = []


def upload(seed: Seed, media: str, file_name: str) -> Uploaded:
    org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="Ngân hàng thử")
    user = seed.row("org.app_user", org_id=org, external_id="kc", username="u", display_name="U")
    space = seed.row("org.data_space", org_id=org, code="DS", name="DS", steward_id=user)
    collection = seed.row(
        "document.collection",
        org_id=org,
        data_space_id=space,
        code="c",
        name="Văn bản",
        purpose="tra cứu",
        qdrant_collection="doc_x",
        embed_model="bge-m3",
        pii_policy="mask",
    )
    document = seed.row(
        "document.document",
        org_id=org,
        collection_id=collection,
        sha256=uuid.uuid4().hex,
        media=media,
        file_name=file_name,
        object_key="k",
        size_bytes=4,
        title="Quy chế tín dụng",
    )
    return Uploaded(org, document)


def rules_docx() -> bytes:
    docx = pytest.importorskip("docx")
    d = docx.Document()
    d.add_heading("Chương I. Quy định chung", level=1)
    d.add_paragraph("Điều 1. Phạm vi điều chỉnh")
    d.add_paragraph("Quy chế này áp dụng cho mọi khoản cho vay của ngân hàng.")
    d.add_paragraph("Điều 2. Dư nợ")
    d.add_paragraph("Bảng 1. Dư nợ theo kỳ (triệu đồng)", style="Caption")
    t = d.add_table(rows=3, cols=3)
    for r, row in enumerate([["Chỉ tiêu", "Kế hoạch", "Thực hiện"], ["Dư nợ", "1.234", "1.300"],
                             ["Nợ xấu", "12", "15"]]):  # fmt: skip
        for c, text in enumerate(row):
            t.cell(r, c).text = text
    buffer = io.BytesIO()
    d.save(buffer)
    return buffer.getvalue()


def scanned_table_pdf() -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1700, 1100), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=40)
    rows = [["Chi tieu", "Q1", "Q2"], ["Doanh thu", "1.234", "1.456"], ["Chi phi", "800", "910"]]
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            box = [150 + c * 400, 220 + r * 110, 150 + (c + 1) * 400, 220 + (r + 1) * 110]
            draw.rectangle(box, outline="black", width=3)
            draw.text((box[0] + 20, box[1] + 30), text, fill="black", font=font)
    buffer = io.BytesIO()
    img.save(buffer, format="PDF", resolution=150)
    return buffer.getvalue()


async def test_a_docx_is_read_into_elements_tables_cells_and_chunks(seed: Seed) -> None:
    u = upload(seed, "docx", "quy-che.docx")
    Files.content = rules_docx()
    await handler.process_document(event(u))
    assert seed.one("SELECT status FROM document.document WHERE id = %s", u.document) == "ready"
    kinds = seed.all(
        "SELECT kind, level, extractor FROM document.element WHERE document_id = %s ORDER BY order_idx",
        u.document,
    )
    assert ("heading", 4, "docling") in kinds and ("table", None, "docling") in kinds
    assert ("caption", None, "docling") in kinds
    # The caption points at its table; the table at its element.
    assert (
        seed.one(
            "SELECT count(*) FROM document.element c JOIN document.element t ON t.id = c.parent_element_id"
            " WHERE c.document_id = %s AND c.kind = 'caption' AND t.kind = 'table'",
            u.document,
        )
        == 1
    )
    table_id, caption, rows, cols = seed.all(
        "SELECT id, caption, n_rows, n_cols FROM document.doc_table WHERE document_id = %s",
        u.document,
    )[0]
    assert (caption, rows, cols) == ("Bảng 1. Dư nợ theo kỳ (triệu đồng)", 3, 3)
    # "1.234" in vi-VN, in millions of đồng, as the caption says.
    assert (
        seed.one(
            "SELECT numeric_value FROM document.table_cell WHERE table_id = %s AND row_idx = 1 AND col_idx = 1",
            table_id,
        )
        == 1_234_000_000
    )
    chunks = seed.all(
        "SELECT kind, section_path, text, table_id, cardinality(element_ids) FROM document.chunk"
        " WHERE document_id = %s ORDER BY kind, page_from, section_path",
        u.document,
    )
    sections = [c for c in chunks if c[0] == "section"]
    (summary,) = (c for c in chunks if c[0] == "table")
    assert ("Chương I. Quy định chung > Điều 1. Phạm vi điều chỉnh") in [c[1] for c in sections]
    assert summary[3] == table_id and summary[4] == 1
    # No chunk holds a figure of the table (§5.3 rule 1, §5.4).
    assert not any(f in c[2] for c in chunks for f in ("1.234", "1.300"))
    payload = seed.one(
        "SELECT payload FROM document.outbox WHERE aggregate_id = %s AND event_type = 'document.indexed'",
        u.document,
    )
    assert payload["extractor_version"] == "docling-2.150.0+adapter-1"
    assert payload["table_ids"] == [str(table_id)] and len(payload["chunk_ids"]) == len(chunks)
    assert payload["element_count"] == len(kinds) and payload["contract_version"] == "doc-export-1"
    assert payload["page_count"] == 1 and "quality" in payload


async def test_a_scanned_table_keeps_its_page_its_boxes_and_its_ocr(seed: Seed) -> None:
    u = upload(seed, "pdf", "bang.pdf")
    Files.content = scanned_table_pdf()
    await handler.process_document(event(u))
    assert seed.one("SELECT status FROM document.document WHERE id = %s", u.document) == "ready"
    ocr, text_layer = seed.all(
        "SELECT ocr_conf, text_layer FROM document.document_page WHERE document_id = %s", u.document
    )[0]
    assert ocr is not None and not text_layer
    cell = seed.all(
        "SELECT c.numeric_value, c.page_no, c.bbox IS NOT NULL FROM document.table_cell c"
        " JOIN document.doc_table t ON t.id = c.table_id WHERE t.document_id = %s AND c.text = '1.456'",
        u.document,
    )
    assert cell == [(1456, 1, True)]
    # The page image and its layout went to the derived bucket as on the legacy path.
    assert any(k.endswith("page-1.png") for k in Files.uploads)
    assert any(k.endswith("page-1.layout.json") for k in Files.uploads)


async def test_when_docling_fails_the_legacy_pipeline_reads_the_document_and_says_why(
    seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("docling: failure")

    monkeypatch.setattr(docling_path, "extract", broken)
    monkeypatch.setattr(handler, "extract_pages", lambda *a, **k: [page("Điều 1. Phạm vi")])
    u = upload(seed, "pdf", "x.pdf")
    Files.content = b"%PDF"
    await handler.process_document(event(u))
    assert seed.one("SELECT status FROM document.document WHERE id = %s", u.document) == "ready"
    assert seed.all(
        "SELECT kind, severity, detail->>'docling_failed' FROM document.quality_issue WHERE document_id = %s",
        u.document,
    ) == [("garbled_text", "warning", "RuntimeError: docling: failure")]
    assert seed.one("SELECT count(*) FROM document.element WHERE document_id = %s", u.document) == 0
    assert (
        seed.one(
            "SELECT payload->>'extractor_version' FROM document.outbox"
            " WHERE aggregate_id = %s AND event_type = 'document.indexed'",
            u.document,
        )
        == "legacy-pdfminer-1"
    )


async def test_a_second_run_rewrites_the_structure_and_keeps_unchanged_chunks(seed: Seed) -> None:
    u = upload(seed, "docx", "quy-che.docx")
    Files.content = rules_docx()
    await handler.process_document(event(u))
    first = dict(
        seed.all(
            "SELECT text, id FROM document.chunk WHERE document_id = %s AND kind = 'section'",
            u.document,
        )
    )
    elements = seed.one("SELECT count(*) FROM document.element WHERE document_id = %s", u.document)
    await handler.reprocess_document(event(u, handler.DOCUMENT_REPROCESS))
    assert (
        seed.one("SELECT count(*) FROM document.element WHERE document_id = %s", u.document)
        == elements
    )
    assert (
        seed.one("SELECT count(*) FROM document.doc_table WHERE document_id = %s", u.document) == 1
    )
    again = dict(
        seed.all(
            "SELECT text, id FROM document.chunk WHERE document_id = %s AND kind = 'section'",
            u.document,
        )
    )
    assert again == first  # the same text keeps its chunk (and its vector)
    # Every chunk's elements are this run's.
    assert (
        seed.one(
            "SELECT count(*) FROM document.chunk c WHERE c.document_id = %s AND EXISTS ("
            " SELECT 1 FROM unnest(c.element_ids) e WHERE NOT EXISTS ("
            "  SELECT 1 FROM document.element x WHERE x.id = e))",
            u.document,
        )
        == 0
    )
    assert (
        seed.one(
            "SELECT count(*) FROM document.chunk WHERE document_id = %s AND kind = 'table'",
            u.document,
        )
        == 1
    )


def test_the_extractor_stays_legacy_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOC_EXTRACTOR", raising=False)
    from services.worker_document import docling_adapter

    assert docling_adapter.extractor() == "legacy"
