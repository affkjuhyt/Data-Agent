# Audit: Document Intelligence (unstructured data) — 2026-11-12

Paths are under `/Users/thiennlinh/Dev/Projects/Data-Agent/`. `wd` = `backend/services/worker_document`, `wa` = `backend/services/worker_agent/documents`, `api` = `backend/services/api/modules/document/routes.py`.

## 1. Intake & type detection — **partial**

- Formats: by **extension only** (`api:96-114` `MEDIA_BY_EXTENSION`): pdf, png/jpg/jpeg/tif/tiff/webp → image, docx, eml/msg → email, mp3/wav/m4a/ogg → audio, html/htm. Extractors: `wd/processing.py:115-130` dispatch; audio raises "not transcribed yet" (`:129`); `.msg` refused (`test_extractors.py:240`). **No XLSX, PPTX, TXT, MD, legacy .doc/.xls** (grep: none).
- MIME: no sniffing at upload; content magic checked only inside extractors (`extractors.py:67-75` IMAGE_MAGIC, `:343-348` zip/docx, pdfium failure for PDF).
- Size: 200 MB `api:95`, `_hash` `api:490-494`; pages ≤ 2000 `processing.py:31`; render ≤ 40 Mpx `:29`; docx inflate ≤ 300 MB `extractors.py:51`.
- Dedup: sha256 per collection, returns existing doc `api:536-546`; object key content-addressed `api:548`.
- Versioning: `document.supersedes_id/effective_from/effective_to` (`models/document.py:159-161`); chain route `api:1039-1065`; proposal from "thay thế … số" regex `wd/validity.py:24-28`, queued as `document.validity_proposed` `:89`; human accepts. No structural diff between versions.
- Raw storage: `da-raw-docs` created in the plain bucket loop (`deploy/k8s/dev/job/minio-init.yaml:56`) — **no object lock / retention**, unlike `raw` and `da-raw-crawl` (`:87-90`). Derived page PNG + layout JSON + embedded images in `da-doc-derived` (`wd/handler.py:349-369`).

## 2. Parsing — **partial**

- PDF: pdfminer.six `LAParams()` default for text boxes, char font sizes (`processing.py:145`, `:526-551`); pypdfium2 for page image (144 dpi) and embedded pictures (`:177-182`, `:244-265`). Reading order = sort by (y, x) of boxes (`:550`) — **no multi-column logic** beyond pdfminer's defaults. Headings by font size ≥1.16× body median or marker/numbered regex (`:588-603`), levels 1-3.
- OCR trigger: page text layer < 20 chars (`:169`) → tesseract psm 3 TSV at 300 dpi (`:312-341`); languages from collection ingest profile, default `vie+eng` (`:34`, Dockerfile `OCR_LANGS="eng vie"` line 14); confidence = length-weighted mean word conf (`:344-364`); < 0.85 → page `usable=false` + `low_ocr` quality issue (`handler.py:384-394`, `:417-434`). Mixed pages (text layer + image text) are **not** OCR'd.
- **No header/footer/page-number removal, no TOC removal** (grep "footer" in processing: none; RES-15 todo). Wrapped-line joining and cross-page sentence join exist (`:412-430`, `:572-585`).
- Page numbers kept (`document_page.page_no`, `chunk.page_from/page_to`). Bounding boxes kept per block in layout JSON on MinIO (`:606-621`, `layout_ref`), **not** per chunk and not in Postgres.
- DOCX: raw `word/document.xml`, heading styles (en/vi), lists, tables, pictures, page breaks (`extractors.py:331-420`). HTML: main-area extraction, skips nav/header/footer/forms (`:96-116`, `:291-316`). Email: headers + body + attachment names (`:472-477`). Image/TIFF: one OCR page per frame (`:483-539`).

## 3. Tables / figures / formulas — **missing (RG-05 todo)**

- HTML and DOCX tables are **flattened to `"a | b"` rows inside a `paragraph` block** with no header binding, no cell positions (`extractors.py:264-273`, `:403-420`). PDF tables: nothing (pdfminer text boxes only). No merged cells, no multi-page continuation, no `table` element kind.
- Figures: embedded pictures stored as files with a bbox and a `picture` block (`processing.py:177-181`), no captions, no OCR of figures, no chart reading. Formulas: nothing.
- ragflow research verdict confirms: "today `"a | b"` rows with no header" (`docs/research/ragflow-python.md:59-62`).

## 4. Canonical representation — **partial**

`document.*` tables (`backend/libs/dataagent_db/models/document.py`): `collection` (media_kinds, purpose, pii_policy, qdrant_collection, embed_model, ingest_profile, extraction_schema_id), `document_source`, `document` (sha256, media, object_key, size_bytes, title, doc_no, issued_on, effective_from/to, supersedes_id, parent_document_id, page_count, status, error), `document_page` (page_no, page_hash, text_layer, ocr_conf, layout_ref, image_ref, text, human_corrected, usable), `chunk` (page_from, page_to, section_path, context_header, text, tokens, qdrant_point_id, embed_model, effective_from/to, human_corrected, injection_suspected, pii_masked), `quality_issue` (page_no, chunk_id, extracted_record_id, kind ∈ {low_ocr, pii_unmasked, garbled_text, empty_chunk, low_confidence_field, field_mismatch} `alembic/sql/dataagent.sql:4590`, severity, status, resolution), `extraction_schema` (fields, target_dataset_id, join_key, sample_accuracy, status), `extracted_record` (values, confidence, source_spans, status, loaded_at), `transcript_segment`, `email_message`, `outbox`.

Missing: **no element table** (blocks live only in MinIO JSON), no element type on chunks, no bbox per chunk, **no parser/extractor version** column (grep: none — reprocess relies on hash reconciliation `handler.py:279-290`), no per-chunk confidence, `tokens` = whitespace word count (`handler.py:264`). Only `low_ocr` of the six quality kinds is ever produced (grep).

## 5. Chunking — **partial**

- Vietnamese legal structure-aware: Phần/Chương/Mục/Điều/Khoản regexes (`processing.py:40-43`); one Điều = one chunk across pages, cut between Khoản when > `MAX_CHUNK_CHARS = 4000` (`:20`, `:383-523`); text outside Điều packed per paragraph within one page (`:517-519`). `section_path` like "Chương II > Điều 5 > Khoản 2".
- **No overlap**, fixed 4000-char cap, no token-based sizing. **Non-legal documents (product manuals, reports) fall back to per-page paragraph packing** — heading levels detected in layout (`LayoutBlock.level`) are **not used** by the chunker (`document_chunks` takes `list[str]`, `:383`). AUD-15 flags this.
- Context header `title | Số doc_no | section_path` prefixed at embedding and at rerank (`:376-380`, `handler.py:73-74`, `retrieve.py:181-183`), not indexed by FTS (RG-06 note).
- Parent/child (RES-13): **missing** — `chunk` has no parent column.

## 6. Retrieval — **exists, with gaps**

- `wa/retrieve.py:101-252`: pre-filter (Data Space collections, purpose BR-59, `status=ready`, validity at `as_of` by chunk-or-document dates, no unusable page, `pii_masked`, not `injection_suspected`) `:112-137`; keyword = Postgres `to_tsvector('simple')` OR-of-words, GIN `chunk_text_idx` `:140-152`; dense = bge-m3 via `/v1/embeddings` + Qdrant per collection filtered by embed model (`search.py:58-89`); RRF k=60 pool 40 `:171-177`; TEI reranker `/rerank` (`search.py:91-108`); final order = 0.7·rerank/top + 0.3·weighted coverage (RES-12, `:44`, `:192-203`); bar `DOCUMENT_MIN_RERANK=0.2`, fallback `MIN_DENSE=0.55`/`MIN_OVERLAP=0.6` env-level (`:37-39`), relaxed retry off by default (`:45`). `superseded_by` annotated (`:222-232`).
- Permissions: Data Space + purpose + RLS session (`questions.py:2010`, `agentic/tools/documents.py:33`); **no per-collection/per-user ACL** beyond Data Space membership (grep BR-107/capability in retrieve: none).
- Query rewrite for follow-ups (RG-07): **missing** — raw `question.text` (`questions.py:2024`); agentic tool passes the model's `query` arg (`tools/documents.py:34-41`), the only de-facto rewrite.
- Diacritics: NFC + lowercase only (`:72-73`); **no `unaccent`/folded column** (grep: none; RG-04 todo); baseline "no diacritics 1/10".
- Stop-word list hard-coded Vietnamese (`:48-51`); no glossary/synonym expansion (grep: none).

## 7. Grounding / citations — **exists**

`wa/answer.py`: model returns claims with passage number + ≤40-word verbatim quote (`:32-37`); verifier keeps a claim only if every citation names a shown passage, `quote_match ≥ 0.9` via rapidfuzz window (`:29`, `:102-119`, `:223`), passage in force at `as_of` (`:121`), and every number in the claim appears in a cited passage (`:203-238`); citations numbered by first use, one per (passage, quote) (`:263-274`); supersession notes appended (`:132-138`); extractive fallback without a gateway (`:156-176`); "không tìm thấy" when nothing survives (`:141`). Agentic path: stable per-turn evidence registry (RES-11 done, `tools/documents.py:45-72`). UI: citation click → `requestPage(doc_id, page)` → `DocumentViewerPage` (`frontend/src/features/ask/AskDataPage.tsx:135-142`, `MarkdownContent.tsx:66`); page-level, not bbox-level highlighting.

## 8. Structured extraction / claims / knowledge / coverage — **partial**

- KB-07 extraction (`wa/extraction.py`): on `document.indexed`, active schema fields filled by LLM from ≤ passages; value kept only if quote is in a passage and numbers present (`_checked` `:68`), confidence 1.0 or 0.5 `UNCHECKED` (`:40`); required field unverifiable → `status="needs_review"` (`:102-136`); schema `:test` measures `sample_accuracy` over 20 docs, activation needs ≥ 0.95 (`api:118`, `:1585`). **Not done:** load into `target_dataset_id` (column written `api:1500` but never read for loading; `loaded_at` never set — grep), compare to structured data (AC-05-06-03), high-risk human review beyond `needs_review` status. Only `low_ocr` quality issues; `low_confidence_field/field_mismatch` never raised.
- KB-05 claims (`wa/claims.py`): subject/attribute/value/unit/valid_from/to per passage, quote ≥ 0.9, number-in-quote (`:96-110`); conflicts when same `subject_key+attribute` from another document, overlapping validity, different value → `conflict.conflict` + `conflict.detected` (`:185-255`). No auto-resolution (backlog KB-05 doing).
- KB-06 knowledge pages: tables (`context.knowledge_page/_version/_source_link/_claim/_figure/_run/_lint_finding`), 16 API routes (`api/modules/context/routes_knowledge.py`), frontend views exist; **no worker composer** (grep `knowledge_run` in worker_agent: none) — backlog todo.
- RG-08 coverage reporting for corpus-wide count/sum: **missing**.

## 9. Cross-document consistency & versions — **partial**

Exists: claim conflicts (above, test `test_two_documents_stating_different_fees_open_one_conflict`), replacement proposal by regex (Vietnamese-only patterns `validity.py:24-28`, AUD-15), `superseded_by` in passages, validity filter at `as_of`, versions chain route. Missing: text/structure diff of two versions, detection that two versions cover the same period without a "thay thế" sentence, English/other-language replacement phrasing, conflict auto-resolution by source authority.

## 10. Document ↔ structured data — **missing**

No column links a chunk/claim/extracted record to a metric or dataset: `conflict.claim.source_kind` ∈ {document, manual, answer} only (grep); `extraction_schema.target_dataset_id` unused downstream; `knowledge_figure.metric_code/dataset_id` (`models/context.py:905-920`) binds a *page placeholder* to a metric, but pages are not composed yet. "Số liệu này được định nghĩa trong tài liệu nào?" is answerable only by free-text retrieval of the metric name, not by a link.

## 11. Measured quality — **partial**

- `wa/retrieval_eval.py`: recall@1/3/10, MRR, not-found accuracy, forbidden rate, per tag, halves-run report; `make eval-retrieval` (Makefile:244). Cases: 156 (`model/eval/cases/retrieval.jsonl`: 133 found / 23 not_found, 54 with `forbid`; tags incl. theo-ngay 29, tieng-anh 26, docx 24, dinh-nghia 20, bay-so-lieu 20, bang 16, huong-dan-san-pham 15, khong-dau 10, pdf 8, follow-up 4). Gold quotes verified against seed by `tests/integration/worker_agent/test_adventureworks_retrieval.py:109`.
- Last numbers (full stack, DS-AW, 04/11/2026, `docs/research/ragflow-python.md:13-16`): 124/156, recall@1 0.75, recall@10 0.80, MRR 0.77, not-found 0.74, forbidden 0; weakest: no diacritics 1/10, cross-language 0/3, paraphrase 3/7, product guides 8/15, PDF 5/8, no-answer 5/10. Not in `model/eval/thresholds.json` gate (grep: none; RG-02 open).
- `grounding.jsonl` 16 cases with `scorers/grounding.py` (unsupported numbers) — offline, not run against document answers end-to-end.
- **Not measured:** OCR accuracy (no scanned ground truth; corpus has 1 PDF, 0 scans), table extraction accuracy, extraction F1 per field (only sample_accuracy on activation), claim/conflict precision, chunk boundary quality, end-to-end faithfulness/citation precision of document answers.

## 12. Interface for an external KB/indexing team — **partial, undocumented**

Events on `document.outbox` (producers): `document.received` (api `api:574`, sync `wa/sync.py:562`; payload document_id, collection_id, data_space_id, media, sha256, status), `document.reprocess` (`wd/reprocess.py:58`), `quality_issue.resolved` (`api:1364`), `document.validity_proposed` (`wd/validity.py:89`), `document.indexed` (`wd/handler.py:292-306`; payload document_id, collection_id, data_space_id only), `extraction_schema.test_requested`, `document_source.sync_requested`. Consumers: worker-document `HANDLERS` (`handler.py:622`), worker-agent `indexed.py:29`. Outbox is also CDC'd by Debezium to Kafka topic = event_type (`worker_agent/events.py:4`).
No event or API exposes chunks/elements in a stable schema: `GET /v1/documents/{id}/chunks` and `/pages/{n}` exist (`api:831`, `:937`) but the page layout JSON shape is marked "PROPOSAL" (`frontend/src/lib/v1/documents.ts:368`); no doc page lists these events (grep docs/: only cutover tables mention `document.indexed`).

## 13. Tests — what they prove

- `tests/unit/worker_document/`: test_pdf_pages 9 (reading order, frame/crop/rotation, pictures, 144 dpi preview, OCR on no-text-layer, pixel/page limits), test_extractors 24 (html main/charset/article chunking, docx headings/lists/tables/pictures/XXE, email, image/TIFF/OCR langs, audio refused, replacement regex), test_processing 11 (NFC, Điều/Khoản chunking, cross-page, context header, injection screen), test_reconcile 7, test_indexing 5, test_pii_packs 5, test_adventureworks_documents 6.
- `tests/integration/worker_document/test_handler.py` 9: ready with structured chunks, low-OCR waits review, resolution re-run, reconciliation keeps corrections, PII masked/reject, injection flagged+audited, replacement proposal, OCR languages.
- worker_agent: unit test_document_answer 9, test_retrieval_eval 4; integration test_document_answers 6, test_answer_verification 5, test_document_claims 4, test_document_extraction 6, test_document_sync 11, test_adventureworks_retrieval 4 (gold is real; expired/replaced/injected never returned; English and no-diacritics found on keyword half).
- api: test_document 25, lists 4, vectors 3, storage 2, secrets 4; e2e `test_kb_replacement_e2e.py:64`.
- Crawler (`backend/services/crawler`) is a separate web-source extraction domain (CSS/XPath records, `extract.py:1-10`), not a document ingester; it does not feed `document.*`.
- Not tested: PDF tables, scanned Vietnamese PDF end-to-end accuracy, multi-column PDFs, header/footer noise, non-legal heading chunking, XLSX/PPTX (unsupported).

## 14. Top 10 gaps vs required pipeline

| # | Stage | Gap | Sev | Where the fix goes |
|---|---|---|---|---|
| 1 | Elements/Structured output | Tables flattened to "a \| b" text; PDF tables not detected at all; no table element, header binding, cell positions (RG-05) | High | `wd/extractors.py` (docx/html table → block kind `table` with header rows), `wd/processing.py` (PDF table via pdfplumber/camelot-style line detection), new `document.element` or table JSON in `document_page.layout_ref` + chunk flag |
| 2 | Parsing/Layout | No header/footer/page-number/TOC removal; multi-column only via pdfminer defaults (RES-15) | High | `wd/processing.py:_native_layout` (drop repeated top/bottom 10% boxes across pages), LAParams tuning |
| 3 | Elements | Heading-level chunking for non-legal docs; no parent/child; no overlap (RES-13) | High | `wd/processing.py:document_chunks` take `LayoutBlock` levels instead of `list[str]`; add `chunk.parent_chunk_id` migration |
| 4 | Retrieval prep | No diacritic-insensitive lexical index (no-diacritics 1/10) (RG-04) | High | migration: `unaccent` + folded tsvector column/index on `document.chunk`; `wa/retrieve.py:_tsquery` query both |
| 5 | Intake | Extension-only type detection; no XLSX/PPTX/TXT/MD/.doc; raw bucket not immutable | Med | `api:96-114` + magic sniff in `upload_document`; new extractors in `wd/extractors.py`; `deploy/k8s/*/job/minio-init.yaml:56` `--with-lock` for `da-raw-docs` |
| 6 | Quality | Only `low_ocr` issue produced; no garbled-text/empty-chunk detection; no parser version for reproducible reprocess | Med | `wd/handler.py:_write_page/_write_chunks` raise `garbled_text`/`empty_chunk`; add `document_page.extractor_version` (`backend/schema`) |
| 7 | Retrieval | Follow-up questions retrieved verbatim (RG-07); thresholds env-global not per Data Space (RG-03) | Med | `questions.py:_answer_from_documents` rewrite via conversation history; `wa/retrieve.py:37-45` read from Data Space settings |
| 8 | Structured output | Extracted records never loaded to `target_dataset_id`; no corpus-wide count/sum with coverage (RG-08); no doc↔metric link | Med | `wa/extraction.py:extract_document` load step + `loaded_at`; new `claim.metric_id`/`chunk`→`catalog` link; agentic tool `count_documents` |
| 9 | Publication | No documented output contract (events, chunk/element schema) for external indexing; `document.indexed` payload carries ids only | Med | `docs/v3/06-api-v1.md` events table; enrich payload in `wd/handler.py:292-306` with page_count, chunk ids, embed_model, extractor version |
| 10 | Quality measurement | No OCR/table/extraction-F1 evals; retrieval baseline not gated; no scanned docs in corpus | Med | `model/eval/cases/` add scan + table cases; `model/eval/thresholds.json` + `gate.py` read retrieval report; `deploy/docker/adventureworks-docs/` add scanned PDF |

Also: AUD-15 (replacement regex and Điều/Khoản only Vietnamese; cross-language 0/3), audio never transcribed (`processing.py:129`), `.msg` refused, knowledge page composer absent (KB-06), health screen absent (RG-09).
