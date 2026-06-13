import re
import uuid
import asyncio

from neo4j import AsyncGraphDatabase
from neo4j._async.driver import AsyncDriver
from services.log_set_up import create_logger
from basemodel.services_databaseconnector.shared_model import ResponseModel, RetryConfig, HealthCheckLoopConfig
from basemodel.services_databaseconnector.neo4j_model import (
    AddNodeRequest, AddRelationshipRequest, GraphExpandRequest,
    RelationshipDirection
)

log = create_logger("services.neo4j", "service_neo4j")


class Neo4jClient:
    __slots__ = ("_client", "_connection_wait", "_healthy", "_connected", "_timeout", "_timeout_incremental", "_url")

    def __init__(self):
        self._client: AsyncDriver | None = None
        self._timeout: int = 10
        self._timeout_incremental: int = 1
        self._connection_wait: int = 5
        self._healthy: bool = False
        self._connected: bool = False
        self._url: str | None = None

    def set_url(self, url: str):
        self._url = url

    async def _check_health(self, config: HealthCheckLoopConfig) -> None:
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

    async def health_check_loop(self, config: HealthCheckLoopConfig):
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
        for _ in range(retry.count):
            try:
                await asyncio.wait_for(
                    self._client.verify_connectivity(),
                    timeout=self._timeout + min(self._timeout, _ * self._timeout_incremental)
                )

                log.info("Neo4j connection successes")
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
        self._client = AsyncGraphDatabase.driver(uri=self._url)
        return self._client


def _validate_node_name(value: str) -> str:
    if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', value):
        raise ValueError(f"Invalid identifier '{value}': only letters, digits and underscores allowed")
    return value


async def add_node(driver: AsyncDriver, item: AddNodeRequest) -> ResponseModel:
    try:
        label = _validate_node_name(item.node.label)
    except ValueError as e:
        return ResponseModel(code=400, error=str(e))

    node_id = str(uuid.uuid4())
    props = {**item.node.properties.model_dump(mode="json"), "id": node_id}
    if item.node.embedding:
        props["embedding"] = item.node.embedding

    async with driver.session() as session:
        result = await session.run(
            f"CREATE (n:`{label}` $props) RETURN n.id AS id",
            props=props,
        )
        record = await result.single()
    return ResponseModel(code=200, data={"id": record["id"]})


async def add_relationship(driver: AsyncDriver, item: AddRelationshipRequest) -> ResponseModel:
    props = {**(item.relationship.properties or {}), "type": item.relationship.type}
    direction = item.relationship.direction

    if direction == RelationshipDirection.OUTGOING.value:
        pattern = "(a)-[r:RELATED_TO $props]->(b)"
    elif direction == RelationshipDirection.INCOMING.value:
        pattern = "(a)<-[r:RELATED_TO $props]-(b)"
    else:
        pattern = "(a)-[r:RELATED_TO $props]-(b)"

    async with driver.session() as session:
        result = await session.run(
            f"""
            MATCH (a {{id: $from_id}}), (b {{id: $to_id}})
            CREATE {pattern}
            RETURN r.type AS type
            """,
            from_id=item.from_node_id,
            to_id=item.to_node_id,
            props=props,
        )
        record = await result.single()

    if record is None:
        return ResponseModel(code=404, error="One or both nodes not found")
    return ResponseModel(code=200, data={"type": record["type"]})


_WRITE_KEYWORDS = re.compile(
    r'\b(DELETE|DETACH|CREATE|MERGE|SET|REMOVE|DROP|LOAD\s+CSV|CALL\s+apoc\.)\b',
    re.IGNORECASE,
)


async def run_query(driver: AsyncDriver, cypher: str, params: dict | None = None) -> ResponseModel:
    normalized = cypher.strip()
    if not normalized.upper().startswith("MATCH"):
        return ResponseModel(code=400, error="Only MATCH queries are permitted")
    match = _WRITE_KEYWORDS.search(normalized)
    if match:
        return ResponseModel(code=400, error=f"Forbidden keyword in query: {match.group().upper()}")
    async with driver.session() as session:
        result = await session.run(normalized, **(params or {}))
        data = await result.data()
    return ResponseModel(code=200, data=data)


async def graph_expand(driver: AsyncDriver, item: GraphExpandRequest) -> ResponseModel:
    similarity_expr = (
        """
        CASE WHEN neighbour.embedding IS NOT NULL
             THEN vector.similarity.cosine(neighbour.embedding, $query_vector)
             ELSE 0.0 END
        """
        if item.query_vector
        else "0.0"
    )

    query = f"""
        MATCH (start {{id: $start_id}})-[*1..{item.max_hops}]-(neighbour)
        WHERE neighbour.id <> $start_id
        WITH DISTINCT neighbour, {similarity_expr} AS similarity
        ORDER BY similarity DESC
        LIMIT $max_neighbours
        RETURN neighbour{{.*}} AS node, similarity
    """

    async with driver.session() as session:
        result = await session.run(
            query,
            start_id=item.start_node_id,
            max_neighbours=item.max_neighbours,
            query_vector=item.query_vector or [],
        )
        records = await result.data()

    return ResponseModel(code=200, data=records)
