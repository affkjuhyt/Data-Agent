"""connector_type -> fetch() dispatch, mirroring the alias/dispatch pattern in
`platform_workers/workers/schema_crawler/metadata.py` (one dict, no if/elif
chain to extend when a V2 connector is added)."""
from __future__ import annotations

from typing import Any, Callable

from libs.ingestion.connectors import http_file, rest_api, rss, web_crawl
from libs.ingestion.connectors.base import FetchedArtifact

_CONNECTORS: dict[str, Callable[[dict], FetchedArtifact]] = {
    "http_file": http_file.fetch,
    "web_crawl": web_crawl.fetch,
    # web_crawl_headless is the SAME connector — `render` in the config
    # payload is what actually switches Playwright on, so both connector_type
    # values dispatch to the identical fetch() (web_crawl.fetch sets
    # `render=False` by default; a `web_crawl_headless` source's config is
    # expected to carry `"render": true`).
    "web_crawl_headless": web_crawl.fetch,
    "rss": rss.fetch,
    # rest_api and sdmx are the SAME connector — see rest_api.py's docstring:
    # neither parses the response, both just GET-and-store-raw.
    "rest_api": rest_api.fetch,
    "sdmx": rest_api.fetch,
}


def get_connector(connector_type: str) -> Callable[[dict], FetchedArtifact]:
    try:
        return _CONNECTORS[connector_type]
    except KeyError:
        raise ValueError(f"no connector registered for connector_type={connector_type!r}") from None


def fetch(connector_type: str, config: dict[str, Any]) -> FetchedArtifact:
    return get_connector(connector_type)(config)
