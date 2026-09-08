from typing import Any
from collections.abc import AsyncIterator
import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from libs.basemodel.services_databaseconnector.shared_model import RetryConfig, HealthCheckLoopConfig, PoolConfig
from libs.database_connector.postgres_connector import PostgresClient
from libs.database_connector.qdrant_connector import QdrantClient as QdrantService
from libs.database_connector.neo4j_connector import Neo4jClient
from libs.database_connector.mongo_connector import MongoClient
from libs.database_connector.minio_connector import MinIOClient
from libs.database_connector.redis_connector import RedisClient
from libs.shared.kb_result_cache import ResultCache
from libs.database_connector.kafka_connector import KafkaProducerClient
from libs.database_connector.model_connector import ModelServiceClient
from libs.shared.observability import setup_observability

from services.kb_backend.pipeline.light_rag.pipeline import LightRAGPipeline, cache_sweep_loop
from services.kb_backend.pipeline.light_rag import config as cfg
from services.kb_backend.pipeline.ingestion.pipeline import IngestionPipeline

log = logging.getLogger(__name__)

# KB_BACKEND_URL_ASYNC is the table-scoped, NOBYPASSRLS role. Falling back to
# POSTGRES_URL keeps an un-migrated deployment working; setting it is what
# makes row-level security actually apply to this service.
_POSTGRES_URL = os.environ.get("KB_BACKEND_URL_ASYNC", "") or os.environ.get("POSTGRES_URL", "")
# Set when _POSTGRES_URL points at PgBouncer: session-scoped work (advisory
# locks) needs a connection that is not multiplexed per transaction.
_POSTGRES_DIRECT_URL = os.environ.get("POSTGRES_DIRECT_URL", "")
_MONGO_URL = os.environ.get("MONGO_URL", "mongodb://mongo:27017/dataagent")
_MINIO_URL = os.environ.get("MINIO_URL", "http://minioadmin:minioadmin@minio:9000")
_KAFKA_URL = os.environ.get("KAFKA_URL", "kafka:9092")
_REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
_RESULT_CACHE_ENABLED = os.environ.get("RESULT_CACHE_ENABLED", "true").lower() in {"1", "true", "yes"}
_RESULT_CACHE_TTL = int(os.environ.get("RESULT_CACHE_TTL", "900"))

KB_LITE = os.getenv("KB_LITE", "false").lower() == "true"

_postgres = PostgresClient()
_postgres_direct = PostgresClient(PoolConfig.from_env("POSTGRES_DIRECT")) if _POSTGRES_DIRECT_URL else None
_qdrant = QdrantService()
_neo4j = Neo4jClient()
_mongo = MongoClient()
_minio = MinIOClient()
_model = ModelServiceClient()

_kafka_producer = KafkaProducerClient()
_redis = RedisClient()
_result_cache = ResultCache(_redis, _RESULT_CACHE_TTL, _RESULT_CACHE_ENABLED)

_light_rag = LightRAGPipeline()
_ingestion = IngestionPipeline()


def _supervise(task: asyncio.Task[Any]) -> asyncio.Task[Any]:
    def _on_done(t: asyncio.Task[Any]) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            log.error("Background task %r died with an unhandled exception — "
                      "health gating for this service is now STALE", t.get_name(),
                      exc_info=exc)
        else:
            log.error("Background task %r exited unexpectedly (loops should run "
                      "for the process lifetime)", t.get_name())

    task.add_done_callback(_on_done)
    return task


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if not _POSTGRES_URL:
        raise RuntimeError("POSTGRES_URL is not set (see deploy/k8s/secrets.env.example)")

    retry = RetryConfig()

    _postgres.set_url(_POSTGRES_URL)
    if _postgres_direct is not None:
        _postgres_direct.set_url(_POSTGRES_DIRECT_URL)
    _qdrant.set_url(cfg.QDRANT_URL)
    _neo4j.set_url(cfg.NEO4J_URL)
    _mongo.set_url(_MONGO_URL)
    _minio.set_url(_MINIO_URL)
    _model.set_url(cfg.MODEL_SERVICE_URL)
    _kafka_producer.set_url(_KAFKA_URL)
    _redis.set_url(_REDIS_URL)

    await _postgres.open(retry)
    if _postgres_direct is not None:
        await _postgres_direct.open(retry)
    async with _postgres.get_platform_client() as session:
        migrated = await session.scalar(text(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'KBData'"
        ))
    if not migrated:
        raise RuntimeError("Schema is missing — run the migrate Job before starting kb-backend")

    if KB_LITE:
        log.warning(
            "KB_LITE=true — skipping Qdrant/Neo4j/Mongo/MinIO/model_service/Kafka "
            "connections. Auth/RBAC verification mode: only Postgres is live."
        )
    else:
        await _qdrant.open(retry)
        await _neo4j.open(retry)
        await _mongo.open(retry)
        await _minio.open(retry)
        await _model.open(retry)
        await _kafka_producer.open(retry)
        try:
            await _redis.open(retry)
        except Exception as e:
            log.warning("Redis (result cache) unavailable — retrieval works uncached: %s", e)

    app.state.postgres = _postgres
    app.state.postgres_direct = _postgres_direct or _postgres
    app.state.qdrant = _qdrant
    app.state.neo4j = _neo4j
    app.state.mongo = _mongo
    app.state.minio = _minio
    app.state.model = _model
    app.state.kafka_producer = _kafka_producer
    app.state.redis = _redis
    app.state.result_cache = _result_cache
    app.state.light_rag = _light_rag
    app.state.ingestion = _ingestion

    hc = HealthCheckLoopConfig()
    hc_tasks = [
        _supervise(asyncio.create_task(_postgres.health_check_loop(hc), name="hc-postgres")),
    ]
    if not KB_LITE:
        hc_tasks += [
            _supervise(asyncio.create_task(_qdrant.health_check_loop(hc), name="hc-qdrant")),
            _supervise(asyncio.create_task(_neo4j.health_check_loop(hc), name="hc-neo4j")),
            _supervise(asyncio.create_task(_mongo.health_check_loop(hc), name="hc-mongo")),
            _supervise(asyncio.create_task(_minio.health_check_loop(hc), name="hc-minio")),
            _supervise(asyncio.create_task(_model.health_check_loop(hc), name="hc-model")),
            _supervise(asyncio.create_task(_kafka_producer.health_check_loop(hc), name="hc-kafka-producer")),
            _supervise(asyncio.create_task(cache_sweep_loop(_qdrant), name="cache-sweep")),
        ]

    yield

    for task in hc_tasks:
        task.cancel()
    await asyncio.gather(*hc_tasks, return_exceptions=True)

    await _postgres.close()
    if _postgres_direct is not None:
        await _postgres_direct.close()
    await _qdrant.close()
    await _neo4j.close()
    await _mongo.close()
    await _minio.close()
    await _model.close()
    await _kafka_producer.close()


app = FastAPI(title="AeroFlow KB API", lifespan=lifespan)
setup_observability("kb-backend", app)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://localhost(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from services.kb_backend.router.knowledge import router

app.include_router(router)


@app.get("/health", tags=["Health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
