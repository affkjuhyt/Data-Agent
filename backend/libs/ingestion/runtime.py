"""Shared runtime for the ingestion worker processes."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from libs.basemodel.services_databaseconnector.qdrant_model import CreateCollectionRequest
from libs.basemodel.services_databaseconnector.shared_model import HealthCheckLoopConfig, RetryConfig
from libs.database_connector.kafka_connector import KafkaConsumerClient, KafkaProducerClient
from libs.database_connector.minio_connector import MinIOClient
from libs.database_connector.model_connector import ModelServiceClient
from libs.database_connector.mongo_connector import MongoClient
from libs.database_connector.neo4j_connector import Neo4jClient
from libs.database_connector.postgres_connector import PostgresClient
from libs.database_connector.qdrant_connector import (
    QdrantClient as QdrantService,
    create_collection as qdrant_create_collection,
)
from libs.database_connector.redis_connector import RedisClient
from libs.shared.kb_result_cache import ResultCache
from libs.ingestion import config as cfg
from libs.ingestion import conflict_store
from libs.ingestion.graph_store import GraphStore
from libs.ingestion.log_writer import IngestionLogger
from libs.ingestion.stage_chain import StageChain

if TYPE_CHECKING:
    from libs.ingestion.docling_parser import InlineDoclingParser


class Component(str, Enum):
    POSTGRES = "postgres"
    QDRANT = "qdrant"
    NEO4J = "neo4j"
    MONGO = "mongo"
    MINIO = "minio"
    MODEL = "model"
    KAFKA = "kafka"
    REDIS = "redis"
    DOCLING = "docling"
    GRAPH = "graph"


@dataclass
class WorkerRuntime:
    logger: IngestionLogger
    stage_record: StageChain
    retry: RetryConfig = field(default_factory=RetryConfig)
    health_check: HealthCheckLoopConfig = field(default_factory=HealthCheckLoopConfig)
    postgres: PostgresClient = field(default_factory=PostgresClient)
    qdrant: QdrantService = field(default_factory=QdrantService)
    neo4j: Neo4jClient = field(default_factory=Neo4jClient)
    mongo: MongoClient = field(default_factory=MongoClient)
    minio: MinIOClient = field(default_factory=MinIOClient)
    model: ModelServiceClient = field(default_factory=ModelServiceClient)
    consumer: KafkaConsumerClient = field(default_factory=KafkaConsumerClient)
    producer: KafkaProducerClient = field(default_factory=KafkaProducerClient)
    redis: RedisClient = field(default_factory=RedisClient)
    result_cache: ResultCache | None = None
    docling_parser: InlineDoclingParser | None = None
    graph: GraphStore | None = None
    health_check_tasks: list[asyncio.Task[Any]] = field(default_factory=list)

    def require_graph(self) -> GraphStore:
        """The graph store, for workers that started with Component.GRAPH."""
        if self.graph is None:
            raise RuntimeError("graph store not initialised: start the runtime with Component.GRAPH")
        return self.graph

    def configure_urls(self) -> None:
        self.consumer.set_group_id(cfg.KAFKA_CONSUMER_GROUP)
        self.postgres.set_url(cfg.POSTGRES_URL)
        self.qdrant.set_url(cfg.QDRANT_URL)
        self.neo4j.set_url(cfg.NEO4J_URI)
        self.mongo.set_url(cfg.MONGO_URL)
        self.minio.set_url(cfg.MINIO_URL)
        self.model.set_url(cfg.MODEL_SERVICE_URL)
        self.consumer.set_url(cfg.KAFKA_URL)
        self.producer.set_url(cfg.KAFKA_URL)
        self.redis.set_url(cfg.REDIS_URL)


def build_runtime() -> WorkerRuntime:
    runtime = WorkerRuntime(
        logger=IngestionLogger(),
        stage_record=StageChain(),
    )
    runtime.configure_urls()
    return runtime


async def probe_embed_dim(model: ModelServiceClient) -> int:
    vecs = await model.embed(["probe"])
    if not vecs or not vecs[0]:
        raise RuntimeError("model_service returned an empty embedding — cannot determine vector size")
    return len(vecs[0])


async def ensure_qdrant_collection(
    client: Any,
    name: str,
    embed_dim: int,
    *,
    strict_dim_check: bool = True,
) -> None:
    if await client.collection_exists(name):
        info = await client.get_collection(name)
        collection_dim = info.config.params.vectors.size
        if collection_dim != embed_dim and strict_dim_check:
            raise RuntimeError(
                f"Qdrant collection '{name}' has vector size {collection_dim} but the "
                f"embedding model produces {embed_dim}-dim vectors. Drop the collection "
                f"manually and restart the worker."
            )
        return

    result = await qdrant_create_collection(
        client,
        CreateCollectionRequest(name=name, vector_size=embed_dim),
    )
    if result.code != 200:
        raise RuntimeError(f"Failed to create Qdrant collection '{name}': {result.error}")


async def ensure_payload_indexes(client: Any) -> None:
    from qdrant_client import models as qmodels

    from libs.ingestion.entity_types import ENTITY_TYPE_LABELS

    typed_entity_fields = [(f"ent_{entity_type}", qmodels.PayloadSchemaType.KEYWORD) for entity_type in ENTITY_TYPE_LABELS]
    wanted: dict[str, list[tuple[str, "qmodels.PayloadSchemaType"]]] = {
        cfg.QDRANT_COLLECTION: [
            ("data_id", qmodels.PayloadSchemaType.KEYWORD),
            ("tenant_id", qmodels.PayloadSchemaType.KEYWORD),
            ("knowledge_base", qmodels.PayloadSchemaType.KEYWORD),
            ("entities", qmodels.PayloadSchemaType.KEYWORD),
            ("is_deleted", qmodels.PayloadSchemaType.BOOL),
            *typed_entity_fields,
        ],
        cfg.QDRANT_DOC_COLLECTION: [
            ("tenant_id", qmodels.PayloadSchemaType.KEYWORD),
            ("knowledge_base", qmodels.PayloadSchemaType.KEYWORD),
            ("intents", qmodels.PayloadSchemaType.KEYWORD),
            ("is_deleted", qmodels.PayloadSchemaType.BOOL),
        ],
        cfg.ENTITY_COLLECTION: [
            ("tenant_id", qmodels.PayloadSchemaType.KEYWORD),
            ("is_deleted", qmodels.PayloadSchemaType.BOOL),
        ],
    }
    for collection, fields in wanted.items():
        for field_name, schema in fields:
            try:
                await client.create_payload_index(
                    collection_name=collection,
                    field_name=field_name,
                    field_schema=schema,
                )
            except Exception:
                pass


async def validate_or_create_qdrant_collections(
    qdrant: QdrantService,
    model: ModelServiceClient,
) -> None:
    embed_dim = await probe_embed_dim(model)
    client = qdrant.get_client()
    await ensure_qdrant_collection(client, cfg.QDRANT_COLLECTION, embed_dim, strict_dim_check=True)
    await ensure_qdrant_collection(client, cfg.QDRANT_DOC_COLLECTION, embed_dim, strict_dim_check=True)
    await ensure_qdrant_collection(client, cfg.ENTITY_COLLECTION, embed_dim, strict_dim_check=False)
    await ensure_payload_indexes(client)


async def ensure_neo4j_indexes(neo4j: Neo4jClient) -> None:
    statements = [
        "CREATE INDEX entity_canonical_tenant IF NOT EXISTS FOR (n:Entity) ON (n.canonical_name, n.tenant_id)",
        "CREATE INDEX entity_type_idx         IF NOT EXISTS FOR (n:Entity) ON (n.entity_type)",
    ]
    try:
        async with neo4j.get_client().session() as session:
            for stmt in statements:
                result = await session.run(stmt)
                await result.consume()
    except Exception:
        pass


async def cancel_tasks(tasks: list[asyncio.Task[Any]]) -> None:
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def startup(runtime: WorkerRuntime, components: set[Component]) -> None:
    """Open only the stores this process needs, recording each for unwind."""
    await runtime.logger.logging(f"Opening connections: {sorted(c.value for c in components)}")

    async def open_client(component: Component, client: Any) -> None:
        if component not in components:
            return
        await client.open(runtime.retry)
        runtime.stage_record.push(f"{component.value}_opened", rollback_func=client.close)

    await open_client(Component.POSTGRES, runtime.postgres)
    await open_client(Component.QDRANT, runtime.qdrant)
    await open_client(Component.NEO4J, runtime.neo4j)
    await open_client(Component.MONGO, runtime.mongo)
    await open_client(Component.MINIO, runtime.minio)
    await open_client(Component.MODEL, runtime.model)
    if Component.KAFKA in components:
        await open_client(Component.KAFKA, runtime.consumer)
        await runtime.producer.open(runtime.retry)
        runtime.stage_record.push("producer_opened", rollback_func=runtime.producer.close)

    if Component.REDIS in components:
        try:
            await runtime.redis.open(runtime.retry)
            runtime.stage_record.push("redis_opened", rollback_func=runtime.redis.close)
        except Exception as exc:
            await runtime.logger.logging(
                f"Redis unavailable — cache invalidation disabled: {exc}", level="WARNING",
            )
        runtime.result_cache = ResultCache(runtime.redis, 900, cfg.RESULT_CACHE_ENABLED)

    if Component.QDRANT in components and Component.MODEL in components:
        await validate_or_create_qdrant_collections(runtime.qdrant, runtime.model)
        runtime.stage_record.push("qdrant_collections_validated")
    if Component.NEO4J in components:
        await ensure_neo4j_indexes(runtime.neo4j)
        runtime.stage_record.push("neo4j_indexes_ensured")
    if Component.MONGO in components:
        await conflict_store.ensure_indexes(runtime.mongo.get_client())
        runtime.stage_record.push("conflict_indexes_ensured")

    if Component.DOCLING in components:
        from libs.ingestion.docling_parser import InlineDoclingParser, load_converter

        await runtime.logger.logging(f"Loading Docling converter (use_vlm={cfg.USE_VLM})...")
        load_converter(use_vlm=cfg.USE_VLM)
        runtime.stage_record.push("docling_loaded")
        runtime.docling_parser = InlineDoclingParser(minio=runtime.minio)

    if Component.GRAPH in components:
        runtime.graph = GraphStore(runtime.neo4j.get_client(), runtime.postgres, runtime.model)

    health_checked = {
        Component.POSTGRES: runtime.postgres,
        Component.QDRANT: runtime.qdrant,
        Component.NEO4J: runtime.neo4j,
        Component.MONGO: runtime.mongo,
        Component.MINIO: runtime.minio,
        Component.MODEL: runtime.model,
        Component.KAFKA: runtime.consumer,
    }
    runtime.health_check_tasks = [
        asyncio.create_task(client.health_check_loop(runtime.health_check), name=f"hc-{component.value}")
        for component, client in health_checked.items()
        if component in components
    ]
    runtime.stage_record.push(
        "health_check_tasks_started",
        rollback_func=cancel_tasks,
        rollback_args=(runtime.health_check_tasks,),
    )
