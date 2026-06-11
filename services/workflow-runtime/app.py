from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import CORS_ORIGINS
from db import close_db, init_db
from memory_middleware import close_qdrant, init_qdrant
from redis_cache import init_redis, close_redis
from mongo_client import close_mongo, init_mongo
from routers import catalog, conversations, health, traces
from services.database_connector.mongo_connector import client as mongo_client
from services.database_connector.postgres_connector import client as pg_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await init_qdrant()
    await init_mongo()
    await init_redis()

    tasks = [
        asyncio.create_task(pg_client.health_check_loop()),
        asyncio.create_task(mongo_client.health_check_loop()),
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
    await close_db()
    await close_redis()


def create_app() -> FastAPI:
    app = FastAPI(title="workflow-runtime", version="0.1.0", lifespan=lifespan)

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

    return app
