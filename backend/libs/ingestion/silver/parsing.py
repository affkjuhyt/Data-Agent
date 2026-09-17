"""Source parsing helpers for the silver pipeline."""
from __future__ import annotations

import asyncio
import io
from typing import Any

from libs.ingestion.connectors import web_crawl as web_crawl_connector
from libs.ingestion.silver.shared import _vlm_describe
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
    content_type = f"image/{extension.lstrip('.')}"
    description = await _vlm_describe(file_bytes, content_type)
    return [{"block_index": 0, "content": description, "table_involved": False, "table": None}]


async def _parse_video(file_bytes: bytes) -> list[dict[str, Any]]:
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
        description = await _vlm_describe(buffer.getvalue(), "image/jpeg")
        chunks.append({
            "block_index": len(chunks),
            "content": f"[Frame {frame_index}] {description}",
            "table_involved": False,
            "table": None,
        })

    container.close()
    return chunks
