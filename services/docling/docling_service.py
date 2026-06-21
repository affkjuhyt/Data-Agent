import asyncio
from collections.abc import Generator
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    smolvlm_picture_description,
)
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import PictureItem, SectionHeaderItem, TableItem, TextItem

from basemodel.services_databaseconnector.shared_model import (
    HealthCheckLoopConfig,
    RetryConfig,
)
from basemodel.services_docling.docling_model import (
    DoclingResult,
    ParsedChunk,
    ParsedTable,
    SupportedExtension,
)
import logging

log = logging.getLogger(__name__)

MAX_CHUNK_CHARS: int = 4000
_worker_converter: DocumentConverter | None = None
_worker_use_vlm: bool = False


def _worker_init(use_vlm: bool) -> None:
    global _worker_converter, _worker_use_vlm
    _worker_use_vlm = use_vlm
    if use_vlm:
        pipeline_options = PdfPipelineOptions(
            do_picture_description=True,
            picture_description_options=smolvlm_picture_description,
            generate_picture_images=True,
        )
        _worker_converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )
    else:
        _worker_converter = DocumentConverter()
    log.info(f"Worker process ready (use_vlm={use_vlm})")


def _worker_ping() -> bool:
    return _worker_converter is not None


# ── Internal helpers ──────────────────────────────────────────────────────────


def _table_description(name: str, headers: list[str], rows: list[list[str]]) -> str:
    col_str = ", ".join(headers) if headers else "unknown columns"
    desc = f"Table '{name}' has {len(rows)} row(s) and {len(headers)} column(s): {col_str}."
    if rows:
        sample = "; ".join(f"{h}={v}" for h, v in zip(headers, rows[0]) if v)
        if sample:
            desc += f" First row: {sample}."
    return desc


def _build_parsed_tables(doc) -> list[ParsedTable | None]:
    """Build ParsedTable list from Docling's internal table model.

    Returns a list aligned 1-to-1 with doc.tables. None entries mark tables
    merged into the preceding entry (multi-page continuation detection).
    """
    result: list[ParsedTable | None] = []

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
        if last is not None and last.data["headers"] == headers:
            last.data["rows"].extend(rows)
            last.description = _table_description(
                last.table_name, last.data["headers"], last.data["rows"]
            )
            log.info(
                f"Merged split table '{last.table_name}': +{len(rows)} continuation row(s)"
            )
            result.append(None)
        else:
            result.append(
                ParsedTable(
                    table_name=name,
                    description=_table_description(name, headers, rows),
                    data={"headers": headers, "rows": rows},
                )
            )

    return result


def _iter_chunks(
    doc, parsed_tables: list[ParsedTable | None], max_chars: int
) -> Generator[ParsedChunk, None, None]:
    doc_idx = 0
    chunk_idx = 0
    buf: list[str] = []

    def flush() -> ParsedChunk | None:
        nonlocal chunk_idx
        text = "\n\n".join(buf).strip()
        buf.clear()
        if text:
            c = ParsedChunk(block_index=chunk_idx, content=text)
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
                    yield ParsedChunk(
                        block_index=chunk_idx,
                        content=pt.description,
                        table_involved=True,
                        table=pt,
                    )
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


def _run_parse(path: Path, max_chunk_chars: int) -> DoclingResult:
    """Runs inside the worker process — uses the process-global converter."""
    if _worker_converter is None:
        raise RuntimeError("Worker not initialised — _worker_init was not called")

    log.info(f"Parsing '{path.name}' with Docling")
    conversion = _worker_converter.convert(str(path))
    doc = conversion.document

    has_figures = len(list(doc.pictures)) > 0
    if _worker_use_vlm and has_figures:
        log.info(f"VLM pass: {len(list(doc.pictures))} figure(s) described by SmolVLM")

    parsed_tables = _build_parsed_tables(doc)
    real_count = sum(1 for t in parsed_tables if t is not None)
    merged_count = len(parsed_tables) - real_count
    log.info(
        f"Extracted {real_count} table(s) from '{path.name}'"
        + (f" ({merged_count} continuation page(s) merged)" if merged_count else "")
    )

    chunks = list(_iter_chunks(doc, parsed_tables, max_chunk_chars))
    log.info(f"Produced {len(chunks)} chunk(s) from '{path.name}'")

    return DoclingResult(has_figures=has_figures, chunks=chunks)


class DoclingClient:
    __slots__ = ("_executor", "_use_vlm", "_connected", "_healthy")

    def __init__(self, use_vlm: bool = False) -> None:
        self._executor: ProcessPoolExecutor | None = None
        self._use_vlm: bool = use_vlm
        self._connected: bool = False
        self._healthy: bool = False

    # Protocol surface
    def set_url(self, url: str) -> None:
        pass

    def get_client(self) -> "DoclingClient":
        return self

    async def open(self, retry: RetryConfig = RetryConfig()) -> None:
        """Spawn the worker process and load DocumentConverter inside it."""
        if self._connected:
            return
        for attempt in range(max(retry.count, 1)):
            try:
                self._executor = ProcessPoolExecutor(
                    max_workers=1,
                    initializer=_worker_init,
                    initargs=(self._use_vlm,),
                )
                self._connected = True
                self._healthy = True
                log.info(f"Docling process pool ready (use_vlm={self._use_vlm})")
                return
            except Exception as e:
                self._healthy = False
                log.info(
                    f"Docling open attempt {attempt + 1}/{retry.count} failed: {e}"
                )
        raise RuntimeError("Failed to start Docling process pool after all retries")

    async def close(self) -> None:
        if self._executor:
            self._executor.shutdown(wait=True)
            self._executor = None
        self._connected = False
        self._healthy = False
        log.info("Docling client closed")

    async def health_check_loop(
        self, config: HealthCheckLoopConfig = HealthCheckLoopConfig()
    ) -> None:
        """Pings the worker process to confirm it is alive and the model is loaded."""
        log.info("Docling health check loop started")
        while True:
            await asyncio.sleep(config.interval)
            try:
                loop = asyncio.get_running_loop()
                alive = await asyncio.wait_for(
                    loop.run_in_executor(self._executor, _worker_ping),
                    timeout=float(config.timeout_for_health_check),
                )
                self._healthy = bool(alive)
            except Exception:
                self._healthy = False
                log.info("Docling health check: worker unresponsive — respawning")
                try:
                    await self.close()
                    await self.open()
                except RuntimeError:
                    pass

    def is_healthy(self) -> bool:
        return self._healthy

    def is_connected(self) -> bool:
        return self._connected

    # Operations
    async def parse(
        self, file_path: str | Path, max_chunk_chars: int = MAX_CHUNK_CHARS
    ) -> DoclingResult:
        """Submit a parse job to the worker process and await the result."""
        path = Path(file_path)
        try:
            SupportedExtension(path.suffix.lower())
        except ValueError:
            raise ValueError(
                f"Unsupported file extension '{path.suffix}'. "
                f"Supported: {', '.join(sorted(e.value for e in SupportedExtension))}"
            )
        if self._executor is None:
            raise RuntimeError("DoclingClient is not open — call open() first")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, _run_parse, path, max_chunk_chars
        )


client = DoclingClient()
