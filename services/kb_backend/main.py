from typing import Any
from collections.abc import AsyncIterator
import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from libs.basemodel.services_databaseconnector.postgres_orm.base import Base

from libs.basemodel.services_databaseconnector.shared_model import RetryConfig, HealthCheckLoopConfig
from libs.database_connector.postgres_connector import PostgresClient
from libs.database_connector.qdrant_connector import QdrantClient as QdrantService
from libs.database_connector.neo4j_connector import Neo4jClient
from libs.database_connector.mongo_connector import MongoClient
from libs.database_connector.minio_connector import MinIOClient
from libs.database_connector.kafka_connector import KafkaProducerClient
from libs.database_connector.model_connector import ModelServiceClient
from libs.shared.observability import setup_observability

from services.kb_backend.pipeline.light_rag.pipeline import LightRAGPipeline, cache_sweep_loop
from services.kb_backend.pipeline.light_rag import config as cfg
from services.kb_backend.pipeline.ingestion.pipeline import IngestionPipeline

log = logging.getLogger(__name__)

_POSTGRES_URL  = os.environ.get("POSTGRES_URL",  "postgresql+asyncpg://aeroflow:aeroflow_secret@postgres/aeroflow_kb")
_MONGO_URL     = os.environ.get("MONGO_URL",     "mongodb://mongo:27017/dataagent")
_MINIO_URL     = os.environ.get("MINIO_URL",     "http://minioadmin:minioadmin@minio:9000")
_KAFKA_URL     = os.environ.get("KAFKA_URL",     "kafka:9092")

# Lite/auth-verification mode. When true, only Postgres is opened at startup;
# the heavy data-plane stores (Qdrant, Neo4j, Mongo, MinIO, model-service, Kafka)
# are skipped so the API can boot for verifying the Kong→router identity chain
# (user_id / tenant_id / role / permission) without standing up the full stack.
# Routes that actually touch those stores will fail at request time — that's
# expected; RBAC-gated mutations 403 before ever reaching a store.
KB_LITE = os.getenv("KB_LITE", "false").lower() == "true"

# One instance per service — shared for the full process lifetime
_postgres = PostgresClient()
_qdrant   = QdrantService()
_neo4j    = Neo4jClient()
_mongo    = MongoClient()
_minio    = MinIOClient()
_model    = ModelServiceClient()

_kafka_producer = KafkaProducerClient()

# Stateless pipelines — clients injected per request from app.state
_light_rag  = LightRAGPipeline()
_ingestion  = IngestionPipeline()


def _supervise(task: asyncio.Task[Any]) -> asyncio.Task[Any]:
    """Attach a done-callback that surfaces a background loop dying.

    The health-check and cache-sweep loops are `while True` and must never
    complete while the process is up. If one escapes its own try/except (e.g. a
    reconnect path raising an unexpected error), the task ends and its exception
    is otherwise swallowed until GC prints "Task exception was never retrieved".
    A dead health loop is dangerous: the connector's `_healthy` flag freezes, so
    `svc()` gating goes stale (permanent 503, or worse, handing out a dead
    client). Log it loudly so it is visible immediately instead of silently.
    """
    def _on_done(t: asyncio.Task[Any]) -> None:
        if t.cancelled():
            return  # expected on shutdown
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
    # ── Postgres bootstrap ────────────────────────────────────────────────────
    # Connect to template1 (always exists) to check/create the target DB.
    # Handles any DB name (aeroflow_kb in standalone stacks, dataagent in the
    # full-platform overlay). Safety net for fresh non-Docker starts only —
    # in Docker the init scripts already created the DB before this runs.
    _pg_db = _POSTGRES_URL.rsplit("/", 1)[-1].split("?")[0]
    admin_url = _POSTGRES_URL.rsplit("/", 1)[0] + "/template1"
    admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin_engine.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": _pg_db},
            )
            if not exists:
                # DDL cannot be parameterized; _pg_db comes from our own env var.
                await conn.execute(text(f"CREATE DATABASE {_pg_db}"))
    finally:
        await admin_engine.dispose()

    # Schema is owned by infra/Postgres/init.sql (idempotent CREATE TABLE IF NOT
    # EXISTS) and seeded by infra/Postgres/seed.sql at Postgres init. create_all
    # stays as a safety net for non-Docker / fresh starts where those scripts did
    # not run — it no-ops on tables that already exist. There is NO Python seeder.
    kb_engine = create_async_engine(_POSTGRES_URL)
    async with kb_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await kb_engine.dispose()

    # ── Set URLs ──────────────────────────────────────────────────────────────
    retry = RetryConfig()

    _postgres.set_url(_POSTGRES_URL)
    _qdrant.set_url(cfg.QDRANT_URL)
    _neo4j.set_url(cfg.NEO4J_URL)
    _mongo.set_url(_MONGO_URL)
    _minio.set_url(_MINIO_URL)
    _model.set_url(cfg.MODEL_SERVICE_URL)
    _kafka_producer.set_url(_KAFKA_URL)

    # ── Open all connections ──────────────────────────────────────────────────
    await _postgres.open(retry)
    if KB_LITE:
        log.warning(
            "KB_LITE=true — skipping Qdrant/Neo4j/Mongo/MinIO/model-service/Kafka "
            "connections. Auth/RBAC verification mode: only Postgres is live."
        )
    else:
        await _qdrant.open(retry)
        await _neo4j.open(retry)
        await _mongo.open(retry)
        await _minio.open(retry)
        await _model.open(retry)
        await _kafka_producer.open(retry)

    # ── Expose on app.state ───────────────────────────────────────────────────
    app.state.postgres       = _postgres
    app.state.qdrant         = _qdrant
    app.state.neo4j          = _neo4j
    app.state.mongo          = _mongo
    app.state.minio          = _minio
    app.state.model          = _model
    app.state.kafka_producer = _kafka_producer
    app.state.light_rag      = _light_rag
    app.state.ingestion      = _ingestion

    # ── Background: health check loops ────────────────────────────────────────
    hc = HealthCheckLoopConfig()
    hc_tasks = [
        _supervise(asyncio.create_task(_postgres.health_check_loop(hc),       name="hc-postgres")),
    ]
    if not KB_LITE:
        hc_tasks += [
            _supervise(asyncio.create_task(_qdrant.health_check_loop(hc),         name="hc-qdrant")),
            _supervise(asyncio.create_task(_neo4j.health_check_loop(hc),          name="hc-neo4j")),
            _supervise(asyncio.create_task(_mongo.health_check_loop(hc),          name="hc-mongo")),
            _supervise(asyncio.create_task(_minio.health_check_loop(hc),          name="hc-minio")),
            _supervise(asyncio.create_task(_model.health_check_loop(hc),          name="hc-model")),
            _supervise(asyncio.create_task(_kafka_producer.health_check_loop(hc), name="hc-kafka-producer")),
            # Background sweep that reclaims expired LightRAG query-cache entries (24h TTL).
            _supervise(asyncio.create_task(cache_sweep_loop(_qdrant.get_client()), name="cache-sweep")),
        ]

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    for task in hc_tasks:
        task.cancel()
    await asyncio.gather(*hc_tasks, return_exceptions=True)

    await _postgres.close()
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
