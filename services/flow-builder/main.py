from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.router.flow_builder.agent import router as agent_router
from app.router.flow_builder.workflow import router as workflow_router
from app.router.flow_builder.canvas import router as canvas_router

from services.database_connector.postgres_connector import client as pg_client
from services.database_connector.mongo_connector import client as mongo_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pg_url = os.environ.get("DATABASE_URL", "")
    if pg_url:
        pg_client.set_url(pg_url)
        await pg_client.open()

    mongo_url = os.environ.get("MONGODB_URL", "mongodb://localhost:27017/dataagent")
    mongo_client.set_url(mongo_url)
    await mongo_client.open()

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

    await mongo_client.close()
    await pg_client.close()


app = FastAPI(title="flow-builder", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agent_router, prefix="/api")
app.include_router(workflow_router, prefix="/api")
app.include_router(canvas_router, prefix="/api")


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.1.0"}
