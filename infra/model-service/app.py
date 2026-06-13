import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

import config as cfg
import models
import registry
from routers import embed, health, rerank

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    models.load(cfg.EMBED_MODEL, cfg.RERANK_MODEL)
    # Register this model in Postgres in the background — Postgres may not be
    # ready yet, so the retry loop runs independently without blocking serving.
    asyncio.create_task(registry.register_with_retry(), name="model-registry")
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="model-service", lifespan=lifespan)

    app.include_router(health.router)
    app.include_router(embed.router)
    app.include_router(rerank.router)

    return app


if __name__ == "__main__":
    uvicorn.run("app:create_app", factory=True, host="0.0.0.0", port=cfg.PORT)
