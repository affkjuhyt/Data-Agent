"""The api's Prometheus metrics (PL-05): every request counted and timed by its route template.

`/metrics` is served beside `/healthz`, outside /v1: Kong routes /v1 only, so it is read inside
the cluster (Prometheus) and never through the gateway. A request no route matched is labelled
`unmatched` rather than by its path, which anyone could make up.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from libs import metrics

SERVICE = "api"
# Not measured: Prometheus' own reads and the probes would drown the requests people make.
SKIPPED = frozenset({"/metrics", "/healthz"})


def route_of(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


async def metrics_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    if request.url.path in SKIPPED:
        return await call_next(request)
    in_flight = metrics.HTTP_IN_FLIGHT.labels(SERVICE)
    in_flight.inc()
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        in_flight.dec()
        # The route is known only once routing ran, inside call_next.
        route = route_of(request)
        metrics.HTTP_SECONDS.labels(SERVICE, request.method, route).observe(
            time.perf_counter() - started
        )
        metrics.HTTP_REQUESTS.labels(
            SERVICE, request.method, route, metrics.status_class(status)
        ).inc()


async def metrics_endpoint() -> Response:
    body, content_type = metrics.exposition()
    return Response(content=body, media_type=content_type)
