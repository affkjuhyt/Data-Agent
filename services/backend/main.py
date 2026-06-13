import asyncio
import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from basemodel.services_databaseconnector.postgres_orm.base import Base
import basemodel.services_databaseconnector.postgres_orm  # noqa: F401 — registers ORM models

from basemodel.services_databaseconnector.shared_model import RetryConfig, HealthCheckLoopConfig
from services.database_connector.postgres_connector import PostgresClient
from services.database_connector.qdrant_connector import QdrantClient as QdrantService
from services.database_connector.neo4j_connector import Neo4jClient
from services.database_connector.mongo_connector import MongoClient
from services.database_connector.minio_connector import MinIOClient
from services.database_connector.kafka_connector import KafkaProducerClient
from services.database_connector.model_connector import ModelServiceClient

from services.backend.pipeline.light_rag.pipeline import LightRAGPipeline, cache_sweep_loop
from services.backend.pipeline.light_rag.pipeline import SYSTEM_PROMPTS as _LR_SYSTEM_PROMPTS
from services.backend.pipeline.light_rag import config as cfg
from services.backend.pipeline.ingestion.pipeline import IngestionPipeline
from services.backend.dev_seed import seed_dev_data

log = logging.getLogger(__name__)

_LLAMA_BASE_URL = os.environ.get("LLAMA_BASE_URL", "http://llama:11434")

# Only LightRAG prompts — silver/gold prompts are registered by kb-ingestion-worker
_ALL_SYSTEM_PROMPTS: dict[str, str] = {**_LR_SYSTEM_PROMPTS}


async def _register_system_prompts() -> None:
    """POST each system prompt to the llama server's /api/system endpoint.
    Pre-computes KV cache snapshots so each generation call only tokenizes user content.
    Best-effort — failures are logged but don't block startup."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        for name, prompt in _ALL_SYSTEM_PROMPTS.items():
            try:
                resp = await client.post(
                    f"{_LLAMA_BASE_URL}/api/system",
                    json={"name": name, "prompt": prompt},
                )
                resp.raise_for_status()
                log.info("startup: registered system type %r on llama server", name)
            except Exception as exc:
                log.warning("startup: failed to register system type %r: %s", name, exc)

_POSTGRES_URL  = os.environ.get("POSTGRES_URL",  "postgresql+asyncpg://aeroflow:aeroflow_secret@postgres/aeroflow_kb")
_MONGO_URL     = os.environ.get("MONGO_URL",     "mongodb://mongo:27017/dataagent")
_MINIO_URL     = os.environ.get("MINIO_URL",     "http://minioadmin:minioadmin@minio:9000")
_KAFKA_URL     = os.environ.get("KAFKA_URL",     "kafka:9092")

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Postgres bootstrap ────────────────────────────────────────────────────
    # Connect to template1 (always exists) to check/create aeroflow_kb.
    # The Docker container creates aeroflow_kb on first init, so this is a
    # safety net for fresh non-Docker starts only.
    admin_url = _POSTGRES_URL.rsplit("/", 1)[0] + "/template1"
    admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin_engine.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = 'aeroflow_kb'")
            )
            if not exists:
                await conn.execute(text("CREATE DATABASE aeroflow_kb"))
    finally:
        await admin_engine.dispose()

    kb_engine = create_async_engine(_POSTGRES_URL)
    async with kb_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await seed_dev_data(kb_engine)
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
    await _qdrant.open(retry)
    await _neo4j.open(retry)
    await _mongo.open(retry)
    await _minio.open(retry)
    await _model.open(retry)
    await _kafka_producer.open(retry)

    # ── Register system prompts on llama server (best-effort) ────────────────
    await _register_system_prompts()

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
        asyncio.create_task(_postgres.health_check_loop(hc),       name="hc-postgres"),
        asyncio.create_task(_qdrant.health_check_loop(hc),         name="hc-qdrant"),
        asyncio.create_task(_neo4j.health_check_loop(hc),          name="hc-neo4j"),
        asyncio.create_task(_mongo.health_check_loop(hc),          name="hc-mongo"),
        asyncio.create_task(_minio.health_check_loop(hc),          name="hc-minio"),
        asyncio.create_task(_model.health_check_loop(hc),          name="hc-model"),
        asyncio.create_task(_kafka_producer.health_check_loop(hc), name="hc-kafka-producer"),
        # Background sweep that reclaims expired LightRAG query-cache entries (24h TTL).
        asyncio.create_task(cache_sweep_loop(_qdrant.get_client()), name="cache-sweep"),
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

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://localhost(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from services.backend.router.knowledge import router

app.include_router(router)


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok"}
