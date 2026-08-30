from qdrant_client import models
from qdrant_client.async_qdrant_client import AsyncQdrantClient
from typing import Any, List
import asyncio
import logging
from libs.basemodel.services_databaseconnector.qdrant_model import (
    # Enums
    DistanceMetric, MatchType,

    # Base Struct
    PointPayload,
    MatchingPayload,
    PointData,
    PaginationConfig,

    # Request Model
    CreateCollectionRequest,
    DeleteCollectionRequest,
    AddPointsRequest,
    DeletePointsRequest,
    QueryByPayloadRequest,
    SearchRequest,
    SoftDeletePointsRequest,
)

from libs.basemodel.services_databaseconnector.shared_model import (
    ResponseModel,
    RetryConfig,
    HealthCheckLoopConfig,
)

# Logging Initiate
log = logging.getLogger(__name__)

# Utilities
def _build_qdrant_filters(
        tenant_id: str | List[str],
        data: List[MatchingPayload],
        match_type: MatchType | None = None,
        pagination_config: PaginationConfig | None = None,
        knowledge_base: str | None = None) -> models.Filter | None:


    def build_matching_field() -> list[models.FieldCondition]:
        fields: list[models.FieldCondition] = []
        for matching_entity in data:
            for value in matching_entity.values:
                fields.append(
                    models.FieldCondition(
                        key=matching_entity.field,
                        match=models.MatchValue(value=value),
                    )
                )
        return fields

    def build_matching_tenant() -> list[models.FieldCondition]:
        must_ = []
        if isinstance(tenant_id, str):
            must_ = [
                models.FieldCondition(
                    key="tenant_id",
                    match=models.MatchValue(value=tenant_id)
                )
            ]
        else:
            for tn_id in tenant_id:
                must_.append(
                    models.FieldCondition(
                        key="tenant_id",
                        match=models.MatchValue(value=tn_id)
                    )
                )

        return must_

    def build_paginating() -> list[models.FieldCondition]:
        # Only called when pagination_config and pagination_cursor are set (see caller below).
        assert pagination_config is not None
        paging_filter = models.FieldCondition(
            key=pagination_config.pagination_on,
            range=models.Range(gt=pagination_config.pagination_cursor),
        )
        return [paging_filter]


    matching_require = build_matching_tenant()
    # Always exclude soft-deleted points from every search and scroll
    matching_require.append(
        models.FieldCondition(key="is_deleted", match=models.MatchValue(value=False))
    )
    # Scope to a single knowledge base (the "kind of knowledge") when provided.
    if knowledge_base:
        matching_require.append(
            models.FieldCondition(key="knowledge_base", match=models.MatchValue(value=knowledge_base))
        )
    matching_field = build_matching_field()
    if pagination_config is not None and pagination_config.pagination_cursor is not None:
        matching_require += build_paginating()


    if not matching_field:
        return models.Filter(must=matching_require)

    if match_type is MatchType.any:
        return models.Filter(
            must=matching_require,
            should=matching_field,
        )

    return models.Filter(
        must=matching_require + matching_field,
    )


def _build_batch_points(tenant_id: str, points: List[PointData]) -> list[models.PointStruct]:

    def add_tenant(payload: PointPayload) -> PointPayload:
        payload.tenant_id = tenant_id
        return payload

    return [
        models.PointStruct(
            id=p.payload.block_id,   # block_id is 1-to-1 with KBTextBlock in Postgres
            vector=p.vector,
            payload=add_tenant(p.payload).model_dump(mode="json"),
        )
        for p in points
    ]


# Qdrant Client
class QdrantClient:
    # Follow DatabaseConnector Protocol in server/basemodel/protocol_model.py
    __slots__ = ("_client","_retry" ,"_connection_wait", "_healthy", "_connected", "_timeout", "_timeout_incremental", "_url")
    def __init__(self) -> None:
        self._client: AsyncQdrantClient | None = None
        self._timeout:int = 10
        self._timeout_incremental:int = 1
        self._connection_wait:int = 5
        self._healthy:bool = False
        self._connected:bool = False
        self._url:str|None = None

    def set_url(self, url: str) -> None:
        self._url = url

    async def _check_health(self, config: HealthCheckLoopConfig) -> None:
        # _check_health is only invoked from health_check_loop after a
        # successful open(), so the client is guaranteed to be set.
        assert self._client is not None
        await asyncio.wait_for(
            self._client.get_collections(),
            timeout=config.timeout_for_health_check,
        )

    async def _reconnect(self) -> None:
        await self.close()
        try:
            self._create_connection()
        except Exception as e:
            log.warning(f"Qdrant reconnect failed: {e}")

    async def health_check_loop(self, config: HealthCheckLoopConfig) -> None:
        log.info("Qdrant health check loop started")
        while True:
            try:
                await self._check_health(config)
                self._healthy = True
            except Exception as e:
                self._healthy = False
                log.warning(f"Qdrant unhealthy: {e} — attempting reconnect")
                await self._reconnect()
            await asyncio.sleep(config.interval)

    async def open(self, retry: RetryConfig) -> AsyncQdrantClient:
        if self._client is not None:
            return self._client
        log.info("Qdrant Connection established")
        self._create_connection()
        for _ in range(retry.count):
            try:
                # _create_connection() above always sets self._client.
                assert self._client is not None
                await asyncio.wait_for(
                    self._client.get_collections(),
                    timeout = self._timeout + min(self._timeout, _ * self._timeout_incremental)
                )

                log.info("Qdrant connection successes")
                self._connected = True
                self._healthy = True
                return self.get_client()
            except asyncio.TimeoutError as e:
                log.info(f"Attempt #{_}/{retry.count}: Fail To Connect To Qdrant, retry after: {self._connection_wait}. Error: {e}")
                await asyncio.sleep(self._connection_wait)

        log.info(f"Failed to connect to Qdrant after {retry.count} attempts")
        raise ConnectionError(f"Qdrant connection failed")


    async def close(self) -> None:
        if self._client is None:
            return
        try:
            await asyncio.wait_for(
                self._client.close(),
                timeout=self._timeout,
            )
        except Exception as e:
            log.info(f"Qdrant close error (ignored): {e}")
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

    def get_client(self) -> AsyncQdrantClient:
        if self._client is None:
            raise ConnectionError("Connection must be first established")
        return self._client

    def connect(self) -> AsyncQdrantClient:
        return self._create_connection()

    def _create_connection(self) -> AsyncQdrantClient:
        if self._url is None:
            raise ValueError("qdrant url must be set")
        self._client = AsyncQdrantClient(
                    url = self._url,
                )
        return self._client

async def delete_collection(client: AsyncQdrantClient, item: DeleteCollectionRequest) -> ResponseModel[Any]:
    if not await client.collection_exists(item.collection_name):
        return ResponseModel(code=404, error="Collection does not exist")
    await client.delete_collection(item.collection_name)
    return ResponseModel(code=200)

async def create_collection(client: AsyncQdrantClient, item: CreateCollectionRequest) -> ResponseModel[Any]:
    if await client.collection_exists(item.name):
        return ResponseModel(code=409, error="Collection already exists")

    if item.distance_metric not in DistanceMetric:
        return ResponseModel(code=404, error="Distance metric does not exist")

    distance = models.Distance.COSINE
    if item.distance_metric == DistanceMetric.Euclidean.value:
        distance = models.Distance.EUCLID
    elif item.distance_metric == DistanceMetric.Dot.value:
        distance = models.Distance.DOT

    await client.create_collection(
        collection_name=item.name,
        vectors_config=models.VectorParams(
            size=item.vector_size,
            distance=distance,
        ),
    )
    return ResponseModel(code=200)


async def add_points(client: AsyncQdrantClient, item: AddPointsRequest) -> ResponseModel[Any]:
    if not await client.collection_exists(item.collection_name):
        return ResponseModel(code=404, error="Collection does not exist")

    if not item.points:
        return ResponseModel(code=400, error="Must provide points")

    await client.upsert(
        collection_name=item.collection_name,
        points=_build_batch_points(item.tenant_id,item.points),
    )
    return ResponseModel(code=200)


async def soft_delete_points(client: AsyncQdrantClient, item: SoftDeletePointsRequest) -> ResponseModel[Any]:
    if not await client.collection_exists(item.collection_name):
        return ResponseModel(code=404, error="Collection does not exist")

    if len(item.data_ids) == 0 and len(item.matching_payload) == 0 and len(item.block_ids) == 0:
        return ResponseModel(code=400, error="Must provide at least block_ids or matching_payload or data_ids")

    matching_field = item.matching_payload
    if len(item.data_ids) >= 0:
        matching_field = matching_field + [MatchingPayload(field="data_id", values=item.data_ids)]

    # Data_id contains multiple block_ids, filter with data_ids + field first then block_ids + field
    await client.set_payload(
        collection_name=item.collection_name,
        payload={
            "is_deleted": True
        },
        points=_build_qdrant_filters(
            tenant_id=item.tenant_id,
            data=matching_field,
            match_type=MatchType.must
        )
    )

    if len(item.block_ids) == 0:
        return ResponseModel(code=200)

    matching_field = item.matching_payload + [MatchingPayload(field="block_ids", values=item.block_ids)]
    await client.set_payload(
        collection_name=item.collection_name,
        payload={
            "is_deleted": True
        },
        points=_build_qdrant_filters(
            tenant_id=item.tenant_id,
            data=matching_field,
            match_type=MatchType.must
        )
    )

    return ResponseModel(code=200)

async def delete_points(client: AsyncQdrantClient, item: DeletePointsRequest) -> ResponseModel[Any]:
    if not await client.collection_exists(item.collection_name):
        return ResponseModel(code=404, error="Collection does not exist")

    await client.delete(
        collection_name=item.collection_name,
        points_selector=_build_qdrant_filters(
            tenant_id=item.tenant_id,
            data=[MatchingPayload(field="is_deleted", values=[True])],
            match_type=MatchType.must
        )
    )
    return  ResponseModel(code=200)



async def query_by_payload(client: AsyncQdrantClient, item: QueryByPayloadRequest) -> ResponseModel[Any]:
    if not await client.collection_exists(item.collection_name):
        return ResponseModel(code=404, error="Collection does not exist")

    results, _ = await client.scroll(
        collection_name=item.collection_name,
        scroll_filter=_build_qdrant_filters(
            tenant_id=item.tenant_id,
            data=item.matching_payload or [],
            match_type=item.match_type,
            pagination_config=item.pagination_config),
        limit=item.limit,
        with_payload=True,
        with_vectors=False,
    )

    return ResponseModel(code=200, data = list(results))


async def vector_search(client: AsyncQdrantClient, item: SearchRequest) -> ResponseModel[Any]:
    if not await client.collection_exists(item.collection_name):
        return ResponseModel(code=404, error="Collection does not exist")

    query_filter = _build_qdrant_filters(
        tenant_id=item.tenant_id,
        data=item.matching_payload or [],
        match_type=item.match_type if item.matching_payload else None,
        pagination_config=item.pagination_config,
        knowledge_base=item.knowledge_base,
    )

    results = await client.query_points(
        collection_name=item.collection_name,
        query=item.query_vector,
        limit=item.limit,
        query_filter=query_filter,
        with_payload=True,
    )
    return ResponseModel(code=200, data=results.points)
