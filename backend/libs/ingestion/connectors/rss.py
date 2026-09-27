"""`rss` connector — stores a feed's raw bytes as-is in Bronze (like
`http_file`), not a per-item fan-out into separate KBData rows. `feedparser`
is used only to compute `extra["item_count"]` for the run's quality metrics;
it never touches what actually gets stored."""
from __future__ import annotations

import httpx

from libs.ingestion import config as cfg
from libs.ingestion.connectors import tls
from libs.ingestion.connectors.base import FetchedArtifact

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
        raise ValueError(f"rss fetch of {url} is {len(content)} bytes, over the {cfg.MAX_FILE_SIZE_BYTES}-byte cap")

    item_count = None
    try:
        import feedparser

        item_count = len(feedparser.parse(content).entries)
    except Exception:
        pass  # quality metric only — a malformed/unparsable feed still gets stored raw

    content_type = response.headers.get("content-type", "application/rss+xml").split(";")[0]
    return FetchedArtifact(
        content_bytes=content,
        content_type=content_type,
        source_url=url,
        extension="xml",
        extra={"item_count": item_count},
    )
