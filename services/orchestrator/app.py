import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

import config
from basemodel.services_databaseconnector.shared_model import RetryConfig
from routers.orchestrate import router as orchestrate_router
from routers.reviews import router as reviews_router
from routers.stream import router as stream_router
from services.database_connector.redis_connector import RedisClient

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Postgres (agent catalog) ──────────────────────────────────────────
    if config.DATABASE_URL:
        from services.database_connector.postgres_connector import client
        client.set_url(config.DATABASE_URL)
        await client.open()
        logger.info("orchestrator DB connected")
    else:
        logger.warning("DATABASE_URL not set — agent catalog will return empty list")

    # ── Redis (planning-state checkpoint cache, see session_cache.py) ──────
    redis_client = RedisClient()
    redis_client.set_url(config.REDIS_URL)
    try:
        await redis_client.open(RetryConfig(count=1))
        logger.info("orchestrator Redis connected")
    except Exception as e:
        logger.warning("Redis unavailable at startup, health loop will retry: %s", e)
    app.state.redis = redis_client
    redis_health_task = asyncio.create_task(redis_client.health_check_loop())

    yield

    redis_health_task.cancel()
    try:
        await redis_health_task
    except asyncio.CancelledError:
        pass
    await redis_client.close()
    if config.DATABASE_URL:
        from services.database_connector.postgres_connector import client
        await client.close()


def create_app() -> FastAPI:
    app = FastAPI(title="GTEL Orchestrator", version="0.1.0", lifespan=lifespan)
    app.include_router(orchestrate_router)
    app.include_router(reviews_router)
    app.include_router(stream_router)
    return app
