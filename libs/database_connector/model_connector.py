import asyncio

import httpx

from libs.basemodel.services_databaseconnector.shared_model import HealthCheckLoopConfig, RetryConfig
from libs.shared.logger import create_logger

log = create_logger("services.model", "service_model")


class ModelServiceClient:
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
            raise ValueError("model-service url must be set")
        self._client = httpx.AsyncClient(
            base_url=self._url,
            timeout=httpx.Timeout(connect=5.0, read=60.0, write=5.0, pool=5.0),
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
                log.info("model-service connection established")
                return
            except Exception as e:
                log.info(
                    f"Attempt #{attempt}/{retry.count}: Failed to connect to model-service, "
                    f"retry after {self._connection_wait}s. Error: {e}"
                )
                await asyncio.sleep(self._connection_wait)
        raise ConnectionError("model-service connection failed after all retries")

    async def close(self) -> None:
        if self._client is None:
            return
        try:
            await asyncio.wait_for(self._client.aclose(), timeout=float(self._timeout))
        except Exception as e:
            log.info(f"model-service close error (ignored): {e}")
        finally:
            self._client = None
            self._connected = False
            self._healthy = False

    async def _reconnect(self) -> None:
        await self.close()
        try:
            self._create_connection()
        except Exception as e:
            log.warning(f"model-service reconnect failed: {e}")

    async def health_check_loop(self, config: HealthCheckLoopConfig) -> None:
        log.info("model-service health check loop started")
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
                log.warning(f"model-service unhealthy: {e} — attempting reconnect")
                await self._reconnect()

    def is_healthy(self) -> bool:
        if self._client is None:
            raise ConnectionError("Connection must be first established")
        return self._healthy

    def is_connected(self) -> bool:
        if self._client is None:
            raise ConnectionError("Connection must be first established")
        return self._connected

    def get_client(self) -> "ModelServiceClient":
        if self._client is None:
            raise ConnectionError("Connection must be first established")
        return self

    # ── Operations ────────────────────────────────────────────────────────────

    async def embed(self, texts: list[str], model_id: str | None = None) -> list[list[float]]:
        if not texts:
            return []
        payload: dict = {"texts": texts}
        if model_id:
            payload["model_id"] = model_id
        resp = await self._client.post("/embed", json=payload)
        resp.raise_for_status()
        return resp.json()["embeddings"]

    async def rerank(self, query: str, documents: list[str], top_n: int) -> list[dict]:
        if not documents:
            return []
        resp = await self._client.post(
            "/rerank",
            json={"query": query, "documents": documents, "top_n": top_n},
        )
        resp.raise_for_status()
        return resp.json().get("results", [])

    async def generate(
        self,
        prompt: str = "",
        *,
        system_prompt: str = "",
        messages: list[dict] | None = None,
        json_schema: dict | None = None,
        json_mode: bool = True,
        max_tokens: int = 512,
        temperature: float = 0.1,
    ) -> str:
        """LLM text generation via the model-service gateway. The caller provides
        only the input (prompt/messages, optional json_schema); model-service routes
        to whichever LLM backend is configured (local or Colab). Returns the
        assistant content string."""
        payload: dict = {
            "system_prompt": system_prompt, "json_mode": json_mode,
            "max_tokens": max_tokens, "temperature": temperature,
        }
        if messages is not None:
            payload["messages"] = messages
        else:
            payload["prompt"] = prompt
        if json_schema is not None:
            payload["json_schema"] = json_schema
        # LLM calls run longer than the default 60s read timeout.
        resp = await self._client.post(
            "/generate", json=payload,
            timeout=httpx.Timeout(connect=5.0, read=180.0, write=5.0, pool=5.0),
        )
        resp.raise_for_status()
        return resp.json().get("content", "")


# module-level singleton (same pattern as the mongo/postgres/elasticsearch connectors)
client = ModelServiceClient()
