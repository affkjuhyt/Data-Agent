import asyncio

import httpx

from basemodel.services_databaseconnector.shared_model import HealthCheckLoopConfig, RetryConfig
from basemodel.services_docling.docling_model import DoclingResult, ParsedChunk, ParsedTable
from services.log_set_up import create_logger

log = create_logger("services.docling", "service_docling_connector")


class DoclingServiceClient:
    # Follows DatabaseConnector protocol in server/basemodel/protocol_model.py
    __slots__ = ("_client", "_url", "_healthy", "_connected", "_timeout", "_connection_wait")

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._url: str | None = None
        self._healthy: bool = False
        self._connected: bool = False
        self._timeout: int = 10
        self._connection_wait: int = 5

    def set_url(self, url: str) -> None:
        self._url = url

    def _create_connection(self) -> httpx.AsyncClient:
        if self._url is None:
            raise ValueError("docling-service url must be set")
        self._client = httpx.AsyncClient(
            base_url=self._url,
            # Docling can take a long time on large PDFs — generous read timeout
            timeout=httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=5.0),
        )
        self._connected = True
        return self._client

    async def open(self, retry: RetryConfig) -> None:
        if self._client is not None:
            return
        self._create_connection()
        for attempt in range(retry.count):
            try:
                resp = await asyncio.wait_for(
                    self._client.get("/health"),
                    timeout=float(self._timeout),
                )
                resp.raise_for_status()
                self._healthy = True
                log.info("docling-service connection established")
                return
            except Exception as e:
                log.info(
                    f"Attempt #{attempt}/{retry.count}: Failed to connect to docling-service, "
                    f"retry after {self._connection_wait}s. Error: {e}"
                )
                await asyncio.sleep(self._connection_wait)
        raise ConnectionError("docling-service connection failed after all retries")

    async def close(self) -> None:
        if self._client is None:
            return
        try:
            await asyncio.wait_for(self._client.aclose(), timeout=float(self._timeout))
        except Exception as e:
            log.info(f"docling-service close error (ignored): {e}")
        finally:
            self._client = None
            self._connected = False
            self._healthy = False

    async def _reconnect(self) -> None:
        await self.close()
        try:
            self._create_connection()
        except Exception as e:
            log.warning(f"docling-service reconnect failed: {e}")

    async def health_check_loop(self, config: HealthCheckLoopConfig) -> None:
        log.info("docling-service health check loop started")
        while True:
            await asyncio.sleep(config.interval)
            try:
                await asyncio.wait_for(
                    self._client.get("/health"),
                    timeout=float(config.timeout_for_health_check),
                )
                self._healthy = True
            except Exception as e:
                self._healthy = False
                log.warning(f"docling-service unhealthy: {e} — attempting reconnect")
                await self._reconnect()

    def is_healthy(self) -> bool:
        if self._client is None:
            raise ConnectionError("Connection must be first established")
        return self._healthy

    def is_connected(self) -> bool:
        if self._client is None:
            raise ConnectionError("Connection must be first established")
        return self._connected

    def get_client(self) -> "DoclingServiceClient":
        if self._client is None:
            raise ConnectionError("Connection must be first established")
        return self

    # ── Operations ──────────���─────────────────────────────────────────────────

    async def parse(
        self,
        bucket: str,
        object_key: str,
        extension: str,
        max_chunk_chars: int = 1500,
    ) -> DoclingResult:
        resp = await self._client.post(
            "/parse",
            json={
                "bucket": bucket,
                "object_key": object_key,
                "extension": extension,
                "max_chunk_chars": max_chunk_chars,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        chunks = [
            ParsedChunk(
                block_index=c["block_index"],
                content=c["content"],
                table_involved=c["table_involved"],
                table=ParsedTable(**c["table"]) if c.get("table") else None,
            )
            for c in data["chunks"]
        ]
        return DoclingResult(has_figures=data["has_figures"], chunks=chunks)
