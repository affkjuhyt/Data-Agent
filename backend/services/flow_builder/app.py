from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from libs.shared.logger import setup_logging

setup_logging("flow_buider")

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from auth import get_current_user
from db_mongo import close_mongo, init_mongo
from db_pg import close_db, init_db
from routers import agents, canvas, health, workflows
from libs.database_connector.mongo_connector import client as mongo_client
from libs.database_connector.postgres_connector import client as pg_client
from libs.shared.observability import setup_observability


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
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
    app = FastAPI(title="flow_buider", version="0.1.0", lifespan=lifespan)
    setup_observability("flow_buider", app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # /health stays open — Kong's upstream probe and the compose healthcheck
    # both run before any token exists, and it returns nothing but a literal.
    app.include_router(health.router)

    # Authenticated at include time rather than route by route. This service had
    # NO authentication at all and eighteen routes, every one of them taking its
    # tenant from a client header; a per-route dependency is one a nineteenth
    # route can silently forget. Same shape as workflow_runtime's create_app.
    authed = [Depends(get_current_user)]
    for r in (agents.router, workflows.router, canvas.router):
        app.include_router(r, dependencies=authed)

    return app
