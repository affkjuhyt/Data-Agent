"""
kb-ingestion-worker entry point.
Opens all service connections, loads Docling, then runs the Kafka consume loop.
"""
import asyncio
import logging
import os
import sys
from typing import Any

# Project root → services.*, basemodel.*
# Worker dir  → pipeline stages (silver, gold, conflict_resolver) + the `utils`
#               support package (config, worker, docling_parser, …)
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.abspath(os.path.join(_here, "..", ".."))
for _p in (_root, _here):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from utils import config as cfg
from utils.docling_parser import InlineDoclingParser, load_converter
from utils.graph_store import GraphStore
from utils.worker import consume_loop
from conflict_resolver import conflict_resolve_loop

from libs.basemodel.services_databaseconnector.shared_model import RetryConfig, HealthCheckLoopConfig
from libs.basemodel.services_databaseconnector.qdrant_model import CreateCollectionRequest
from libs.database_connector.postgres_connector import PostgresClient
from libs.database_connector.qdrant_connector import (
    QdrantClient as QdrantService,
    create_collection as qdrant_create_collection,
)
from libs.database_connector.neo4j_connector import Neo4jClient
from libs.database_connector.mongo_connector import MongoClient
from libs.database_connector.minio_connector import MinIOClient
from libs.database_connector.kafka_connector import KafkaConsumerClient, KafkaProducerClient
from libs.database_connector.model_connector import ModelServiceClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("kb-ingestion-worker")


async def _probe_embed_dim(model: ModelServiceClient) -> int:
    """Ask the model-service for the embedding dimension by embedding a probe string."""
    vecs = await model.embed(["probe"])
    if not vecs or not vecs[0]:
        raise RuntimeError("model-service returned an empty embedding — cannot determine vector size")
    return len(vecs[0])


async def _ensure_qdrant_collection(
    client: Any,
    name: str,
    embed_dim: int,
    *,
    strict_dim_check: bool = True,
) -> None:
    if await client.collection_exists(name):
        info = await client.get_collection(name)
        collection_dim = info.config.params.vectors.size
        if collection_dim != embed_dim:
            msg = (
                f"Qdrant collection '{name}' has vector size {collection_dim} "
                f"but the embedding model produces {embed_dim}-dim vectors."
            )
            if strict_dim_check:
                raise RuntimeError(msg + "  Drop the collection manually and restart the worker.")
            log.warning("%s  Proceeding with caution.", msg)
        else:
            log.info("Qdrant collection '%s' OK — dim=%d", name, collection_dim)
        return

    result = await qdrant_create_collection(client, CreateCollectionRequest(
        name=name,
        vector_size=embed_dim,
    ))
    if result.code == 200:
        log.info("Qdrant collection '%s' created (dim=%d)", name, embed_dim)
    else:
        raise RuntimeError(f"Failed to create Qdrant collection '{name}': {result.error}")


async def _ensure_payload_indexes(client: Any) -> None:
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
    log.info("Embedding model dim=%d (probed from model-service)", embed_dim)

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
        log.info("Neo4j retrieval indexes ensured")
    except Exception as exc:
        log.warning("Neo4j index creation failed (non-fatal): %s", exc)


async def main() -> None:
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
    # Producer for the DLQ / bounded-retry path (failed silver/gold documents are
    # re-published instead of being dropped after the offset commits).
    producer = KafkaProducerClient()

    postgres.set_url(cfg.POSTGRES_URL)
    qdrant.set_url(cfg.QDRANT_URL)
    neo4j.set_url(cfg.NEO4J_URI)
    mongo.set_url(cfg.MONGO_URL)
    minio.set_url(cfg.MINIO_URL)
    model.set_url(cfg.MODEL_SERVICE_URL)
    consumer.set_url(cfg.KAFKA_URL)
    producer.set_url(cfg.KAFKA_URL)

    log.info("Opening connections...")
    await postgres.open(retry)
    await qdrant.open(retry)
    await neo4j.open(retry)
    await mongo.open(retry)
    await minio.open(retry)
    await model.open(retry)
    await consumer.open(retry)
    await producer.open(retry)

    await _validate_or_create_qdrant_collection(qdrant, model)
    await _ensure_neo4j_indexes(neo4j)
    # The LLM is reached through the model-service /generate gateway (see utils.llm_client);
    # system prompts are sent inline per call, so there is nothing to pre-register here.

    log.info("Loading Docling converter (use_vlm=%s)...", cfg.USE_VLM)
    load_converter(use_vlm=cfg.USE_VLM)
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

    log.info("Worker ready — listening on %s, %s",
             cfg.KAFKA_TOPIC_SILVER, cfg.KAFKA_TOPIC_GOLD)

    conflict_task = asyncio.create_task(
        conflict_resolve_loop(
            postgres=postgres,
            qdrant_svc=qdrant,
            graph=graph,
            model=model,
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
        log.info("Worker shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
