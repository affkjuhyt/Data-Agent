"""Source parsing helpers for the silver pipeline."""
from __future__ import annotations

import asyncio
import io
import json
from typing import Any

from libs.ingestion.connectors import web_crawl as web_crawl_connector
from libs.ingestion.silver.shared import _ocr_image_bytes
from libs.ingestion import config as cfg


def _docling_chunks_to_dicts(docling_result: Any) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for parsed_chunk in docling_result.chunks:
        chunk: dict[str, Any] = {
            "block_index": parsed_chunk.block_index,
            "content": parsed_chunk.content,
            "table_involved": parsed_chunk.table_involved,
            "table": None,
        }
        if parsed_chunk.table is not None:
            chunk["table"] = {
                "table_name": parsed_chunk.table.table_name,
                "description": parsed_chunk.table.description,
                "data": parsed_chunk.table.data,
            }
        chunks.append(chunk)
    return chunks


async def _parse_doc(docling: Any, bucket: str, object_key: str, extension: str) -> list[dict[str, Any]]:
    result = await docling.parse(bucket=bucket, object_key=object_key, extension=extension)
    return _docling_chunks_to_dicts(result)


# Docling reads JSON only in its own document schema and XML only in a few
# domain schemas (JATS, XBRL, USPTO), so an API response or an RSS feed fails
# with "document backend could not parse the input". These two are turned
# into text here instead.
STRUCTURED_TEXT_EXTENSIONS = frozenset({"json", "xml"})
_STRUCTURED_MAX_CHARS = 4000  # same budget as docling_parser.MAX_CHUNK_CHARS
_FEED_ITEM_TAGS = ("item", "entry")
_FEED_FIELD_TAGS = (
    "title", "link", "description", "summary", "content", "encoded",
    "pubdate", "published", "updated", "author", "creator", "category",
)


def _pack_blocks(blocks: list[str], max_chars: int = _STRUCTURED_MAX_CHARS) -> list[dict[str, Any]]:
    """Group consecutive blocks into chunks of at most `max_chars`; a single
    block longer than that is cut at line boundaries (then hard-cut)."""
    pieces: list[str] = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if len(block) <= max_chars:
            pieces.append(block)
            continue
        current = ""
        for line in block.splitlines():
            while len(line) > max_chars:
                if current:
                    pieces.append(current)
                    current = ""
                pieces.append(line[:max_chars])
                line = line[max_chars:]
            if current and len(current) + len(line) + 1 > max_chars:
                pieces.append(current)
                current = ""
            current = f"{current}\n{line}" if current else line
        if current:
            pieces.append(current)

    chunks: list[str] = []
    for piece in pieces:
        if chunks and len(chunks[-1]) + len(piece) + 2 <= max_chars:
            chunks[-1] = f"{chunks[-1]}\n\n{piece}"
        else:
            chunks.append(piece)
    return [
        {"block_index": index, "content": content, "table_involved": False, "table": None}
        for index, content in enumerate(chunks)
    ]


def _json_blocks(raw: bytes) -> list[str]:
    text = raw.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text)
    except ValueError:
        return [text]
    # One block per top-level element keeps a record together rather than
    # letting a chunk boundary fall inside it.
    elements = parsed if isinstance(parsed, list) else [parsed]
    return [json.dumps(element, ensure_ascii=False, indent=2) for element in elements]


def _xml_blocks(raw: bytes) -> list[str]:
    from lxml import etree

    # Feeds come from third-party sites: no entity expansion, no network.
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, recover=True)
    root = etree.fromstring(raw, parser)
    if root is None:
        return []

    def local(tag: Any) -> str:
        return etree.QName(tag).localname.lower() if isinstance(tag, str) else ""

    items = [el for el in root.iter() if local(el.tag) in _FEED_ITEM_TAGS]
    if not items:
        return [" ".join(part.strip() for part in root.itertext() if part.strip())]

    blocks: list[str] = []
    for item in items:
        lines: list[str] = []
        for child in item:
            name = local(child.tag)
            if name not in _FEED_FIELD_TAGS:
                continue
            value = " ".join(part.strip() for part in child.itertext() if part.strip())
            value = value or (child.get("href") or "")  # Atom carries the link in an attribute
            if value:
                lines.append(f"{name}: {value}")
        if lines:
            blocks.append("\n".join(lines))
    return blocks


async def _parse_structured_text(file_bytes: bytes, extension: str) -> list[dict[str, Any]]:
    blocks = await asyncio.to_thread(
        _json_blocks if extension.lower().lstrip(".") == "json" else _xml_blocks, file_bytes,
    )
    return _pack_blocks(blocks)


async def _parse_web(docling: Any, url: str) -> list[dict[str, Any]]:
    """Fetch + extraction now live in `libs.ingestion.connectors.web_crawl`
    (shared with the scheduled `web-crawler` platform worker) — this just
    bridges that sync connector into the async silver pipeline via an
    executor, the same way the CPU-bound trafilatura call used to be bridged
    on its own before the refactor."""
    if not url:
        raise ValueError("Web document has no URL in its metadata")
    loop = asyncio.get_event_loop()
    artifact = await loop.run_in_executor(
        None, web_crawl_connector.fetch, {"url": url, "render": cfg.WEB_USE_PLAYWRIGHT},
    )
    result = await docling.parse_bytes(artifact.content_bytes, artifact.extension)
    return _docling_chunks_to_dicts(result)


async def _parse_image(file_bytes: bytes, extension: str) -> list[dict[str, Any]]:
    """OCR only (see shared._ocr_image_bytes) — a photo with no readable text
    produces zero chunks, not an error."""
    text = await asyncio.to_thread(_ocr_image_bytes, file_bytes)
    if not text:
        return []
    return [{"block_index": 0, "content": text, "table_involved": False, "table": None}]


async def _parse_video(file_bytes: bytes) -> list[dict[str, Any]]:
    """Same OCR engine as `_parse_image`, run against every VLM_FRAME_INTERVAL-th
    sampled frame — catches on-screen text (slides, captions, signage), NOT
    spoken audio: this pipeline has no speech-to-text step. A frame with no
    readable text contributes no chunk, so a video with no on-screen text
    (e.g. only speech) legitimately produces zero chunks."""
    try:
        import av  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError("PyAV not installed — cannot extract video frames") from exc

    container = av.open(io.BytesIO(file_bytes))
    video_stream = next((stream for stream in container.streams if stream.type == "video"), None)
    if video_stream is None:
        raise ValueError("No video stream found in file")

    chunks: list[dict[str, Any]] = []
    for frame_index, frame in enumerate(container.decode(video_stream)):
        if frame_index % cfg.VLM_FRAME_INTERVAL != 0:
            continue
        buffer = io.BytesIO()
        frame.to_image().save(buffer, format="JPEG")
        text = await asyncio.to_thread(_ocr_image_bytes, buffer.getvalue())
        if not text:
            continue
        chunks.append({
            "block_index": len(chunks),
            "content": f"[Frame {frame_index}] {text}",
            "table_involved": False,
            "table": None,
        })

    container.close()
    return chunks
