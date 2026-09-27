"""Connector protocol every `libs/ingestion/connectors/*` module implements."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class FetchedArtifact:
    """What a connector hands back — enough for the caller to land it in
    Bronze (either as a MinIO blob via `upload_to_bronze`-equivalent, or as a
    metadata-only web row via `register_web_bronze`-equivalent) without the
    connector knowing anything about Postgres/MinIO."""
    content_bytes: bytes
    content_type: str
    source_url: str
    # extension WITHOUT a leading dot (e.g. "pdf", "html") — drives
    # SourceType routing (http_file -> doc-style bronze, web_crawl -> web-style).
    extension: str
    extra: dict[str, Any] = field(default_factory=dict)


class Connector(Protocol):
    def fetch(self, config: dict[str, Any]) -> FetchedArtifact:
        """`config` is a `KBSourceConfig.config` JSONB dict (url/cron/render/...)."""
        ...
