"""
Core Docling parsing logic — ported from services/docling/docling_service.py.
Call load_converter() once at startup; then run_parse() per request.
"""
from __future__ import annotations

import logging
import tempfile
import os
from collections.abc import Generator
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    smolvlm_picture_description,
)
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import PictureItem, SectionHeaderItem, TableItem, TextItem

log = logging.getLogger(__name__)

MAX_CHUNK_CHARS: int = 1500
_converter: DocumentConverter | None = None
_use_vlm: bool = False


def load_converter(use_vlm: bool = False) -> None:
    global _converter, _use_vlm
    _use_vlm = use_vlm
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
        _converter = DocumentConverter()
    log.info("Docling converter ready (use_vlm=%s)", use_vlm)


def is_ready() -> bool:
    return _converter is not None


# ── Internal helpers ──────────────────────────────────────────────────────────

def _table_description(name: str, headers: list[str], rows: list[list[str]]) -> str:
    col_str = ", ".join(headers) if headers else "unknown columns"
    desc = f"Table '{name}' has {len(rows)} row(s) and {len(headers)} column(s): {col_str}."
    if rows:
        sample = "; ".join(f"{h}={v}" for h, v in zip(headers, rows[0]) if v)
        if sample:
            desc += f" First row: {sample}."
    return desc


def _build_parsed_tables(doc) -> list[dict | None]:
    result: list[dict | None] = []
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


def _iter_chunks(doc, parsed_tables: list[dict | None], max_chars: int) -> Generator[dict, None, None]:
    doc_idx = 0
    chunk_idx = 0
    buf: list[str] = []

    def flush() -> dict | None:
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
                    yield {
                        "block_index": chunk_idx,
                        "content": pt["description"],
                        "table_involved": True,
                        "table": pt,
                    }
                    chunk_idx += 1
        elif isinstance(item, SectionHeaderItem):
            chunk = flush()
            if chunk:
                yield chunk
            buf.append(f"{'#' * level} {item.text}")
        elif isinstance(item, (TextItem, PictureItem)):
            text = getattr(item, "text", "").strip()
            if not text:
                continue
            if buf and len("\n\n".join(buf)) + 2 + len(text) > max_chars:
                chunk = flush()
                if chunk:
                    yield chunk
            buf.append(text)

    chunk = flush()
    if chunk:
        yield chunk


def run_parse(file_bytes: bytes, extension: str, max_chunk_chars: int = MAX_CHUNK_CHARS) -> dict:
    """Write bytes to a temp file, parse with Docling, return serialisable result dict."""
    if _converter is None:
        raise RuntimeError("Converter not loaded — call load_converter() first")

    suffix = f".{extension.lstrip('.')}"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        conversion = _converter.convert(tmp_path)
        doc = conversion.document
        has_figures = bool(list(doc.pictures))
        parsed_tables = _build_parsed_tables(doc)
        chunks = list(_iter_chunks(doc, parsed_tables, max_chunk_chars))
        log.info("Parsed %d chunk(s) from %s file", len(chunks), extension)
        return {"has_figures": has_figures, "chunks": chunks}
    finally:
        os.unlink(tmp_path)
