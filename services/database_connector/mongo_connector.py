import asyncio
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from services.log_set_up import create_logger
from basemodel.services_databaseconnector.shared_model import RetryConfig, HealthCheckLoopConfig

log = create_logger("services.mongo", "service_mongo")


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

    # ── Canvas methods (flow-builder) ─────────────────────────────────────────

    async def save_canvas(self, workflow_version_id: str, nodes: list, edges: list) -> None:
        db = self.get_client()
        await db.flow_nodes.delete_many({"workflow_version_id": workflow_version_id})
        await db.flow_edges.delete_many({"workflow_version_id": workflow_version_id})
        if nodes:
            await db.flow_nodes.insert_many([
                {**n, "workflow_version_id": workflow_version_id} for n in nodes
            ])
        if edges:
            await db.flow_edges.insert_many([
                {**e, "workflow_version_id": workflow_version_id} for e in edges
            ])

    async def load_canvas(self, workflow_version_id: str) -> dict:
        db = self.get_client()
        nodes = await db.flow_nodes.find(
            {"workflow_version_id": workflow_version_id}, {"_id": 0}
        ).to_list(None)
        edges = await db.flow_edges.find(
            {"workflow_version_id": workflow_version_id}, {"_id": 0}
        ).to_list(None)
        return {"nodes": nodes, "edges": edges}

    # ── Flow nodes method (workflow-runtime) ──────────────────────────────────

    async def load_flow_nodes(self, workflow_version_id: str) -> list[dict]:
        if not self.is_connected() or not workflow_version_id:
            return []
        try:
            db = self.get_client()
            nodes = await db.flow_nodes.find(
                {"workflow_version_id": workflow_version_id}, {"_id": 0}
            ).to_list(None)
            return nodes
        except Exception as e:
            log.warning("mongo load_flow_nodes failed: %s", e)
            return []


client = MongoClient()
