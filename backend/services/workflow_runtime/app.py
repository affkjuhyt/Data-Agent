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
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from libs.shared import audit
from libs.shared.logger import setup_logging

setup_logging("workflow_runtime")
logger = logging.getLogger(__name__)

from services.workflow_runtime.auth import get_current_user
from services.workflow_runtime.config import CORS_ORIGINS, REDIS_URL
from services.workflow_runtime.db import close_db, init_db
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from services.workflow_runtime.kafka_producer import close_kafka_producer, init_kafka_producer, produce_event
from services.workflow_runtime.memory_middleware import close_qdrant, init_qdrant
from services.workflow_runtime.mongo_client import close_mongo, init_mongo
from services.workflow_runtime.orchestrator.routers import reviews as orch_reviews
from services.workflow_runtime.orchestrator.routers import stream as orch_stream
from services.workflow_runtime.orchestrator.routers import traces as orch_traces
from services.workflow_runtime.redis_cache import close_redis, init_redis
from services.workflow_runtime.routers import (
    agents,
    billing,
    catalog,
    canvas,
    conversations,
    db_connector,
    health,
    llm,
    platform,
    reviews,
    ask_exports,
    roi_use_cases,
    sandbox_ops,
    storage,
    traces,
    workflows,
)
from services.workflow_runtime.runtime_services.conversation_runner import invalidate_graph_version
from services.workflow_runtime.runtime_services.roi_use_cases import scheduler_loop
from services.workflow_runtime.schema_catalog import close_schema_catalog_qdrant, init_schema_catalog_qdrant
from services.workflow_runtime.workflow_memory import close_workflow_qdrant, init_workflow_qdrant

from libs.basemodel.services_databaseconnector.shared_model import RetryConfig
from libs.database_connector.mongo_connector import client as mongo_client
from libs.database_connector.postgres_connector import client as pg_client
from libs.database_connector.redis_connector import RedisClient
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

    # Orchestrator planning-state checkpoint cache (orchestrator/session_cache.py
    # reads request.app.state.redis). Separate from redis_cache.py's raw client
    # on purpose: session_cache needs RedisClient's is_healthy()/health loop.
    orch_redis = RedisClient()
    orch_redis.set_url(REDIS_URL)
    try:
        await orch_redis.open(RetryConfig(count=1))
        logger.info("orchestrator Redis connected")
    except Exception as e:  # noqa: BLE001 — health loop retries; the cache is optional
        logger.warning("orchestrator Redis unavailable at startup, health loop will retry: %s", e)
    app.state.redis = orch_redis

    def _invalidate_graph_version(workflow_version_id: str) -> None:
        # invalidate_graph_version() returns the evicted-entry count for logging
        # at its own call sites; the listener callback contract is fire-and-forget
        # (Callable[[str], None]), so the count is intentionally discarded here.
        invalidate_graph_version(workflow_version_id)

    scheduler_stop = asyncio.Event()
    tasks = [
        asyncio.create_task(pg_client.health_check_loop()),
        asyncio.create_task(mongo_client.health_check_loop()),
        asyncio.create_task(orch_redis.health_check_loop()),
        asyncio.create_task(scheduler_loop(scheduler_stop)),
        # Evict locally-cached graphs when another replica/flow_builder signals a
        # republished version. No-op if REDIS_URL is unset.
        asyncio.create_task(
            run_graph_invalidation_listener(REDIS_URL, _invalidate_graph_version)
        ),
    ]

    yield

    scheduler_stop.set()
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
    await orch_redis.close()
    audit.set_sink(None)
    await close_kafka_producer()


def create_app() -> FastAPI:
    app = FastAPI(title="workflow_runtime", version="0.1.0", lifespan=lifespan)
    setup_observability("workflow_runtime", app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Conversation-Id"],
    )

    # /health stays open — Kong's upstream probe and the kubelet's probes
    # both run before any token exists, and it returns nothing but a literal.
    app.include_router(health.router)
    app.include_router(orch_stream.health_router)

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
        reviews.router,
        db_connector.router,
        platform.router,
        billing.router,
        ask_exports.router,
        roi_use_cases.router,
        # Object-storage browsing for Settings (/api/storage, ex minio_service).
        storage.router,
        # Agent / workflow / canvas authoring (ex flow_builder). Kong still
        # routes these paths through its `flow_builder` service entry.
        agents.router,
        workflows.router,
        canvas.router,
        # Orchestrator (multi-agent front door, /api/orchestrate/*) — same
        # process since the merge; its agent calls are in-process too.
        orch_stream.router,
        orch_reviews.router,
        orch_traces.router,
    ):
        app.include_router(r, dependencies=authed)

    return app
