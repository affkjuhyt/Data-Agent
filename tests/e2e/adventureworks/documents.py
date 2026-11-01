"""The document corpus of the Data Space DS-AW, as the SQL seed db-init applies.

deploy/docker/adventureworks-docs/ holds the files, one folder per collection: the eight product manuals Microsoft ships in the
AdventureWorks OLTP sample (Production.Document, github.com/microsoft/sql-server-samples, MIT;
saved from .doc to .docx, the text untouched), and business documents of the fictional company
written for the product's tests, in Vietnamese and English. minio-init puts them in da-raw-docs.

The pages and chunks are not written by hand: they are what worker-document would store for each
file, read with its own extractor, PII mask and chunker, so the seed is a processed corpus and
not a paraphrase of one. Only the metadata a person enters at upload (number, title, dates, the
replaced document) is declared here. Regenerate after editing a file or the pipeline:

    make adventureworks-documents     # writes deploy/docker/sql/v3-adventureworks-documents.sql

tests/unit/worker_document/test_adventureworks_documents.py fails when the seed is stale.
Runs in the worker-document image (pymupdf, defusedxml).
"""

from __future__ import annotations

import datetime
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

from services.worker_document.handler import mask_for
from services.worker_document.processing import (
    context_header,
    document_chunks,
    extract_pages,
    injection_suspected,
    source_hash,
)

ROOT = Path(__file__).resolve().parents[3]
FILES = ROOT / "deploy" / "docker" / "adventureworks-docs"
SEED = ROOT / "deploy" / "docker" / "sql" / "v3-adventureworks-documents.sql"
PII_POLICY = "mask"
EMBED_MODEL = "multilingual-e5-small"
MEDIA = {".html": "html", ".docx": "docx", ".eml": "email", ".pdf": "pdf"}


@dataclass(frozen=True)
class Collection:
    key: int
    code: str
    name: str
    purpose: str
    folder: (
        str  # under deploy/docker/adventureworks-docs/, and under adventureworks/ in da-raw-docs
    )
    qdrant: str


@dataclass(frozen=True)
class Doc:
    key: int
    collection: int
    file: str
    doc_no: str
    title: str
    issued_on: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    supersedes: str | None = None  # doc_no of the replaced document


COLLECTIONS = (
    Collection(
        1,
        "aw-chinh-sach-kinh-doanh",
        "Chính sách và báo cáo kinh doanh",
        "Định nghĩa chỉ tiêu, khuyến mại, ngân sách, tổng đài và báo cáo kinh doanh",
        "business",
        "aw-business-documents",
    ),
    Collection(
        2,
        "aw-tai-lieu-san-pham",
        "Tài liệu sản phẩm và dịch vụ",
        "Product manuals and the dealer return and warranty policy",
        "product",
        "aw-product-documents",
    ),
)

# The manuals have no number of their own; AWC-MAN-nn follows their order in Production.Document.
DOCUMENTS = (
    Doc(
        1,
        1,
        "01-2011-qd-awc.html",
        "01/2011/QĐ-AWC",
        "Quy định cách tính các chỉ tiêu kinh doanh",
        "2011-01-05",
        "2011-01-01",
        "2012-06-30",
    ),
    Doc(
        2,
        1,
        "07-2012-qd-awc.html",
        "07/2012/QĐ-AWC",
        "Quy định cách tính các chỉ tiêu kinh doanh (sửa đổi)",
        "2012-06-20",
        "2012-07-01",
        None,
        "01/2011/QĐ-AWC",
    ),
    Doc(
        3,
        1,
        "14-2010-qd-awc.html",
        "14/2010/QĐ-AWC",
        "Chính sách chiết khấu theo số lượng cho đại lý",
        "2010-12-20",
        "2010-12-29",
        "2013-12-28",
    ),
    Doc(
        4,
        1,
        "11-2011-tb-awc.html",
        "11/2011/TB-AWC",
        "Thong bao khuyen mai cuoi nam 2011 cho dai ly",
        "2011-11-05",
        "2011-11-12",
        "2012-02-28",
    ),
    Doc(
        5,
        1,
        "12-2012-tb-awc.html",
        "12/2012/TB-AWC",
        "Thông báo khuyến mại mùa đông 2012-2013",
        "2012-12-05",
        "2012-12-12",
        "2013-03-29",
    ),
    Doc(
        6,
        1,
        "03-2013-tb-awc.eml",
        "03/2013/TB-AWC",
        "Chương trình Half-Price Pedal Sale",
        "2013-02-04",
        "2013-02-11",
        "2013-03-14",
    ),
    Doc(
        7,
        1,
        "15-2013-tb-awc.html",
        "15/2013/TB-AWC",
        "Thông báo thanh lý xe Mountain-500 màu bạc",
        "2013-10-22",
        "2013-10-29",
        "2013-12-28",
    ),
    Doc(
        8,
        1,
        "15-2010-qd-awc.docx",
        "15/2010/QĐ-AWC",
        "Quy chế kế hoạch ngân sách và so sánh với thực hiện",
        "2010-12-15",
        "2011-01-01",
    ),
    Doc(
        9,
        1,
        "05-2014-qt-awc.html",
        "05/2014/QT-AWC",
        "Quy trình vận hành tổng đài chăm sóc khách hàng",
        "2014-03-25",
        "2014-04-01",
    ),
    Doc(
        10,
        1,
        "09-2013-bc-awc.docx",
        "09/2013/BC-AWC",
        "Báo cáo kinh doanh quý 4 năm tài chính 2012",
        "2013-07-15",
        "2013-07-15",
    ),
    Doc(
        11,
        1,
        "08-2013-bc-awc.html",
        "08/2013/BC-AWC",
        "Báo cáo nhanh kết quả kinh doanh quý 4 năm tài chính 2012 (sơ bộ)",
        "2013-07-03",
        "2013-07-03",
    ),
    Doc(
        12,
        1,
        "21-2013-cv-awc.eml",
        "21/2013/CV-AWC",
        "Góp ý của đại lý Progressive Sports về chiết khấu",
        "2013-11-04",
        "2013-11-04",
    ),
    Doc(
        13,
        2,
        "06-2011-pol-awc.html",
        "06/2011/POL-AWC",
        "Dealer Return Policy",
        "2011-06-15",
        "2011-07-01",
        "2013-06-30",
    ),
    # Linked by hand: "supersedes" is English, which validity._REPLACES does not read.
    Doc(
        14,
        2,
        "10-2013-pol-awc.pdf",
        "10/2013/POL-AWC",
        "Dealer Return and Warranty Policy",
        "2013-06-20",
        "2013-07-01",
        None,
        "06/2011/POL-AWC",
    ),
    Doc(15, 2, "introduction-1.docx", "AWC-MAN-01", "Introduction 1"),
    Doc(16, 2, "repair-and-service-guidelines.docx", "AWC-MAN-02", "Repair and Service Guidelines"),
    Doc(
        17, 2, "crank-arm-and-tire-maintenance.docx", "AWC-MAN-03", "Crank Arm and Tire Maintenance"
    ),
    Doc(18, 2, "lubrication-maintenance.docx", "AWC-MAN-04", "Lubrication Maintenance"),
    Doc(
        19,
        2,
        "front-reflector-bracket-and-reflector-assembly-3.docx",
        "AWC-MAN-05",
        "Front Reflector Bracket and Reflector Assembly 3",
    ),
    Doc(
        20,
        2,
        "front-reflector-bracket-installation.docx",
        "AWC-MAN-06",
        "Front Reflector Bracket Installation",
    ),
    Doc(21, 2, "installing-replacement-pedals.docx", "AWC-MAN-07", "Installing Replacement Pedals"),
    Doc(22, 2, "seat-assembly.docx", "AWC-MAN-08", "Seat Assembly"),
)


def uid(kind: int, n: int, k: int = 0) -> str:
    """kind 1 collection, 2 source, 3 document, 4 page, 5 chunk."""
    return f"a0000000-0000-0000-0000-0000d{kind}{n:03d}{k:03d}"


def lit(value: str | None) -> str:
    return "NULL" if value is None else "'" + value.replace("'", "''") + "'"


def day(value: str | None) -> str:
    return "NULL" if value is None else f"date '{datetime.date.fromisoformat(value)}'"


def _bool(value: bool) -> str:
    return "true" if value else "false"


@dataclass(frozen=True)
class Processed:
    doc: Doc
    content: bytes
    pages: list[tuple[str, str, bool, bool]]  # (page_hash, text, text_layer, usable)
    chunks: list[tuple[int, int, str | None, str, bool, bool]]


def _folder(doc: Doc) -> str:
    return next(c.folder for c in COLLECTIONS if c.key == doc.collection)


def path(doc: Doc) -> Path:
    return FILES / _folder(doc) / doc.file


def object_key(doc: Doc) -> str:
    """Where minio-init puts the file in da-raw-docs."""
    return f"adventureworks/{_folder(doc)}/{doc.file}"


def process(doc: Doc) -> Processed:
    """What worker-document's handler stores for the file: page text masked under the policy,
    chunks cut by the structured chunker over the masked pages, each chunk masked again."""
    content = path(doc).read_bytes()
    extracted = extract_pages(content, MEDIA[Path(doc.file).suffix], doc.file)
    texts = [mask_for(PII_POLICY, p.text)[0] for p in extracted]
    pages = [
        (source_hash(p), t, p.text_layer, bool(t) and (p.ocr_conf is None or p.ocr_conf >= 0.85))
        for p, t in zip(extracted, texts, strict=True)
    ]
    chunks = []
    for draft in document_chunks(texts):
        text, masked = mask_for(PII_POLICY, draft.text)
        chunks.append(
            (
                draft.page_from,
                draft.page_to,
                draft.section_path,
                text,
                masked,
                injection_suspected(text),
            )
        )
    return Processed(doc, content, pages, chunks)


HEADER = """\
-- Documents of the Data Space "AdventureWorks" (DS-AW, 04-v3-adventureworks-seed.sql).
-- GENERATED by tests/e2e/adventureworks/documents.py (make adventureworks-documents): do not edit.
--
-- The files are deploy/docker/adventureworks-docs/, which minio-init puts in da-raw-docs; sha256
-- and size_bytes are theirs. Pages and chunks are what worker-document stores for each file (its
-- extractor, PII mask and chunker), so reprocessing a document keeps them. The chunks have no
-- vector: until a reindex, retrieval over them is the keyword half.
--
-- Two collections: Vietnamese business documents of the fictional Adventure Works Cycles, written
-- for the tests and consistent with AdventureWorksDW, and English product documents (the eight
-- manuals of the OLTP sample's Production.Document, MIT, saved as .docx). Traps on purpose:
--   - 07/2012/QĐ-AWC replaces 01/2011/QĐ-AWC from 01/07/2012 ("thay thế Quyết định số ...", the
--     form worker_document.validity detects): revenue moves from ship date to order date and the
--     fiscal year from the year it ends to the year it starts.
--   - 10/2013/POL-AWC supersedes 06/2011/POL-AWC, said in English: the detector misses it, the
--     link here is a person's.
--   - Promotions with the dates of dim_promotion, all ended by 2014; the volume discount ended
--     28/12/2013.
--   - The preliminary Q4 FY2012 report (08/2013/BC-AWC) adds tax and freight and averages the
--     channel margins; the final one (09/2013/BC-AWC) matches the data. Both stay in force, so
--     claims put them in conflict.
--   - 21/2013/CV-AWC carries a prompt injection: its chunk is flagged and never retrieved.
--   - 11/2011/TB-AWC is written without diacritics, 05/2014/QT-AWC with typos.
-- Idempotent: the documents are deleted (pages and chunks cascade) and written again.
"""


def sql() -> str:
    processed = [process(d) for d in DOCUMENTS]
    ids = {d.doc_no: uid(3, d.key) for d in DOCUMENTS}
    out = [HEADER]
    out.append("\\set org '''a0000000-0000-0000-0000-000000000001'''")
    out.append("\\set engineer '''a0000000-0000-0000-0000-000000000022'''")
    out.append("\\set space '''a0000000-0000-0000-0000-000000000a01'''\n")
    for c in COLLECTIONS:
        kinds = sorted({MEDIA[Path(d.file).suffix] for d in DOCUMENTS if d.collection == c.key})
        out.append(
            "INSERT INTO document.collection (\n"
            "    id, org_id, data_space_id, code, name, media_kinds, purpose, pii_policy,\n"
            "    qdrant_collection, embed_model, status, created_by, updated_by\n)\n"
            f"VALUES (\n    '{uid(1, c.key)}', :org::uuid, :space::uuid, {lit(c.code)}, {lit(c.name)},\n"
            f"    ARRAY[{', '.join(lit(k) for k in kinds)}], {lit(c.purpose)}, {lit(PII_POLICY)},\n"
            f"    {lit(c.qdrant)}, {lit(EMBED_MODEL)}, 'active', :engineer::uuid, :engineer::uuid\n)\n"
            "ON CONFLICT (id) DO UPDATE SET\n"
            "    name = EXCLUDED.name, media_kinds = EXCLUDED.media_kinds, purpose = EXCLUDED.purpose;\n"
        )
        out.append(
            "INSERT INTO document.document_source (\n"
            "    id, org_id, collection_id, kind, location, sync_cron, status, created_by, updated_by\n)\n"
            f"VALUES (\n    '{uid(2, c.key)}', :org::uuid, '{uid(1, c.key)}', 'object_store',\n"
            f"    's3://da-raw-docs/adventureworks/{c.folder}/', '0 0 1 1 *', 'active', :engineer::uuid, :engineer::uuid\n)\n"
            "ON CONFLICT (id) DO NOTHING;\n"
        )
    out.append(
        "DELETE FROM document.document WHERE id IN (\n"
        + ",\n".join(f"    '{ids[d.doc_no]}'" for d in DOCUMENTS)
        + "\n);\n"
    )
    # Replaced documents first: supersedes_id points at a row that already exists.
    ordered = sorted(processed, key=lambda p: p.doc.supersedes is not None)
    for p in ordered:
        d = p.doc
        out.append(
            "INSERT INTO document.document (\n"
            "    id, org_id, collection_id, document_source_id, sha256, media, file_name, object_key,\n"
            "    size_bytes, title, doc_no, issued_on, effective_from, effective_to, supersedes_id,\n"
            "    page_count, status, created_by, updated_by\n)\n"
            f"VALUES (\n    '{ids[d.doc_no]}', :org::uuid, '{uid(1, d.collection)}', '{uid(2, d.collection)}',\n"
            f"    '{hashlib.sha256(p.content).hexdigest()}',\n"
            f"    {lit(MEDIA[Path(d.file).suffix])}, {lit(d.file)}, {lit(object_key(d))}, {len(p.content)},\n"
            f"    {lit(d.title)}, {lit(d.doc_no)}, {day(d.issued_on)},\n"
            f"    {day(d.effective_from)}, {day(d.effective_to)}, "
            f"{lit(ids[d.supersedes]) if d.supersedes else 'NULL'}, {len(p.pages)}, 'ready',\n"
            "    :engineer::uuid, :engineer::uuid\n);\n"
        )
    for p in processed:
        d = p.doc
        rows = [
            f"    ('{uid(4, d.key, n)}', :org::uuid, '{ids[d.doc_no]}', {n}, '{h}', {_bool(layer)},\n"
            f"     {lit(text or None)},\n     {_bool(usable)}, :engineer::uuid, :engineer::uuid)"
            for n, (h, text, layer, usable) in enumerate(p.pages, start=1)
        ]
        out.append(
            "INSERT INTO document.document_page (\n"
            "    id, org_id, document_id, page_no, page_hash, text_layer, text, usable, created_by,\n"
            "    updated_by\n)\nVALUES\n" + ",\n".join(rows) + ";\n"
        )
        rows = [
            f"    ('{uid(5, d.key, n)}', :org::uuid, '{ids[d.doc_no]}', '{uid(1, d.collection)}', {a}, {b},\n"
            f"     {lit(path)}, {lit(context_header(d.title, d.doc_no, path) or None)},\n"
            f"     {lit(text)},\n"
            f"     {len(text.split())}, {day(d.effective_from)}, {_bool(masked)}, {_bool(injected)})"
            for n, (a, b, path, text, masked, injected) in enumerate(p.chunks, start=1)
        ]
        out.append(
            "INSERT INTO document.chunk (\n"
            "    id, org_id, document_id, collection_id, page_from, page_to, section_path,\n"
            "    context_header, text, tokens, effective_from, pii_masked, injection_suspected\n)\n"
            "VALUES\n" + ",\n".join(rows) + ";\n"
        )
    out.append(CHECKS)
    return "\n".join(out)


CHECKS = """\
-- The seed fails closed when a trap is missing, so a later edit cannot silently flatten the corpus.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM document.document AS new
        JOIN document.document AS old ON old.id = new.supersedes_id
        WHERE new.doc_no = '07/2012/QĐ-AWC' AND old.doc_no = '01/2011/QĐ-AWC'
          AND old.effective_to < new.effective_from
    ) THEN
        RAISE EXCEPTION '07/2012/QĐ-AWC does not replace 01/2011/QĐ-AWC';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM document.chunk AS c JOIN document.document AS d ON d.id = c.document_id
        WHERE d.doc_no = '07/2012/QĐ-AWC' AND c.text LIKE '%thay thế Quyết định số 01/2011/QĐ-AWC%'
    ) THEN
        RAISE EXCEPTION 'the replacement sentence is gone';
    END IF;
    IF EXISTS (
        SELECT 1 FROM document.document
        WHERE doc_no LIKE '%/TB-AWC' AND (effective_to IS NULL OR effective_to >= date '2014-01-01')
    ) THEN
        RAISE EXCEPTION 'a promotion is still in force in 2014';
    END IF;
    IF (
        SELECT count(*) FROM document.document
        WHERE doc_no IN ('08/2013/BC-AWC', '09/2013/BC-AWC') AND effective_to IS NULL
          AND supersedes_id IS NULL
    ) <> 2 THEN
        RAISE EXCEPTION 'the preliminary and final reports must both be in force';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM document.chunk AS c JOIN document.document AS d ON d.id = c.document_id
        WHERE d.doc_no = '21/2013/CV-AWC' AND c.injection_suspected
    ) THEN
        RAISE EXCEPTION 'the injected letter is not flagged';
    END IF;
END
$$;
"""


def main(argv: list[str]) -> None:
    if argv[1:] != ["sql"]:
        raise SystemExit(
            "usage: documents.py sql > deploy/docker/sql/v3-adventureworks-documents.sql"
        )
    sys.stdout.write(sql())


if __name__ == "__main__":
    main(sys.argv)
