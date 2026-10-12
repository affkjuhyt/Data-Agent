"""api: the synchronous deployable of Data Agent V3 (ADR-28), serving /v1.

Run: `uvicorn services.api.app:app` with backend/ on PYTHONPATH.

Every business module of tab 1 Bảng 7.1 lives here as a package under
modules/, one schema each; the OpenAPI this app generates is the contract
(ADR-48) and catalogue/check.py holds it to tab 4 A2.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

from services.api.config import Settings
from services.api.kernel.access_log import access_log_middleware
from services.api.kernel.auth import IdentityResolver, JwksTokenVerifier, TokenVerifier
from services.api.kernel.etag import etag_middleware
from services.api.kernel.idempotency import (
    IdempotentAnswer,
    ResponseStore,
    idempotency_middleware,
    on_idempotent_answer,
    response_store_from_env,
)
from services.api.kernel.problems import Problem, install_problem_handlers, problem_response
from services.api.kernel.ratelimit import (
    CounterStore,
    counter_store_from_env,
    per_minute,
    rate_limit_middleware,
)
from services.api.kernel.tracing import trace_middleware
from services.api.modules import ROUTERS
from services.api.modules.org import OrgIdentityResolver

logger = logging.getLogger(__name__)

API_VERSION = "1.0.0"


class UnconfiguredVerifier:
    """Without an issuer every token is refused, never accepted unchecked."""

    def claims(self, token: str) -> dict[str, object]:
        raise Problem(503, "source_unavailable", "Authentication is not configured.")


def create_app(
    settings: Settings | None = None,
    *,
    token_verifier: TokenVerifier | None = None,
    identity_resolver: IdentityResolver | None = None,
    response_store: ResponseStore | None = None,
    counter_store: CounterStore | None = None,
    rate_limit: int | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()

    app = FastAPI(
        title="Data Agent API",
        version=API_VERSION,
        openapi_url="/v1/openapi.json",
        docs_url="/v1/docs",
        redoc_url=None,
        # One schema per model; the default splits a model used both ways into
        # "-Input" / "-Output", which the generated TypeScript client would carry.
        separate_input_output_schemas=False,
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
    app.state.identity_resolver = identity_resolver or OrgIdentityResolver()

    install_problem_handlers(app)
    app.add_exception_handler(IdempotentAnswer, on_idempotent_answer)
    store = response_store or response_store_from_env()

    @app.middleware("http")
    async def idempotency(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        return await idempotency_middleware(request, call_next, store)

    # Outside the idempotency check: a stale If-Match answers 412 before a key is claimed.
    app.middleware("http")(etag_middleware)

    counters = counter_store or counter_store_from_env()
    limit = rate_limit or per_minute()

    # Outside ETag and idempotency: a caller over the limit claims nothing and reads nothing.
    @app.middleware("http")
    async def rate_limited(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        return await rate_limit_middleware(request, call_next, counters, limit)

    # Outermost but tracing: it logs the status every inner layer settled on, 429 and 412 too.
    app.middleware("http")(access_log_middleware)

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
