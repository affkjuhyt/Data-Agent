"""api: the synchronous deployable of Data Agent V3 (ADR-28), serving /v1.

Run: `uvicorn services.api.app:app` with backend/ on PYTHONPATH.

Every business module of tab 1 Bảng 7.1 lives here as a package under
modules/, one schema each; the OpenAPI this app generates is the contract
(ADR-48) and catalogue/check.py holds it to tab 4 A2.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from services.api.config import Settings
from services.api.kernel.auth import CapabilityResolver, JwksTokenVerifier, TokenVerifier
from services.api.kernel.db import Databases
from services.api.kernel.problems import Problem, install_problem_handlers, problem_response
from services.api.kernel.tracing import trace_middleware
from services.api.modules import ROUTERS

logger = logging.getLogger(__name__)

API_VERSION = "1.0.0"


class NoCapabilities:
    """Fail closed until the org module resolves capabilities from
    org.role.capabilities: a signed-in user can reach "—" endpoints only."""

    async def capabilities(self, *, org_id: uuid.UUID, user_id: str) -> frozenset[str]:
        return frozenset()


class UnconfiguredVerifier:
    """Without an issuer every token is refused, never accepted unchecked."""

    def claims(self, token: str) -> dict[str, object]:
        raise Problem(503, "source_unavailable", "Authentication is not configured.")


def create_app(
    settings: Settings | None = None,
    *,
    token_verifier: TokenVerifier | None = None,
    capability_resolver: CapabilityResolver | None = None,
    databases: Databases | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    dbs = databases or Databases(settings.db_role_urls)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await dbs.dispose()

    app = FastAPI(
        title="Data Agent API",
        version=API_VERSION,
        openapi_url="/v1/openapi.json",
        docs_url="/v1/docs",
        redoc_url=None,
        # One schema per model; the default splits a model used both ways into
        # "-Input" / "-Output", which the generated TypeScript client would carry.
        separate_input_output_schemas=False,
        lifespan=lifespan,
    )
    if token_verifier is None:
        if settings.oidc_issuer:
            token_verifier = JwksTokenVerifier(
                issuer=settings.oidc_issuer,
                audience=settings.oidc_audience,
                jwks_url=settings.oidc_jwks_url,
            )
        else:
            logger.warning(
                "api | API_OIDC_ISSUER is not set: every request will be refused as unauthenticated"
            )
            token_verifier = UnconfiguredVerifier()
    app.state.token_verifier = token_verifier
    app.state.capability_resolver = capability_resolver or NoCapabilities()
    app.state.databases = dbs

    install_problem_handlers(app)

    @app.middleware("http")
    async def trace_and_contain(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        async def guarded(req: Request) -> Response:
            # Inside the trace scope, so a 500 body still carries its trace_id
            # (the framework's last-resort handler runs after the scope ends).
            try:
                return await call_next(req)
            except Exception:
                logger.exception("api | unhandled error")
                return problem_response(
                    Problem(
                        500, "internal_error", "Unexpected error; quote the trace_id to support."
                    )
                )

        return await trace_middleware(request, guarded)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    for router in ROUTERS:
        app.include_router(router)
    return app


app = create_app()
