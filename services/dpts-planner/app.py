from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

import asyncio
from contextlib import asynccontextmanager

from services.log_set_up.logger import setup_logging

setup_logging("dpts-planner")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import config
import plan_db
import plan_index
import sweeper
from basemodel.services_databaseconnector.shared_model import RetryConfig
from routers import health, planner
from services.database_connector.model_connector import client as model_client
from services.database_connector.mongo_connector import client as mongo_client
from services.shared.observability import setup_observability


@asynccontextmanager
async def lifespan(app: FastAPI):
    await plan_db.init_plan_db()         # plan DB (Mongo)
    await plan_index.init_plan_index()   # Cached Plan search (Elasticsearch)

    # Model service (infra/model-service /generate -> vLLM). Best-effort: a miss
    # falls back to MockLLM so the planner still serves.
    if config.MODEL_SERVICE_URL and not config.USE_MOCK_EXPANDER:
        model_client.set_url(config.MODEL_SERVICE_URL)
        try:
            await model_client.open(RetryConfig(count=3))
        except Exception:  # noqa: BLE001
            pass

    tasks = [
        asyncio.create_task(mongo_client.health_check_loop()),
        asyncio.create_task(sweeper.sweep_loop()),     # TTL: erase expired plans (Mongo + ES)
    ]

    yield

    for task in tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    await model_client.close()
    await plan_index.close_plan_index()
    await plan_db.close_plan_db()


def create_app() -> FastAPI:
    app = FastAPI(title="dpts-planner", version="0.1.0", lifespan=lifespan)
    setup_observability("dpts-planner", app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(planner.router)

    return app
