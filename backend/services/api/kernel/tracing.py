"""The request's trace id, for problem bodies and logs (A1.7, ADR-40).

ADR-40 carries the trace across gateway, api, workers and model calls in the W3C
`traceparent` header, so the id a client quotes from an error body is the one
Tempo and Langfuse hold. A request without the header (a direct call in
development) still gets an id, so every error stays quotable.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from starlette.requests import Request
from starlette.responses import Response

_trace_id: ContextVar[str | None] = ContextVar("api_trace_id", default=None)

# version-traceid-parentid-flags; an all-zero trace id is invalid per the spec.
_TRACEPARENT = re.compile(r"^[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$")


def current_trace_id() -> str | None:
    return _trace_id.get()


def trace_id_from(traceparent: str | None) -> str:
    match = _TRACEPARENT.match((traceparent or "").strip().lower())
    if match and match.group(1) != "0" * 32:
        return match.group(1)
    return secrets.token_hex(16)


async def trace_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    token = _trace_id.set(trace_id_from(request.headers.get("traceparent")))
    try:
        return await call_next(request)
    finally:
        _trace_id.reset(token)
