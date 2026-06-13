import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

import config as cfg
import parser as doc_parser
from routers import health, parse

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    doc_parser.load_converter(use_vlm=cfg.USE_VLM)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="docling-service", lifespan=lifespan)

    app.include_router(health.router)
    app.include_router(parse.router)

    return app


if __name__ == "__main__":
    uvicorn.run("app:create_app", factory=True, host="0.0.0.0", port=cfg.PORT)
