from fastapi import FastAPI

from release_core import enforce_internal_origin
from routers import admin, drift, health, pipelines
from shared.observability import instrument_fastapi


def create_app() -> FastAPI:
    app = FastAPI(title="Release Worker API", version="1.0.0")

    instrument_fastapi(app)
    app.middleware("http")(enforce_internal_origin)

    app.include_router(health.router)
    app.include_router(pipelines.router)
    app.include_router(drift.router)
    app.include_router(admin.router)

    return app
