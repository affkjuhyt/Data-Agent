from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())

import asyncio
import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.router.workflow_runtime.conversation import router as conversation_router
from app.router.workflow_runtime.config import router as config_router

from app.pipeline.workflow_runtime.services.memory import init_qdrant, close_qdrant
from services.database_connector.postgres_connector import client as pg_client
from services.database_connector.mongo_connector import client as mongo_client

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pg_url = os.environ.get("RUNTIME_DB_URL") or os.environ.get("DATABASE_URL")
    if pg_url:
        pg_client.set_url(pg_url)
        await pg_client.open()

    mongo_url = os.environ.get("MONGODB_URL", "mongodb://localhost:27017/dataagent")
    mongo_client.set_url(mongo_url)
    await mongo_client.open()

    await init_qdrant()

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

    await close_qdrant()
    await mongo_client.close()
    await pg_client.close()


app = FastAPI(title="workflow-runtime", version="0.1.0", lifespan=lifespan)

_CORS_ORIGINS = [
    o.strip()
    for o in os.environ.get("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Conversation-Id"],
)

app.include_router(conversation_router, prefix="/api")
app.include_router(config_router, prefix="/api")


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.1.0"}
