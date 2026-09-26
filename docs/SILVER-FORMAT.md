# Silver stage — what goes in, what comes out

Reference for whoever owns a source feeding this pipeline (crawler side) and
whoever owns retrieval/synthesis reading its output (RAG side). Describes what
is actually implemented today, not a target — update it in the same commit
that changes the behaviour it documents.

## Routing by `source_type`

`libs/ingestion/silver/orchestrator.py`'s `_parse_source` picks the path:

| `source_type` | Condition | Parser | Output |
| --- | --- | --- | --- |
| `doc` | extension `json` or `xml` | `silver/parsing.py`'s `_parse_structured_text` | one block per top-level JSON element / RSS-or-Atom item |
| `doc` | anything else (pdf, docx, xlsx, xls, ...) | Docling (`docling_parser.py`) | markdown text + markdown tables |
| `web` | — | fetch live via the `web_crawl` connector, then Docling on the resulting bytes | same as `doc` |
| `image` | — | OCR only (Tesseract, vie+eng) | zero or more text chunks; no chunk if no readable text |
| `video` | — | OCR on every `VLM_FRAME_INTERVAL`-th frame | one chunk per frame with readable on-screen text; no speech-to-text |

A source's raw bytes decide the path — nothing about *who* produced the bytes
(scheduled crawl vs. manual upload) changes this.

## Table representation

A Docling table becomes one or more **markdown-table chunks**
(`_table_row_chunks`), not a prose paragraph — this is what an LLM parses most
reliably for exact lookups, and what most modern table-extraction pipelines
converge on. Every chunk repeats the caption and header row, so a chunk
retrieved on its own still reads as a complete table.

Column names come from Docling's per-cell `column_header` flag
(`_split_header`, added 2026-09-25), not from "row 0":

- A label that spans (almost) the whole table (a unit, "Ngày", "Đơn vị tính")
  moves to the table's name instead of being repeated in every column.
- Multiple header rows are joined top-to-bottom per column
  (`"Số con/phụ nữ / Thành thị Urban"`).
- A row flagged as header but mostly numeric (Docling occasionally
  over-flags an amounts row under the years) is treated as data instead.
- If Docling flags **no** header row at all, columns are named `Column 1..N`
  and nothing is guessed from the data.

**Known gap:** a source whose real header spans many columns with no
Docling-detected structure (seen on some GSO monthly-report XLSX sheets and
on the widest Bản tin nợ công tables) still falls through to `Column N`
naming. There is no fallback heuristic for this today — reprocessing after a
Docling upgrade is the only lever. If a source consistently lands here, the
fix belongs upstream: publish it as a cleaner table (a real header row, no
merged title cell spanning the sheet) rather than trying to out-guess it here.

**Table quality checklist for a new source** (crawler side, before wiring a
new connector to a spreadsheet/PDF-table source): does the file have (a) one
row that is unambiguously the header, with a distinct label in every column,
not a title spanning the row, and (b) no merged cells inside the data body?
A "yes" to both gets clean columns for free; a "no" produces `Column N`
columns and someone has to read the raw file to know what's in them.

## Text chunking (non-table content)

`docling_parser.py`'s `_iter_chunks`: a section header starts a new chunk
unless the running chunk is still small (`MIN_CHUNK_CHARS=200`); a text block
longer than `MAX_CHUNK_CHARS=4000` is split at sentence boundaries, hard-cut
as a last resort. Tables get a much larger ceiling
(`TABLE_MAX_CHUNK_CHARS=30000`) so a normal statistics table is one chunk —
splitting it defeats the point of a markdown table (retrieval would need to
land on the one fragment containing the requested row).

## Structured text (JSON / XML)

Docling only reads JSON in its own document schema and XML in a handful of
domain schemas — an API response or an RSS feed fails outright otherwise, so
these two never reach Docling. `silver/parsing.py`:

- **JSON**: one block per top-level array element (or the whole object if not
  a list), pretty-printed.
- **XML**: if the document has `<item>`/`<entry>` elements (RSS/Atom), one
  block per item with the well-known feed fields (`title`, `link`,
  `description`, `pubDate`, ...); otherwise, one block of all text content.

Both get packed into `_STRUCTURED_MAX_CHARS=4000` chunks the same way plain
text is.

## Format conversions before parsing

- **Legacy `.xls` (binary OLE)** → converted to `.xlsx` via `pandas`
  (`xlrd` read, `openpyxl` write) before Docling ever sees it — Docling reads
  OOXML only. Cell values carry over; formatting does not.
- **Mislabeled `Content-Type`**: some APIs (seen on UN Comtrade) declare
  `application/octet-stream` for an actual JSON/XML body. `infer_extension()`
  sniffs the first bytes as a last resort before defaulting to `.bin` (which
  Docling rejects outright).

## Data Purpose and how far a document travels

`libs/ingestion/purpose.py`'s `MAX_TIER_BY_PURPOSE`: `production` reaches
Gold (embedded, searchable); `training` and `evaluation` stop at Silver —
parsed and chunked, kept in Postgres/Mongo staging, never embedded into
Qdrant. Enforced once, in `promote_to_gold` itself, so every caller (manual
click, the Silver→Gold auto-chain, a replayed Kafka event) is covered by the
same guard.

## What this does *not* cover

- **Structured-table ingestion mode** (`ingestion_mode="structured_table"`,
  a spreadsheet imported as a live SQL table) is a separate pipeline
  (`kb_backend/router/knowledge/structured_table.py`), not part of Silver.
- **The raw XLSX preview** in Data Layers' Preview tab is a client-side
  SheetJS parse for a quick look at the uploaded file — it does not go
  through Docling and is not what retrieval reads. It has its own
  first-non-title-row header heuristic (`inventory.data.ts`'s
  `pickHeaderRowIndex`), independent of the table logic above.
- **Parser choice itself** (Docling vs. an alternative) has not been
  benchmarked against other extractors — only Docling's own header-detection
  bug has been fixed. `tests/fixtures/docling_table_grids.json` holds real
  Docling grids from Niên giám thống kê / Bản tin nợ công that could seed a
  future side-by-side comparison if quality issues persist after this fix.
