"""Elasticsearch connector — follows the same shape as the other connectors:
a stateful client (set_url/open/close/health_check_loop) plus operation methods
that own ALL query logic. Application services call these methods and never build
raw ES queries themselves.

URL format: http://host:9200  (optionally with basic-auth user:pass in the URL).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from basemodel.services_databaseconnector.shared_model import HealthCheckLoopConfig, RetryConfig

log = logging.getLogger(__name__)


class ElasticsearchClient:
    __slots__ = ("_client", "_url", "_timeout", "_connection_wait", "_healthy", "_connected")

    def __init__(self) -> None:
        self._client = None
        self._url: Optional[str] = None
        self._timeout: int = 10
        self._connection_wait: int = 5
        self._healthy: bool = False
        self._connected: bool = False

    # ── lifecycle ───────────────────────────────────────────────────────
    def set_url(self, url: str) -> None:
        self._url = url

    async def open(self, retry: RetryConfig | None = RetryConfig()) -> "ElasticsearchClient":
        if self._url is None:
            raise RuntimeError("Elasticsearch URL is not set — call set_url() first")
        from elasticsearch import AsyncElasticsearch
        for attempt in range(retry.count):
            try:
                self._client = AsyncElasticsearch(self._url, request_timeout=self._timeout)
                await self._client.info()
                self._connected = True
                self._healthy = True
                log.info("Elasticsearch connection established url=%s", self._url)
                return self
            except Exception as e:  # noqa: BLE001
                log.info("Attempt #%d/%d: Elasticsearch connection failed — %s", attempt + 1, retry.count, e)
                if self._client is not None:
                    await self._client.close()
                    self._client = None
                await asyncio.sleep(self._connection_wait)
        raise ConnectionError(f"Could not connect to Elasticsearch after {retry.count} attempts")

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            finally:
                self._client = None
        self._connected = False
        self._healthy = False

    async def health_check_loop(self, config: HealthCheckLoopConfig | None = HealthCheckLoopConfig()) -> None:
        log.info("Elasticsearch health check loop started")
        while True:
            try:
                await asyncio.wait_for(self._client.ping(), timeout=config.timeout_for_health_check)
                self._healthy = True
            except Exception as e:  # noqa: BLE001
                self._healthy = False
                log.warning("Elasticsearch unhealthy: %s — attempting reconnect", e)
                await self._reconnect()
            await asyncio.sleep(config.interval)

    async def _reconnect(self) -> None:
        await self.close()
        try:
            await self.open(RetryConfig(count=1))
        except ConnectionError as e:
            log.warning("Elasticsearch reconnect failed: %s", e)

    def is_healthy(self) -> bool:
        return self._healthy

    def is_connected(self) -> bool:
        return self._connected

    def _db(self):
        if self._client is None:
            raise RuntimeError("ElasticsearchClient is not open — call open() first")
        return self._client

    # ── operations (all query logic lives here) ─────────────────────────
    async def ensure_index(self, index: str, properties: dict[str, dict]) -> None:
        """Create the index with the given field mappings if it does not exist.

        properties: ES mapping ``properties`` block, e.g.
            {"user_input": {"type": "text"}, "user_id": {"type": "keyword"}}
        """
        es = self._db()
        if not await es.indices.exists(index=index):
            await es.indices.create(index=index, mappings={"properties": properties})

    async def index_document(self, index: str, doc_id: str, document: dict[str, Any]) -> None:
        """Upsert a document by id (refresh so it is immediately searchable)."""
        await self._db().index(index=index, id=doc_id, document=document, refresh=True)

    async def search_top_match(
        self, index: str, text_field: str, query_text: str,
        *, min_score: float = 0.0, filters: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        """Full-text match on ``text_field``; return the top hit's ``_source`` when
        its score >= ``min_score``, else None. ``filters`` adds keyword term filters
        (ANDed), e.g. {"user_id": "u1"}.
        """
        must = [{"match": {text_field: query_text}}]
        filt = [{"term": {k: v}} for k, v in (filters or {}).items()]
        query = {"bool": {"must": must, "filter": filt}} if filt else must[0]
        res = await self._db().search(index=index, size=1, query=query)
        hits = res.get("hits", {}).get("hits", [])
        if hits and hits[0]["_score"] >= min_score:
            return hits[0]["_source"]
        return None

    async def delete_document(self, index: str, doc_id: str) -> bool:
        """Delete a document by id. Returns False if it was already absent."""
        from elasticsearch import NotFoundError
        try:
            await self._db().delete(index=index, id=doc_id, refresh=True)
            return True
        except NotFoundError:
            return False

    async def delete_by_term(self, index: str, field: str, value: Any) -> int:
        """Delete every document where field == value. Returns count deleted."""
        res = await self._db().delete_by_query(
            index=index, query={"term": {field: value}}, refresh=True
        )
        return int(res.get("deleted", 0))


client = ElasticsearchClient()
