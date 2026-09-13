import asyncio
import logging
from typing import cast

import redis.asyncio as aioredis

from libs.basemodel.services_databaseconnector.shared_model import HealthCheckLoopConfig, RetryConfig, PoolConfig

log = logging.getLogger(__name__)


class RedisClient:
    __slots__ = ("_client", "_healthy", "_connected", "_url", "_pool")

    def __init__(self, pool: PoolConfig | None = None) -> None:
        self._pool: PoolConfig = pool or PoolConfig.from_env("REDIS")
        self._client: aioredis.Redis | None = None
        self._healthy: bool = False
        self._connected: bool = False
        self._url: str | None = None

    def set_url(self, url: str) -> None:
        self._url = url

    async def open(self, retry: RetryConfig = RetryConfig()) -> None:
        if self._url is None:
            raise RuntimeError("Redis URL is not yet set")

        for attempt in range(retry.count):
            try:
                client = aioredis.from_url(
                    self._url, decode_responses=True, max_connections=self._pool.max_connections,
                )
                await client.ping()
                self._client = client
                self._connected = True
                self._healthy = True
                log.info("Redis connection established")
                return
            except Exception as e:
                log.info(f"Attempt #{attempt + 1}/{retry.count}: Redis connection failed — {e}")
                self._client = None
            await asyncio.sleep(1)
        raise ConnectionError(f"Could not connect to Redis after {retry.count} attempts")

    async def close(self) -> None:
        if self._client:
            try:
                await self._client.aclose()
            except Exception as e:
                log.info(f"Redis client close error (ignored): {e}")
            finally:
                self._client = None
        self._connected = False
        self._healthy = False

    async def health_check_loop(self, config: HealthCheckLoopConfig = HealthCheckLoopConfig()) -> None:
        log.info("Redis health check loop started")
        while True:
            try:
                if self._client is None:
                    raise ConnectionError("no client")
                await asyncio.wait_for(self._client.ping(), timeout=config.timeout_for_health_check)
                self._healthy = True
            except Exception as e:
                self._healthy = False
                log.warning(f"Redis unhealthy: {e} — attempting reconnect")
                await self._reconnect()
            await asyncio.sleep(config.interval)

    async def _reconnect(self) -> None:
        await self.close()
        try:
            await self.open(RetryConfig(count=1))
        except ConnectionError as e:
            log.warning(f"Redis reconnect failed: {e}")

    def is_healthy(self) -> bool:
        return self._healthy

    # Operations — deliberately narrow (get/set/delete), matching what the
    # orchestrator's session cache needs. Extend when a new caller shows up.
    async def get(self, key: str) -> str | None:
        if self._client is None:
            return None
        return cast("str | None", await self._client.get(key))

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        if self._client is None:
            return
        await self._client.set(key, value, ex=ttl)

    async def delete(self, key: str) -> None:
        if self._client is None:
            return
        await self._client.delete(key)

    async def incr(self, key: str) -> int:
        """Atomic INCR (creates the key at 1 if absent). Used for version-keyed
        cache invalidation. Returns 0 when Redis is not connected."""
        if self._client is None:
            return 0
        return cast(int, await self._client.incr(key))
