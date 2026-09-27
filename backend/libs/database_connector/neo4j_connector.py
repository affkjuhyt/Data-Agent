import asyncio

from neo4j import AsyncGraphDatabase
from neo4j._async.driver import AsyncDriver
import logging
from libs.basemodel.services_databaseconnector.shared_model import RetryConfig, HealthCheckLoopConfig, PoolConfig

log = logging.getLogger(__name__)


class Neo4jClient:
    __slots__ = ("_client", "_connection_wait", "_healthy", "_connected", "_timeout",
                 "_timeout_incremental", "_url", "_pool")

    def __init__(self, pool: PoolConfig | None = None) -> None:
        self._pool: PoolConfig = pool or PoolConfig.from_env("NEO4J")
        self._client: AsyncDriver | None = None
        self._timeout: int = 10
        self._timeout_incremental: int = 1
        self._connection_wait: int = 5
        self._healthy: bool = False
        self._connected: bool = False
        self._url: str | None = None

    def set_url(self, url: str) -> None:
        self._url = url

    async def _check_health(self, config: HealthCheckLoopConfig) -> None:
        assert self._client is not None  # health loop runs only after open()
        await asyncio.wait_for(
            self._client.verify_connectivity(),
            timeout=config.timeout_for_health_check,
        )

    async def _reconnect(self) -> None:
        await self.close()
        try:
            self._create_connection()
        except Exception as e:
            log.warning(f"Neo4j reconnect failed: {e}")

    async def health_check_loop(self, config: HealthCheckLoopConfig) -> None:
        log.info("Neo4j health check loop started")
        while True:
            try:
                await self._check_health(config)
                self._healthy = True
            except Exception as e:
                self._healthy = False
                log.warning(f"Neo4j unhealthy: {e} — attempting reconnect")
                await self._reconnect()
            await asyncio.sleep(config.interval)

    async def open(self, retry: RetryConfig) -> AsyncDriver:
        if self._client is not None:
            return self._client
        log.info("Neo4j Connection established")
        self._create_connection()
        assert self._client is not None  # _create_connection sets self._client
        for _ in range(retry.count):
            try:
                await asyncio.wait_for(
                    self._client.verify_connectivity(),
                    timeout=self._timeout + min(self._timeout, _ * self._timeout_incremental)
                )

                log.info("Neo4j connection successes")
                self._connected = True
                self._healthy = True
                return self.get_client()
            except asyncio.TimeoutError as e:
                log.info(f"Attempt #{_}/{retry.count}: Fail To Connect To Neo4j, retry after: {self._connection_wait}. Error: {e}")
                await asyncio.sleep(self._connection_wait)

        log.info(f"Failed to connect to Neo4j after {retry.count} attempts")
        raise ConnectionError("Neo4j connection failed")

    async def close(self) -> None:
        if self._client is None:
            return
        try:
            await asyncio.wait_for(
                self._client.close(),
                timeout=self._timeout,
            )
        except Exception as e:
            log.info(f"Neo4j close error (ignored): {e}")
        finally:
            self._client = None
            self._connected = False
            self._healthy = False

    def is_healthy(self) -> bool:
        if self._client is None:
            raise ConnectionError("Connection must be first established")
        return self._healthy

    def is_connected(self) -> bool:
        if self._client is None:
            raise ConnectionError("Connection must be first established")
        return self._connected

    def get_client(self) -> AsyncDriver:
        if self._client is None:
            raise ConnectionError("Connection must be first established")
        return self._client

    def _create_connection(self) -> AsyncDriver:
        if self._url is None:
            raise ValueError("neo4j url must be set")
        self._client = AsyncGraphDatabase.driver(
            uri=self._url,
            max_connection_pool_size=self._pool.max_connections,
        )
        return self._client
