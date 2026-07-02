import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

import config
from executor.agent_client import close_shared_client
from routers.reviews import router as reviews_router
from routers.stream import router as stream_router
from routers.traces import router as traces_router
from services.shared.observability import setup_observability

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    if config.DATABASE_URL:
        from services.database_connector.postgres_connector import client
        client.set_url(config.DATABASE_URL)
        await client.open()
        logger.info("orchestrator DB connected")
    else:
        logger.warning("DATABASE_URL not set — agent catalog will return empty list")
    yield
    # shutdown
    await close_shared_client()
    if config.DATABASE_URL:
        from services.database_connector.postgres_connector import client
        await client.close()


def create_app() -> FastAPI:
    app = FastAPI(title="GTEL Orchestrator", version="0.1.0", lifespan=lifespan)
    # OTEL tracing (FastAPI + httpx auto-instrument) — traceparent header tự
    # inject khi agent_client.py gọi workflow-runtime, và tự extract phía bên
    # kia. Đây là nền cho việc nối 1 trace Langfuse xuyên 2 service.
    setup_observability("orchestrator", app)
    app.include_router(reviews_router)
    app.include_router(stream_router)
    app.include_router(traces_router)
    return app
