from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

import asyncio
from contextlib import asynccontextmanager

from services.log_set_up.logger import setup_logging

setup_logging("workflow-runtime")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import CORS_ORIGINS, REDIS_URL
from db import close_db, init_db
from kafka_producer import close_kafka_producer, init_kafka_producer
from memory_middleware import close_qdrant, init_qdrant
from workflow_memory import close_workflow_qdrant, init_workflow_qdrant
from schema_catalog import close_schema_catalog_qdrant, init_schema_catalog_qdrant
from redis_cache import init_redis, close_redis
from mongo_client import close_mongo, init_mongo
from routers import catalog, conversations, health, sandbox_ops, schema_memory, schema_catalog as schema_catalog_router, traces, reviews
from runtime_services.conversation_runner import invalidate_graph_version
from services.database_connector.mongo_connector import client as mongo_client
from services.database_connector.postgres_connector import client as pg_client
from services.shared.cache_invalidation import run_graph_invalidation_listener
from services.shared.observability import setup_observability


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await init_qdrant()
    await init_workflow_qdrant()
    await init_schema_catalog_qdrant()
    await init_mongo()
    await init_redis()
    await init_kafka_producer()

    tasks = [
        asyncio.create_task(pg_client.health_check_loop()),
        asyncio.create_task(mongo_client.health_check_loop()),
        # Evict locally-cached graphs when another replica/flow-builder signals a
        # republished version. No-op if REDIS_URL is unset.
        asyncio.create_task(
            run_graph_invalidation_listener(REDIS_URL, invalidate_graph_version)
        ),
    ]

    yield

    for task in tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    await close_mongo()
    await close_qdrant()
    await close_workflow_qdrant()
    await close_schema_catalog_qdrant()
    await close_db()
    await close_redis()
    await close_kafka_producer()


def create_app() -> FastAPI:
    app = FastAPI(title="workflow-runtime", version="0.1.0", lifespan=lifespan)
    setup_observability("workflow-runtime", app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Conversation-Id"],
    )

    app.include_router(health.router)
    app.include_router(catalog.router)
    app.include_router(conversations.router)
    app.include_router(traces.router)
    app.include_router(sandbox_ops.router)
    app.include_router(schema_memory.router)
    app.include_router(schema_catalog_router.router)
    app.include_router(reviews.router)

    return app
