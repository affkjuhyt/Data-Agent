"""
InlineDoclingParser — runs Docling in-process (no HTTP service).
Provides the same .parse(bucket, object_key, extension) interface as DoclingServiceClient
so silver.py needs no changes.
"""
from __future__ import annotations

from typing import Any
import asyncio
import io
import os
import re
import tempfile
from collections.abc import Generator

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    TesseractCliOcrOptions,
    smolvlm_picture_description,
)
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import PictureItem, SectionHeaderItem, TableItem, TextItem

from libs.basemodel.services_docling.docling_model import (
    DoclingResult,
    PageRegion,
    ParsedChunk,
    ParsedTable,
)
from libs.database_connector.minio_connector import MinIOClient

# Hard upper bound on chunk size (chars). Soft lower bound below which a section
# header will NOT start a new chunk — prevents back-to-back headers (e.g. title +
# subtitle + section heading) from each emitting a tiny header-only chunk.
MAX_CHUNK_CHARS: int = 4000
MIN_CHUNK_CHARS: int = 200

# Tables get a much larger ceiling than prose. A GSO-style statistics table
# (tens of rows, ~20 year columns) is a few thousand characters as markdown —
# comfortably one chunk. Fragmenting it across several row-group chunks (the
# previous approach) means retrieval has to happen to pull back the ONE
# fragment that contains the specific row a question asks about; answer
# synthesis reads the full Postgres `content` of whichever chunk gets
# retrieved (see kb_backend/pipeline/light_rag/pipeline.py's
# `chunk_context = pg.get("content") or ...`), not a truncated preview, so a
# bigger single chunk directly means "the whole table was in context", not
# just "the embedding saw more of it". Only tables past this size (rare in
# practice) still split by row groups. Real precise aggregation/lookup still
# belongs to the structured-table -> SQL path, not vector chunks at any size.
TABLE_MAX_CHUNK_CHARS: int = 30000

_converter: DocumentConverter | None = None

# Named explicitly instead of leaving docling on its `auto` OCR default, which
# probes for whichever backend happens to be importable — a rebuild that
# changes the installed set would then silently change extracted text.
# Tesseract ships its language data as an apt package (tesseract-ocr-vie/-eng
# in this service's Dockerfile), so nothing is downloaded at runtime and the
# same image always extracts the same text. Docling only runs OCR on regions
# with no existing text layer, so a born-digital PDF keeps its embedded text
# instead of being re-recognized at a loss — this was previously off entirely
# (do_ocr=False), which made a scanned PDF ingest as a silent empty document.
_OCR_OPTIONS = TesseractCliOcrOptions(lang=["vie", "eng"])


def load_converter(use_vlm: bool = False) -> None:
    global _converter
    if use_vlm:
        pipeline_options = PdfPipelineOptions(
            do_ocr=True,
            ocr_options=_OCR_OPTIONS,
            do_picture_description=True,
            picture_description_options=smolvlm_picture_description,
            generate_picture_images=True,
        )
    else:
        pipeline_options = PdfPipelineOptions(do_ocr=True, ocr_options=_OCR_OPTIONS)
    _converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
    )


# ── Internal parse helpers ────────────────────────────────────────────────────

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _split_oversized(text: str, max_chars: int) -> list[str]:
    """Split a text block that exceeds max_chars into <=max_chars segments.

    Breaks at sentence boundaries; a single sentence longer than the ceiling is
    hard char-cut as a last resort. Returns [text] unchanged when it already fits.
    """
    if len(text) <= max_chars:
        return [text]
    segments: list[str] = []
    cur = ""
    for sentence in _SENTENCE_SPLIT.split(text):
        if not sentence:
            continue
        if len(sentence) > max_chars:
            if cur:
                segments.append(cur)
                cur = ""
            for i in range(0, len(sentence), max_chars):
                segments.append(sentence[i:i + max_chars])
            continue
        if cur and len(cur) + 1 + len(sentence) > max_chars:
            segments.append(cur)
            cur = sentence
        else:
            cur = f"{cur} {sentence}" if cur else sentence
    if cur:
        segments.append(cur)
    return segments


def _page_heights(doc: Any) -> dict[int, tuple[float, float]]:
    sizes = {}
    for page_no, page in getattr(doc, "pages", {}).items():
        size = getattr(page, "size", None)
        if size is not None:
            sizes[page_no] = (float(size.width), float(size.height))
    return sizes


def _regions(item: Any, sizes: dict[int, tuple[float, float]]) -> list[dict[str, Any]]:
    """Top-left-origin boxes for one document item, one per page it touches."""
    out = []
    for prov in getattr(item, "prov", None) or []:
        page_no = getattr(prov, "page_no", None)
        bbox = getattr(prov, "bbox", None)
        if page_no is None or bbox is None or page_no not in sizes:
            continue
        width, height = sizes[page_no]
        try:
            box = bbox.to_top_left_origin(page_height=height)
        # A backend that reports a malformed box should cost the citation its
        # coordinates, not cost the document its text.
        except Exception:  # noqa: BLE001
            continue
        out.append({
            "page_no": page_no,
            "left": float(box.l), "top": float(box.t),
            "right": float(box.r), "bottom": float(box.b),
            "page_width": width, "page_height": height,
        })
    return out


def _merge_regions(regions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """One box for a chunk assembled from several items.

    A chunk can span pages; the citation anchors to the FIRST one and unions
    only the boxes there. Anchoring to where the chunk starts is the honest
    choice — a box spanning two pages is not a place a reader can be sent to.
    """
    if not regions:
        return None
    first = min(r["page_no"] for r in regions)
    on_page = [r for r in regions if r["page_no"] == first]
    return {
        "page_no": first,
        "left": min(r["left"] for r in on_page),
        "top": min(r["top"] for r in on_page),
        "right": max(r["right"] for r in on_page),
        "bottom": max(r["bottom"] for r in on_page),
        "page_width": on_page[0]["page_width"],
        "page_height": on_page[0]["page_height"],
    }


def _table_description(name: str, headers: list[str], rows: list[list[str]]) -> str:
    col_str = ", ".join(headers) if headers else "unknown columns"
    desc = f"Table '{name}' has {len(rows)} row(s) and {len(headers)} column(s): {col_str}."
    if rows:
        sample = "; ".join(f"{h}={v}" for h, v in zip(headers, rows[0]) if v)
        if sample:
            desc += f" First row: {sample}."
    return desc


def _md_cell(value: str) -> str:
    # Pipes and newlines break markdown table syntax; a table cell should
    # never legitimately contain either for this kind of statistics data.
    return str(value).replace("|", "/").replace("\n", " ").strip()


def _table_caption(name: str, headers: list[str], total_rows: int) -> str:
    col_str = ", ".join(h for h in headers if h) or "unknown columns"
    return f"Table: {name} ({total_rows} row(s), {len(headers)} column(s): {col_str})"


def _table_markdown_header(headers: list[str]) -> str:
    display = [_md_cell(h) or "—" for h in headers]
    header_row = "| " + " | ".join(display) + " |"
    sep_row = "| " + " | ".join("---" for _ in display) + " |"
    return header_row + "\n" + sep_row


def _table_row_chunks(name: str, headers: list[str], rows: list[list[str]], max_chars: int) -> list[str]:
    """Serialize the whole table as one or more markdown-table chunks —
    unlike _table_description (a short one-liner kept for the Tables tab UI,
    which only ever samples row 0), this is what actually becomes
    embedded/searchable chunk content, so a query about row 47 of a 71-row
    statistics table has something to match against. Markdown-table syntax
    rather than a "header=value" prose dump: LLMs read/parse it far more
    reliably for exact lookups, and it is what most modern table-extraction
    pipelines converge on for this reason. `max_chars` (TABLE_MAX_CHUNK_CHARS,
    much larger than the plain-text ceiling) keeps a normal-sized statistics
    table to ONE chunk — every fragment repeats the caption + header + rule so
    a row-group chunk still stands alone if retrieved in isolation, only for
    the rare table too big to fit in one."""
    caption = _table_caption(name, headers, len(rows))
    if not rows:
        return [caption]

    md_header = _table_markdown_header(headers)
    preamble = caption + "\n" + md_header
    row_lines = [
        "| " + " | ".join(_md_cell(row[i]) if i < len(row) else "" for i in range(len(headers))) + " |"
        for row in rows
    ]

    chunks: list[str] = []
    cur: list[str] = []
    cur_len = len(preamble)
    for line in row_lines:
        line_len = len(line) + 1
        if cur and cur_len + line_len > max_chars:
            chunks.append(preamble + "\n" + "\n".join(cur))
            cur = []
            cur_len = len(preamble)
        cur.append(line)
        cur_len += line_len
    if cur:
        chunks.append(preamble + "\n" + "\n".join(cur))
    return chunks


_NUMBER_CELL = re.compile(r"^[\d.,\s%()/\-\u2212PR]+$")
_YEAR_CELL = re.compile(r"^(\d{1,2}/)?(\d{1,2}/)?(19|20)\d{2}(\s*\([PR]\))?$")  # 2024, 30/6/2023, 6/2023 (P)


def _is_data_number(text: str) -> bool:
    text = text.strip()
    return bool(text) and any(ch.isdigit() for ch in text) and bool(_NUMBER_CELL.match(text)) \
        and not _YEAR_CELL.match(text)


def _dedupe_consecutive(parts: list[str]) -> list[str]:
    return [part for i, part in enumerate(parts) if i == 0 or part != parts[i - 1]]


def _split_header(grid: list[list[Any]]) -> tuple[list[str], int, list[str]]:
    """(column names, number of header rows, table-wide labels).

    Statistics tables carry a header spread over several rows with merged
    cells (a unit label over every column, "By sex" over Male/Female, a year
    under a period). Docling flags those rows with `column_header`, so the
    column name is the top-to-bottom join of the labels above that column,
    and a label that spans (almost) the whole table is a unit/title, kept out
    of the column names. Taking `grid[0]` as the header instead left every
    merged cell repeated in each column and the real column labels sitting in
    the first "data" row.

    When Docling flagged no header and the first row is mostly numbers, that
    row is data, so columns get neutral names rather than a data row's values.
    """
    ncols = len(grid[0])
    header_rows = 0
    for row in grid:
        texts = [cell.text.strip() for cell in row if cell.text.strip()]
        # Docling sometimes flags the first row of figures as header too; a row
        # of mostly numbers (years excluded) is data whatever the flag says.
        mostly_figures = bool(texts) and sum(_is_data_number(t) for t in texts) * 2 >= len(texts)
        if any(getattr(cell, "column_header", False) for cell in row) and not mostly_figures:
            header_rows += 1
        else:
            break

    if header_rows == 0:
        first = [cell.text.strip() for cell in grid[0]]
        numeric = sum(1 for text in first if _is_data_number(text))
        if numeric * 2 >= max(len(first), 1):
            return [f"Column {i + 1}" for i in range(ncols)], 0, []
        return first, 1, []

    parts: list[list[str]] = [[] for _ in range(ncols)]
    labels: list[str] = []
    for r in range(header_rows):
        for c, cell in enumerate(grid[r]):
            if cell.start_row_offset_idx != r or cell.start_col_offset_idx != c:
                continue  # continuation of a merged cell, already handled at its origin
            text = cell.text.strip()
            if not text:
                continue
            first_col, last_col = cell.start_col_offset_idx, min(cell.end_col_offset_idx, ncols)
            if ncols >= 3 and last_col - first_col >= ncols - 1:
                if text not in labels:
                    labels.append(text)
                continue
            for col in range(first_col, last_col):
                parts[col].append(text)
    return [" / ".join(_dedupe_consecutive(p)) for p in parts], header_rows, labels


def _build_parsed_tables(
    doc: Any, sizes: dict[int, tuple[float, float]] | None = None
) -> list[dict[str, Any] | None]:
    result: list[dict[str, Any] | None] = []
    for idx, table_item in enumerate(doc.tables):
        data = table_item.data
        if not data.grid:
            result.append(None)
            continue
        headers, header_rows, labels = _split_header(data.grid)
        rows = [[cell.text.strip() for cell in row] for row in data.grid[header_rows:]]
        rows = [r for r in rows if any(r)]
        try:
            name = table_item.caption_text(doc).strip() or f"Table_{idx + 1}"
        except Exception:
            name = f"Table_{idx + 1}"
        if labels:
            name = f"{name} ({'; '.join(labels)})"
        # No page sizes (callers that only need the table grid) means no region,
        # the same outcome as a format without page geometry.
        region = _merge_regions(_regions(table_item, sizes or {}))
        last = next((r for r in reversed(result) if r is not None), None)
        if last is not None and last["data"]["headers"] == headers:
            last["data"]["rows"].extend(rows)
            last["description"] = _table_description(last["table_name"], headers, last["data"]["rows"])
            # A continuation keeps the first fragment's anchor: the table is
            # cited where a reader starts reading it, not where it ends.
            result.append(None)
        else:
            result.append({
                "table_name": name,
                "description": _table_description(name, headers, rows),
                "data": {"headers": headers, "rows": rows},
                "region": region,
            })
    return result


def _iter_chunks(
    doc: Any,
    parsed_tables: list[dict[str, Any] | None],
    max_chars: int,
    sizes: dict[int, tuple[float, float]],
) -> Generator[dict[str, Any], None, None]:
    doc_idx = 0
    chunk_idx = 0
    buf: list[str] = []
    buf_regions: list[dict[str, Any]] = []

    def flush() -> dict[str, Any] | None:
        nonlocal chunk_idx
        text = "\n\n".join(buf).strip()
        region = _merge_regions(buf_regions)
        buf.clear()
        buf_regions.clear()
        if text:
            c = {"block_index": chunk_idx, "content": text, "table_involved": False,
                 "table": None, "region": region}
            chunk_idx += 1
            return c
        return None

    for item, level in doc.iterate_items():
        if isinstance(item, TableItem):
            chunk = flush()
            if chunk:
                yield chunk
            if doc_idx < len(parsed_tables):
                pt = parsed_tables[doc_idx]
                doc_idx += 1
                if pt is not None:
                    row_texts = _table_row_chunks(
                        pt["table_name"], pt["data"]["headers"], pt["data"]["rows"], TABLE_MAX_CHUNK_CHARS,
                    )
                    for i, text in enumerate(row_texts):
                        yield {
                            "block_index": chunk_idx,
                            "content": text,
                            "table_involved": True,
                            # Full row data only goes on the first fragment — every
                            # fragment already carries the header line, and attaching
                            # it to each would persist the same rows N times into
                            # KBTextTable for no benefit (the Tables tab reads it once).
                            "table": pt if i == 0 else None,
                            "region": pt.get("region"),
                        }
                        chunk_idx += 1
        elif isinstance(item, SectionHeaderItem):
            header = f"{'#' * level} {item.text}"
            cur_len = len("\n\n".join(buf))
            # Only start a new chunk if the buffer already holds enough content, or
            # if appending the header would breach the max. Otherwise keep the header
            # with the running buffer so consecutive headers don't yield tiny chunks.
            if buf and (cur_len >= MIN_CHUNK_CHARS or cur_len + 2 + len(header) > max_chars):
                chunk = flush()
                if chunk:
                    yield chunk
            buf.append(header)
            buf_regions.extend(_regions(item, sizes))
        elif isinstance(item, (TextItem, PictureItem)):
            text = getattr(item, "text", "").strip()
            if not text:
                continue
            # A single item longer than max_chars is split so no chunk exceeds the
            # ceiling; each segment then flows through the normal accumulation guard.
            item_regions = _regions(item, sizes)
            for seg in _split_oversized(text, max_chars):
                if buf and len("\n\n".join(buf)) + 2 + len(seg) > max_chars:
                    chunk = flush()
                    if chunk:
                        yield chunk
                buf.append(seg)
                # Every segment of a split item carries the whole item's boxes:
                # _split_oversized cuts on sentences, which Docling's geometry
                # does not track, so claiming a narrower box per segment would
                # be a coordinate nobody measured.
                buf_regions.extend(item_regions)

    chunk = flush()
    if chunk:
        yield chunk


def _run_parse_sync(file_bytes: bytes, extension: str, max_chunk_chars: int = MAX_CHUNK_CHARS) -> dict[str, Any]:
    if _converter is None:
        raise RuntimeError("Converter not loaded — call load_converter() first")
    suffix = f".{extension.lstrip('.')}"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name
    try:
        conversion = _converter.convert(tmp_path)
        doc = conversion.document
        sizes = _page_heights(doc)
        parsed_tables = _build_parsed_tables(doc, sizes)
        chunks = list(_iter_chunks(doc, parsed_tables, max_chunk_chars, sizes))
        return {"has_figures": bool(list(doc.pictures)), "chunks": chunks}
    finally:
        os.unlink(tmp_path)


def _xls_to_xlsx(file_bytes: bytes) -> bytes:
    """Docling reads OOXML only; the legacy binary .xls (still what statistics
    offices publish) fails with "unexpected error occurred while opening the
    document". Cell values carry over, cell formatting does not."""
    import pandas as pd

    sheets = pd.read_excel(io.BytesIO(file_bytes), sheet_name=None, header=None, engine="xlrd")
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=str(name)[:31] or "Sheet", header=False, index=False)
    return out.getvalue()


# ── Public interface ──────────────────────────────────────────────────────────

class InlineDoclingParser:
    """Drop-in replacement for DoclingServiceClient — runs Docling in a thread executor."""

    def __init__(self, minio: MinIOClient) -> None:
        self._minio = minio

    async def parse(
        self,
        bucket: str,
        object_key: str,
        extension: str,
        max_chunk_chars: int = MAX_CHUNK_CHARS,
    ) -> DoclingResult:
        file_bytes = await self._minio.download(bucket, object_key)
        return await self.parse_bytes(file_bytes, extension, max_chunk_chars)

    async def parse_bytes(
        self,
        file_bytes: bytes,
        extension: str,
        max_chunk_chars: int = MAX_CHUNK_CHARS,
    ) -> DoclingResult:
        """Parse in-memory bytes (no MinIO download). Used by the web path, which
        fetches + cleans HTML→markdown before handing it to Docling."""
        loop = asyncio.get_event_loop()
        if extension.lower().lstrip(".") == "xls":
            file_bytes = await loop.run_in_executor(None, _xls_to_xlsx, file_bytes)
            extension = "xlsx"
        result = await loop.run_in_executor(
            None, _run_parse_sync, file_bytes, extension, max_chunk_chars
        )

        chunks = [
            ParsedChunk(
                block_index=c["block_index"],
                content=c["content"],
                table_involved=c["table_involved"],
                table=ParsedTable(**c["table"]) if c.get("table") else None,
                region=PageRegion(**c["region"]) if c.get("region") else None,
            )
            for c in result["chunks"]
        ]
        return DoclingResult(has_figures=result["has_figures"], chunks=chunks)
