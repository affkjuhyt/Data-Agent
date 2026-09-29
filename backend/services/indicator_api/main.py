"""FastAPI entrypoint of indicator-api (P0-09 stub)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from libs.shared.event_envelope import EventEnvelope
from services.indicator_api.api.errors import domain_error_handler
from services.indicator_api.api.routers import ALL_ROUTERS
from services.indicator_api.domains.catalog import contract_models
from services.indicator_api.domains.errors import DomainError, ErrorBody
from services.indicator_api.stub.store import StubStore

OPENAPI_VERSION = "0.1.0"


def _openapi(app: FastAPI) -> dict[str, Any]:
    """The route schema plus every contract model, used by a route or not.

    FastAPI only emits models a route references, but the screens generate their
    types from this file for every business object (P0-09 AC-5), and the
    event envelope never appears on a route at all.
    """
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
        separate_input_output_schemas=app.separate_input_output_schemas,
    )
    extra: list[type[BaseModel]] = [*contract_models(), EventEnvelope, ErrorBody]
    _, defs = models_json_schema(
        [(model, "validation") for model in extra], ref_template="#/components/schemas/{model}"
    )
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    for name, model_schema in defs.get("$defs", {}).items():
        components.setdefault(name, model_schema)
    app.openapi_schema = schema
    return schema


def create_app() -> FastAPI:
    # One schema per model instead of Foo-Input / Foo-Output pairs: the screens
    # read and send the same shapes, and one name per object keeps the
    # contract test's per-model version check simple.
    app = FastAPI(
        title="indicator-api", version=OPENAPI_VERSION, separate_input_output_schemas=False
    )
    app.state.store = StubStore()
    app.add_exception_handler(DomainError, domain_error_handler)
    for router in ALL_ROUTERS:
        app.include_router(router)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.openapi = lambda: _openapi(app)  # type: ignore[method-assign]
    return app


app = create_app()
