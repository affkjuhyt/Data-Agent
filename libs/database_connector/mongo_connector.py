import asyncio
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

import logging
from libs.basemodel.services_databaseconnector.shared_model import RetryConfig, HealthCheckLoopConfig

log = logging.getLogger(__name__)


class MongoClient:
    __slots__ = ("_client", "_db", "_db_name", "_connection_wait", "_healthy", "_connected", "_timeout", "_url")

    def __init__(self):
        self._client: AsyncIOMotorClient | None = None
        self._db: AsyncIOMotorDatabase | None = None
        self._db_name: str = "dataagent"
        self._timeout: int = 10
        self._connection_wait: int = 5
        self._healthy: bool = False
        self._connected: bool = False
        self._url: str | None = None

    def set_url(self, url: str) -> None:
        self._url = url
        self._db_name = url.split("/")[-1] or "dataagent"

    async def open(self, retry: RetryConfig | None = RetryConfig()) -> AsyncIOMotorDatabase:
        if self._url is None:
            raise RuntimeError("Mongo URL is not yet set")

        for attempt in range(retry.count):
            try:
                self._client = AsyncIOMotorClient(self._url, serverSelectionTimeoutMS=self._timeout * 1000)
                self._db = self._client[self._db_name]
                await asyncio.wait_for(
                    self._client.admin.command("ping"),
                    timeout=self._timeout,
                )
                self._connected = True
                self._healthy = True
                log.info("Mongo connection established db=%s", self._db_name)
                return self.get_client()
            except Exception as e:
                log.info("Attempt #%d/%d: Mongo connection failed — %s", attempt + 1, retry.count, e)
                if self._client:
                    self._client.close()
                    self._client = None
                    self._db = None
            await asyncio.sleep(self._connection_wait)

        raise ConnectionError(f"Could not connect to Mongo after {retry.count} attempts")

    async def close(self) -> None:
        if self._client:
            try:
                self._client.close()
            except Exception as e:
                log.info("Mongo close error (ignored): %s", e)
            finally:
                self._client = None
                self._db = None
        self._connected = False
        self._healthy = False

    async def health_check_loop(self, config: HealthCheckLoopConfig | None = HealthCheckLoopConfig()) -> None:
        log.info("Mongo health check loop started")
        while True:
            try:
                await self._check_health(config)
                self._healthy = True
            except Exception as e:
                self._healthy = False
                log.warning("Mongo unhealthy: %s — attempting reconnect", e)
                await self._reconnect()
            await asyncio.sleep(config.interval)

    def is_healthy(self) -> bool:
        return self._healthy

    def is_connected(self) -> bool:
        return self._connected

    def get_client(self) -> AsyncIOMotorDatabase:
        if self._db is None:
            raise RuntimeError("MongoClient is not open — call open() first")
        return self._db

    # ── operations (query logic lives in the connector) ─────────────────
    async def create_index(self, collection: str, keys, **kwargs) -> None:
        await self.get_client()[collection].create_index(keys, **kwargs)

    async def upsert(self, collection: str, filter: dict, document: dict) -> None:
        """Replace (or insert) the single document matching ``filter``."""
        await self.get_client()[collection].replace_one(filter, document, upsert=True)

    async def set_fields(self, collection: str, filter: dict, fields: dict) -> int:
        result = await self.get_client()[collection].update_one(filter, {"$set": fields})
        return result.modified_count

    async def find_one(self, collection: str, filter: dict, projection: dict | None = None) -> dict | None:
        return await self.get_client()[collection].find_one(filter, projection or {"_id": 0})

    async def find_many(self, collection: str, filter: dict, projection: dict | None = None,
                        limit: int = 0) -> list[dict]:
        cursor = self.get_client()[collection].find(filter, projection or {"_id": 0})
        return await cursor.to_list(length=limit or None)

    async def delete_one(self, collection: str, filter: dict) -> int:
        result = await self.get_client()[collection].delete_one(filter)
        return result.deleted_count

    async def delete_many(self, collection: str, filter: dict) -> int:
        result = await self.get_client()[collection].delete_many(filter)
        return result.deleted_count

    async def _check_health(self, config: HealthCheckLoopConfig) -> None:
        await asyncio.wait_for(
            self._client.admin.command("ping"),
            timeout=config.timeout_for_health_check,
        )

    async def _reconnect(self) -> None:
        await self.close()
        try:
            await self.open(RetryConfig(count=1))
        except ConnectionError as e:
            log.warning("Mongo reconnect failed: %s", e)


client = MongoClient()
