"""indicator-api: the V2 data / knowledge / governance API (NOP-162).

Run: `uvicorn services.indicator_api.app:app` with backend/ on PYTHONPATH.
"""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import FastAPI

from libs.contracts import CONTRACT_VERSION
from libs.shared.observability import setup_observability
from services.indicator_api import health
from services.indicator_api.adapters import Adapter, configured_adapters
from services.indicator_api.api.routers import capability, governance, observation


def create_app(adapters: Sequence[Adapter] | None = None) -> FastAPI:
    app = FastAPI(
        title="indicator-api",
        version=CONTRACT_VERSION,
        # One schema per model, named as in Contract v1's published components;
        # the default splits a model used both ways into "-Input" / "-Output".
        separate_input_output_schemas=False,
    )
    setup_observability("indicator-api", app)
    app.state.adapters = list(configured_adapters() if adapters is None else adapters)
    app.include_router(health.router)
    app.include_router(observation.router)
    app.include_router(governance.router)
    app.include_router(capability.router)
    return app


app = create_app()
