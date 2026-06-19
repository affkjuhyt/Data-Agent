from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

import asyncio
from contextlib import asynccontextmanager

from services.log_set_up.logger import setup_logging

setup_logging("flow-builder")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from db_mongo import close_mongo, init_mongo
from db_pg import close_db, init_db
from routers import agents, canvas, health, workflows
from services.database_connector.mongo_connector import client as mongo_client
from services.database_connector.postgres_connector import client as pg_client
from services.shared.observability import setup_observability


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await init_mongo()

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
    await close_db()


def create_app() -> FastAPI:
    app = FastAPI(title="flow-builder", version="0.1.0", lifespan=lifespan)
    setup_observability("flow-builder", app)

    setup_tracing("flow-builder", app=app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(agents.router)
    app.include_router(workflows.router)
    app.include_router(canvas.router)

    return app
