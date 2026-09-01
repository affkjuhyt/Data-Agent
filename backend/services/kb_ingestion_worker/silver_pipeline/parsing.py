"""Source parsing helpers for the silver pipeline."""
from __future__ import annotations

import asyncio
import io
from typing import Any, cast

from services.kb_ingestion_worker.silver_pipeline.shared import _vlm_describe
from services.kb_ingestion_worker.utils import config as cfg

_WEB_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


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


async def _fetch_url_static(url: str) -> str:
    import httpx

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(cfg.WEB_FETCH_TIMEOUT),
        headers={"User-Agent": _WEB_USER_AGENT, "Accept-Language": "vi,en;q=0.8"},
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        return cast(str, response.text)


async def _fetch_url_rendered(url: str) -> str:
    from playwright.async_api import TimeoutError as PWTimeout
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        try:
            page = await browser.new_page(user_agent=_WEB_USER_AGENT, locale="vi-VN")
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=int(cfg.WEB_FETCH_TIMEOUT * 1000),
            )
            try:
                await page.wait_for_load_state(
                    "networkidle",
                    timeout=int(cfg.WEB_RENDER_WAIT * 1000),
                )
            except PWTimeout:
                pass
            return cast(str, await page.content())
        finally:
            await browser.close()


async def _fetch_url(url: str) -> str:
    if cfg.WEB_USE_PLAYWRIGHT:
        try:
            return await _fetch_url_rendered(url)
        except Exception:
            pass
    return await _fetch_url_static(url)


def _extract_main_content(html: str, url: str) -> tuple[str, str]:
    try:
        import trafilatura
    except ImportError:
        return html, "html"

    for output_format in ("markdown", "txt"):
        try:
            extracted = trafilatura.extract(
                html,
                url=url,
                output_format=output_format,
                include_tables=True,
                include_comments=False,
                include_images=False,
                favor_precision=True,
            )
        except Exception:
            extracted = None
        if extracted and extracted.strip():
            return extracted, "md"
    return html, "html"


async def _parse_web(docling: Any, url: str) -> list[dict[str, Any]]:
    if not url:
        raise ValueError("Web document has no URL in its metadata")
    html = await _fetch_url(url)
    loop = asyncio.get_event_loop()
    content, extension = await loop.run_in_executor(None, _extract_main_content, html, url)
    result = await docling.parse_bytes(content.encode("utf-8"), extension)
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
