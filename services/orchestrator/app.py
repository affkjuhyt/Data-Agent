import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

import config
from routers.orchestrate import router as orchestrate_router

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
    if config.DATABASE_URL:
        from services.database_connector.postgres_connector import client
        await client.close()


def create_app() -> FastAPI:
    app = FastAPI(title="GTEL Orchestrator", version="0.1.0", lifespan=lifespan)
    app.include_router(orchestrate_router)
    return app
