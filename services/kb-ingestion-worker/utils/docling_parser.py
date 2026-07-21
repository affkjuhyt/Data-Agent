"""
InlineDoclingParser — runs Docling in-process (no HTTP service).
Provides the same .parse(bucket, object_key, extension) interface as DoclingServiceClient
so silver.py needs no changes.
"""
from __future__ import annotations

from typing import Any
import asyncio
import logging
import os
import re
import tempfile
from collections.abc import Generator

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    smolvlm_picture_description,
)
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import PictureItem, SectionHeaderItem, TableItem, TextItem

from libs.basemodel.services_docling.docling_model import DoclingResult, ParsedChunk, ParsedTable
from libs.database_connector.minio_connector import MinIOClient

log = logging.getLogger(__name__)

# Hard upper bound on chunk size (chars). Soft lower bound below which a section
# header will NOT start a new chunk — prevents back-to-back headers (e.g. title +
# subtitle + section heading) from each emitting a tiny header-only chunk.
MAX_CHUNK_CHARS: int = 4000
MIN_CHUNK_CHARS: int = 200

_converter: DocumentConverter | None = None


def load_converter(use_vlm: bool = False) -> None:
    global _converter
    if use_vlm:
        pipeline_options = PdfPipelineOptions(
            do_picture_description=True,
            picture_description_options=smolvlm_picture_description,
            generate_picture_images=True,
        )
        _converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
        )
    else:
        # Disable OCR — text-based PDFs don't need it, and the available OCR
        # backends (torch.PP-OCRv6) are unsupported in this environment.
        pipeline_options = PdfPipelineOptions(do_ocr=False)
        _converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
        )
    log.info("Docling converter ready (use_vlm=%s)", use_vlm)


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


def _table_description(name: str, headers: list[str], rows: list[list[str]]) -> str:
    col_str = ", ".join(headers) if headers else "unknown columns"
    desc = f"Table '{name}' has {len(rows)} row(s) and {len(headers)} column(s): {col_str}."
    if rows:
        sample = "; ".join(f"{h}={v}" for h, v in zip(headers, rows[0]) if v)
        if sample:
            desc += f" First row: {sample}."
    return desc


def _build_parsed_tables(doc: Any) -> list[dict[str, Any] | None]:
    result: list[dict[str, Any] | None] = []
    for idx, table_item in enumerate(doc.tables):
        data = table_item.data
        if not data.grid:
            result.append(None)
            continue
        headers = [cell.text.strip() for cell in data.grid[0]]
        rows = [[cell.text.strip() for cell in row] for row in data.grid[1:]]
        rows = [r for r in rows if any(r)]
        try:
            name = table_item.caption_text(doc).strip() or f"Table_{idx + 1}"
        except Exception:
            name = f"Table_{idx + 1}"
        last = next((r for r in reversed(result) if r is not None), None)
        if last is not None and last["data"]["headers"] == headers:
            last["data"]["rows"].extend(rows)
            last["description"] = _table_description(last["table_name"], headers, last["data"]["rows"])
            result.append(None)
        else:
            result.append({
                "table_name": name,
                "description": _table_description(name, headers, rows),
                "data": {"headers": headers, "rows": rows},
            })
    return result


def _iter_chunks(doc: Any, parsed_tables: list[dict[str, Any] | None], max_chars: int) -> Generator[dict[str, Any], None, None]:
    doc_idx = 0
    chunk_idx = 0
    buf: list[str] = []

    def flush() -> dict[str, Any] | None:
        nonlocal chunk_idx
        text = "\n\n".join(buf).strip()
        buf.clear()
        if text:
            c = {"block_index": chunk_idx, "content": text, "table_involved": False, "table": None}
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
                    yield {"block_index": chunk_idx, "content": pt["description"],
                           "table_involved": True, "table": pt}
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
        elif isinstance(item, (TextItem, PictureItem)):
            text = getattr(item, "text", "").strip()
            if not text:
                continue
            # A single item longer than max_chars is split so no chunk exceeds the
            # ceiling; each segment then flows through the normal accumulation guard.
            for seg in _split_oversized(text, max_chars):
                if buf and len("\n\n".join(buf)) + 2 + len(seg) > max_chars:
                    chunk = flush()
                    if chunk:
                        yield chunk
                buf.append(seg)

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
        parsed_tables = _build_parsed_tables(doc)
        chunks = list(_iter_chunks(doc, parsed_tables, max_chunk_chars))
        log.info("Parsed %d chunk(s) from %s", len(chunks), extension)
        return {"has_figures": bool(list(doc.pictures)), "chunks": chunks}
    finally:
        os.unlink(tmp_path)


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
        log.info("InlineDocling: downloaded %d bytes key=%s", len(file_bytes), object_key)
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
        result = await loop.run_in_executor(
            None, _run_parse_sync, file_bytes, extension, max_chunk_chars
        )

        chunks = [
            ParsedChunk(
                block_index=c["block_index"],
                content=c["content"],
                table_involved=c["table_involved"],
                table=ParsedTable(**c["table"]) if c.get("table") else None,
            )
            for c in result["chunks"]
        ]
        return DoclingResult(has_figures=result["has_figures"], chunks=chunks)
