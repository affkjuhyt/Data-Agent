from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

import litellm
# Tắt LiteLLM's built-in Langfuse integration — tracing đi qua LangChain's
# CallbackHandler (responder) + observability.observe() decorator (planner,
# judge — gọi litellm trực tiếp, xem nodes/model_harness/planner.py, judge.py).
# Nếu không tắt, litellm tự tạo thêm 1 observation "litellm-acompletion" RIÊNG
# BIỆT không nối vào OTEL context hiện hành (cơ chế nội bộ khác với @observe()/
# CallbackHandler của Langfuse v3) → trùng lặp/nhiễu trace.
litellm.success_callback = [cb for cb in litellm.success_callback if cb != "langfuse"]
litellm.failure_callback = [cb for cb in litellm.failure_callback if cb != "langfuse"]

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from libs.shared import audit
from libs.shared.logger import setup_logging

setup_logging("workflow-runtime")

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import CORS_ORIGINS, REDIS_URL
from db import close_db, init_db
from kafka_producer import close_kafka_producer, init_kafka_producer, produce_event
from memory_middleware import close_qdrant, init_qdrant
from workflow_memory import close_workflow_qdrant, init_workflow_qdrant
from schema_catalog import close_schema_catalog_qdrant, init_schema_catalog_qdrant
from redis_cache import init_redis, close_redis
from mongo_client import close_mongo, init_mongo
from auth import get_current_user
from routers import (
    billing, catalog, conversations, db_connector, health, llm, platform, reviews,
    sandbox_ops, schema_catalog as schema_catalog_router, schema_memory, traces,
)
from runtime_services.conversation_runner import invalidate_graph_version
from libs.database_connector.mongo_connector import client as mongo_client
from libs.database_connector.postgres_connector import client as pg_client
from libs.shared.cache_invalidation import run_graph_invalidation_listener
from libs.shared.observability import setup_observability


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await init_db()
    await init_qdrant()
    await init_workflow_qdrant()
    await init_schema_catalog_qdrant()
    await init_mongo()
    await init_redis()
    await init_kafka_producer()
    # Route data-access audit events through the producer that already
    # exists. backend/libs/shared/audit.py stays transport-free (leaf layer) and
    # drops events until a sink is installed here.
    audit.set_sink(produce_event)

    def _invalidate_graph_version(workflow_version_id: str) -> None:
        # invalidate_graph_version() returns the evicted-entry count for logging
        # at its own call sites; the listener callback contract is fire-and-forget
        # (Callable[[str], None]), so the count is intentionally discarded here.
        invalidate_graph_version(workflow_version_id)

    tasks = [
        asyncio.create_task(pg_client.health_check_loop()),
        asyncio.create_task(mongo_client.health_check_loop()),
        # Evict locally-cached graphs when another replica/flow-builder signals a
        # republished version. No-op if REDIS_URL is unset.
        asyncio.create_task(
            run_graph_invalidation_listener(REDIS_URL, _invalidate_graph_version)
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
    audit.set_sink(None)
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

    # /health stays open — Kong's upstream probe and the compose healthcheck
    # both run before any token exists, and it returns nothing but a literal.
    app.include_router(health.router)

    # Every other router is authenticated at include time rather than route by
    # route: this service had NO authentication at all, and a per-route
    # dependency is one a new endpoint can silently forget. Kong already fronts
    # these paths (deploy/docker/kong/kong-setup.sh runtime-route); the dependency is
    # what makes reaching the port directly useless.
    authed = [Depends(get_current_user)]
    for r in (
        # llm before catalog: both live under /api/llm-providers.
        llm.router,
        catalog.router,
        conversations.router,
        traces.router,
        sandbox_ops.router,
        schema_memory.router,
        schema_catalog_router.router,
        reviews.router,
        db_connector.router,
        platform.router,
        billing.router,
    ):
        app.include_router(r, dependencies=authed)

    return app
