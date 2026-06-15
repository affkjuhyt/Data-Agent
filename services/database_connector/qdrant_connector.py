from qdrant_client import models
from qdrant_client.async_qdrant_client import AsyncQdrantClient
from typing import List
import asyncio
import logging
from basemodel.services_databaseconnector.qdrant_model import (
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

from basemodel.services_databaseconnector.shared_model import (
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
        pagination_config: PaginationConfig | None = None) -> models.Filter | None:


    def build_matching_field():
        fields = []
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
        paging_filter = models.FieldCondition(
            key=pagination_config.pagination_on,
            range=models.Range(gt=pagination_config.pagination_cursor),
        )
        return [paging_filter]


    matching_require = build_matching_tenant()
    matching_field = build_matching_field()
    if pagination_config is not None and pagination_config.pagination_cursor is not None:
        matching_require += build_paginating()


    if not matching_field:
        return None

    if match_type is MatchType.any:
        return models.Filter(
            must=matching_require,
            should=matching_field,
        )

    return models.Filter(
        must=matching_require + matching_field,
    )


def _build_batch_points(tenant_id: str, points: List[PointData]):

    def add_tenant(payload: PointPayload):
        payload.tenant_id = tenant_id
        return payload

    return [
        models.PointStruct(
            id=p.payload.data_id,
            vector=p.vector,
            payload=add_tenant(p.payload).model_dump(mode="json"),
        )
        for p in points
    ]


# Qdrant Client
class QdrantClient:
    # Follow DatabaseConnector Protocol in server/basemodel/protocol_model.py
    __slots__ = ("_client","_retry" ,"_connection_wait", "_healthy", "_connected", "_timeout", "_timeout_incremental", "_url")
    def __init__(self):
        self._client: AsyncQdrantClient | None = None
        self._timeout:int = 10
        self._timeout_incremental:int = 1
        self._connection_wait:int = 5
        self._healthy:bool = False
        self._connected:bool = False
        self._url:str|None = None

    def set_url(self, url: str):
        self._url = url

    async def health_check_loop(self, config: HealthCheckLoopConfig):
        log.info("Qdrant health check loop started")
        while True:
            try:
                if self._client is None:
                    self._create_connection()

                await asyncio.wait_for(
                    self._client.get_collections(),
                    timeout=config.timeout_for_health_check
                )

                log.info("Qdrant health check successes")

                self._healthy = True
            except asyncio.TimeoutError as e:
                log.info(f"Qdrant health check failed: {e}")

                self._healthy = False

            await asyncio.sleep(config.interval)

    async def open(self, retry: RetryConfig):
        if self._client is None:
           return self._client
        log.info("Qdrant Connection established")
        self._create_connection()
        for _ in range(retry.count):
            try:
                await asyncio.wait_for(
                    self._client.get_collections(),
                    timeout = self._timeout + min(self._timeout, _ * self._timeout_incremental)
                )

                log.info("Qdrant connection successes")
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

    def is_healthy(self):
        if self._client is None:
            raise ConnectionError("Connection must be first established")
        return self._healthy

    def is_connected(self):
        if self._client is None:
            raise ConnectionError("Connection must be first established")
        return self._connected

    def get_client(self) -> AsyncQdrantClient:
        if self._client is None:
            raise ConnectionError("Connection must be first established")
        return self._client

    def _create_connection(self) -> AsyncQdrantClient:
        if self._url is None:
            raise ValueError("qdrant url must be set")
        self._client = AsyncQdrantClient(
                    url = self._url,
                )
        return self._client

async def delete_collection(client: AsyncQdrantClient, item: DeleteCollectionRequest) -> ResponseModel:
    if not await client.collection_exists(item.collection_name):
        return ResponseModel(code=404, error="Collection does not exist")
    await client.delete_collection(item.collection_name)
    return ResponseModel(code=200)

async def create_collection(client: AsyncQdrantClient, item: CreateCollectionRequest) -> ResponseModel:
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


async def add_points(client: AsyncQdrantClient, item: AddPointsRequest) -> ResponseModel:
    if not await client.collection_exists(item.collection_name):
        return ResponseModel(code=404, error="Collection does not exist")

    if not item.points:
        return ResponseModel(code=400, error="Must provide points")

    await client.upsert(
        collection_name=item.collection_name,
        points=_build_batch_points(item.tenant_id,item.points),
    )
    return ResponseModel(code=200)


async def soft_delete_points(client: AsyncQdrantClient, item: SoftDeletePointsRequest) -> ResponseModel:
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

async def delete_points(client: AsyncQdrantClient, item: DeletePointsRequest) -> ResponseModel:
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



async def query_by_payload(client: AsyncQdrantClient, item: QueryByPayloadRequest) -> ResponseModel:
    if not await client.collection_exists(item.collection_name):
        return ResponseModel(code=404, error="Collection does not exist")

    results, _ = await client.scroll(
        collection_name=item.collection_name,
        scroll_filter=_build_qdrant_filters(
            tenant_id=item.tenant_id,
            data=item.matching_payload,
            match_type=item.match_type,
            pagination_config=item.pagination_config),
        limit=item.limit,
        with_payload=True,
        with_vectors=False,
    )

    return ResponseModel(code=200, data = list(results))


async def vector_search(client: AsyncQdrantClient, item: SearchRequest) -> ResponseModel:
    if not await client.collection_exists(item.collection_name):
        return ResponseModel(code=404, error="Collection does not exist")

    query_filter = None
    if item.matching_payload is not None:
        query_filter = _build_qdrant_filters(
            tenant_id=item.tenant_id,
            data=item.matching_payload,
            match_type=MatchType.must,
            pagination_config=item.pagination_config,
        )

    results = await client.query_points(
        collection_name=item.collection_name,
        query_vector=item.query_vector,
        limit=item.limit,
        query_filter=query_filter,
        with_payload=True,
    )
    return ResponseModel(code = 200, data=list(results))
