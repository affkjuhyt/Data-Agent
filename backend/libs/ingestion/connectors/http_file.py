"""`http_file` connector — a URL that already points at a downloadable file
(PDF/DOCX/XLSX/...), as opposed to an HTML page that needs scraping. This is
the same shape a manual Document Hub upload produces, just fetched by the
worker instead of picked from a human's filesystem."""
from __future__ import annotations

import httpx

from libs.ingestion import config as cfg
from libs.ingestion.connectors import tls
from libs.ingestion.connectors.base import FetchedArtifact
from libs.ingestion.connectors.extension import infer_extension

_USER_AGENT = "DataAgent-SourceWorker/1.0"


def fetch(config: dict) -> FetchedArtifact:
    url = config["url"]
    response = tls.get(
        url,
        follow_redirects=True,
        timeout=httpx.Timeout(cfg.WEB_FETCH_TIMEOUT),
        headers={"User-Agent": _USER_AGENT},
    )
    content = response.content

    if len(content) > cfg.MAX_FILE_SIZE_BYTES:
        raise ValueError(
            f"http_file fetch of {url} is {len(content)} bytes, "
            f"over the {cfg.MAX_FILE_SIZE_BYTES}-byte cap"
        )

    content_type = response.headers.get("content-type", "application/octet-stream").split(";")[0]
    extension = infer_extension(content_type, url, prefer_url=True, content=content)
    return FetchedArtifact(
        content_bytes=content,
        content_type=content_type,
        source_url=url,
        extension=extension,
    )
