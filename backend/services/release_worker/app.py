from fastapi import FastAPI

from libs.shared.observability import instrument_fastapi
from services.release_worker.release_core import enforce_internal_origin
from services.release_worker.routers import health, pipelines


def create_app() -> FastAPI:
    app = FastAPI(title="Release Worker API", version="1.0.0")

    instrument_fastapi(app)
    app.middleware("http")(enforce_internal_origin)

    app.include_router(health.router)
    app.include_router(pipelines.router)

    return app
