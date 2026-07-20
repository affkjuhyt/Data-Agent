"""
kb-ingestion-worker entry point.
Opens all service connections, loads Docling, then runs the Kafka consume loop.
"""
import asyncio
import os
import sys

# Project root → services.*, basemodel.*
# Worker dir  → pipeline stages (silver, gold, conflict_resolver) + the `utils`
#               support package (config, worker, docling_parser, …)
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.abspath(os.path.join(_here, "..", ".."))
for _p in (_root, _here):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from utils import config as cfg
from utils import conflict_store
from utils.docling_parser import InlineDoclingParser, load_converter
from utils.graph_store import GraphStore
from utils.log_writer import IngestionLogger
from utils.stage_chain import StageChain
from utils.worker import consume_loop
from conflict_resolver import conflict_resolve_loop

from basemodel.services_databaseconnector.shared_model import RetryConfig, HealthCheckLoopConfig
from basemodel.services_databaseconnector.qdrant_model import CreateCollectionRequest
from services.database_connector.postgres_connector import PostgresClient
from services.database_connector.qdrant_connector import (
    QdrantClient as QdrantService,
    create_collection as qdrant_create_collection,
)
from services.database_connector.neo4j_connector import Neo4jClient
from services.database_connector.mongo_connector import MongoClient
from services.database_connector.minio_connector import MinIOClient
from services.database_connector.kafka_connector import KafkaConsumerClient, KafkaProducerClient
from services.database_connector.model_connector import ModelServiceClient


async def _probe_embed_dim(model: ModelServiceClient) -> int:
    """Ask the model-service for the embedding dimension by embedding a probe string."""
    vecs = await model.embed(["probe"])
    if not vecs or not vecs[0]:
        raise RuntimeError("model-service returned an empty embedding — cannot determine vector size")
    return len(vecs[0])


async def _ensure_qdrant_collection(
    client,
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

    result = await qdrant_create_collection(client, CreateCollectionRequest(
        name=name,
        vector_size=embed_dim,
    ))
    if result.code != 200:
        raise RuntimeError(f"Failed to create Qdrant collection '{name}': {result.error}")


async def _ensure_payload_indexes(client) -> None:
    """Create the payload indexes filtered search depends on. The chunk
    collection's data_id index backs step-2 of 2-step retrieval (doc point ids →
    MatchAny filter on chunk points) — without it filtered HNSW search degrades
    badly as the collection grows. Idempotent: 'already exists' errors ignored."""
    from qdrant_client import models as qmodels  # local import keeps module deps unchanged

    from utils.entity_types import ENTITY_TYPE_LABELS  # ent_person, ent_location, … typed filters

    typed_entity_fields = [
        (f"ent_{t}", qmodels.PayloadSchemaType.KEYWORD) for t in ENTITY_TYPE_LABELS
    ]
    wanted: dict[str, list[tuple[str, "qmodels.PayloadSchemaType"]]] = {
        cfg.QDRANT_COLLECTION: [
            ("data_id",        qmodels.PayloadSchemaType.KEYWORD),
            ("tenant_id",      qmodels.PayloadSchemaType.KEYWORD),
            ("knowledge_base", qmodels.PayloadSchemaType.KEYWORD),
            ("entities",       qmodels.PayloadSchemaType.KEYWORD),
            ("is_deleted",     qmodels.PayloadSchemaType.BOOL),
            *typed_entity_fields,
        ],
        cfg.QDRANT_DOC_COLLECTION: [
            ("tenant_id",      qmodels.PayloadSchemaType.KEYWORD),
            ("knowledge_base", qmodels.PayloadSchemaType.KEYWORD),
            # step-1 document narrowing filters by content-purpose intent
            ("intents",        qmodels.PayloadSchemaType.KEYWORD),
            ("is_deleted",     qmodels.PayloadSchemaType.BOOL),
        ],
        cfg.ENTITY_COLLECTION: [
            ("tenant_id",      qmodels.PayloadSchemaType.KEYWORD),
            ("is_deleted",     qmodels.PayloadSchemaType.BOOL),
        ],
    }
    for collection, fields in wanted.items():
        for field, schema in fields:
            try:
                await client.create_payload_index(
                    collection_name=collection,
                    field_name=field,
                    field_schema=schema,
                )
            except Exception:
                pass  # already exists


async def _validate_or_create_qdrant_collection(qdrant: QdrantService, model: ModelServiceClient) -> None:
    """Validate / create the chunk, document, and entity registry collections."""
    embed_dim = await _probe_embed_dim(model)

    client = qdrant.get_client()
    await _ensure_qdrant_collection(client, cfg.QDRANT_COLLECTION,     embed_dim, strict_dim_check=True)
    await _ensure_qdrant_collection(client, cfg.QDRANT_DOC_COLLECTION, embed_dim, strict_dim_check=True)
    await _ensure_qdrant_collection(client, cfg.ENTITY_COLLECTION,     embed_dim, strict_dim_check=False)
    await _ensure_payload_indexes(client)


async def _ensure_neo4j_indexes(neo4j: Neo4jClient) -> None:
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


async def _cancel_tasks(tasks: list[asyncio.Task]) -> None:
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def main() -> None:
    logger = IngestionLogger()
    stage_record = StageChain()

    retry = RetryConfig()
    hc    = HealthCheckLoopConfig()

    postgres = PostgresClient()
    qdrant   = QdrantService()
    neo4j    = Neo4jClient()
    mongo    = MongoClient()
    minio    = MinIOClient()
    model    = ModelServiceClient()
    consumer = KafkaConsumerClient()
    consumer.set_group_id(cfg.KAFKA_CONSUMER_GROUP)
    producer = KafkaProducerClient()

    postgres.set_url(cfg.POSTGRES_URL)
    qdrant.set_url(cfg.QDRANT_URL)
    neo4j.set_url(cfg.NEO4J_URI)
    mongo.set_url(cfg.MONGO_URL)
    minio.set_url(cfg.MINIO_URL)
    model.set_url(cfg.MODEL_SERVICE_URL)
    consumer.set_url(cfg.KAFKA_URL)
    producer.set_url(cfg.KAFKA_URL)

    try:
        await logger.logging("Opening connections...")
        await postgres.open(retry)
        stage_record.push("postgres_opened", rollback_func=postgres.close)
        await qdrant.open(retry)
        stage_record.push("qdrant_opened", rollback_func=qdrant.close)
        await neo4j.open(retry)
        stage_record.push("neo4j_opened", rollback_func=neo4j.close)
        await mongo.open(retry)
        stage_record.push("mongo_opened", rollback_func=mongo.close)
        await minio.open(retry)
        stage_record.push("minio_opened", rollback_func=minio.close)
        await model.open(retry)
        stage_record.push("model_opened", rollback_func=model.close)
        await consumer.open(retry)
        stage_record.push("consumer_opened", rollback_func=consumer.close)
        await producer.open(retry)
        stage_record.push("producer_opened", rollback_func=producer.close)

        await _validate_or_create_qdrant_collection(qdrant, model)
        stage_record.push("qdrant_collections_validated")
        await _ensure_neo4j_indexes(neo4j)
        stage_record.push("neo4j_indexes_ensured")
        await conflict_store.ensure_indexes(mongo.get_client())
        stage_record.push("conflict_indexes_ensured")

        await logger.logging(f"Loading Docling converter (use_vlm={cfg.USE_VLM})...")
        load_converter(use_vlm=cfg.USE_VLM)
        stage_record.push("docling_loaded")
        docling_parser = InlineDoclingParser(minio=minio)

        graph = GraphStore(neo4j.get_client(), postgres, model)

        hc_tasks = [
            asyncio.create_task(postgres.health_check_loop(hc), name="hc-postgres"),
            asyncio.create_task(qdrant.health_check_loop(hc),   name="hc-qdrant"),
            asyncio.create_task(neo4j.health_check_loop(hc),    name="hc-neo4j"),
            asyncio.create_task(mongo.health_check_loop(hc),    name="hc-mongo"),
            asyncio.create_task(minio.health_check_loop(hc),    name="hc-minio"),
            asyncio.create_task(model.health_check_loop(hc),    name="hc-model"),
            asyncio.create_task(consumer.health_check_loop(hc), name="hc-kafka-consumer"),
        ]
        stage_record.push("health_check_tasks_started", rollback_func=_cancel_tasks, rollback_args=(hc_tasks,))

        await logger.logging(f"Worker ready — listening on {cfg.KAFKA_TOPIC_SILVER}, {cfg.KAFKA_TOPIC_GOLD}")
    except Exception as exc:
        await logger.logging(f"Worker startup failed: {exc}", level="ERROR")
        await stage_record.unwind()
        raise

    conflict_task = asyncio.create_task(
        conflict_resolve_loop(
            postgres=postgres,
            qdrant_svc=qdrant,
            graph=graph,
            model=model,
            mongo=mongo,
        ),
        name="conflict-resolver",
    )

    try:
        await consume_loop(
            consumer=consumer,
            minio=minio,
            postgres=postgres,
            mongo=mongo,
            qdrant_svc=qdrant,
            graph=graph,
            model=model,
            docling_parser=docling_parser,
            producer=producer,
        )
    finally:
        conflict_task.cancel()
        for task in hc_tasks:
            task.cancel()
        await asyncio.gather(conflict_task, *hc_tasks, return_exceptions=True)
        await postgres.close()
        await qdrant.close()
        await neo4j.close()
        await mongo.close()
        await minio.close()
        await model.close()
        await consumer.close()
        await producer.close()
        await logger.logging("Worker shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
