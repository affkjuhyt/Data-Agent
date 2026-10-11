"""A1.5 ETag and If-Match, for every module at once.

Every successful JSON GET carries an ETag: a hash of the body it returns, so it changes whenever
anything the caller can see of the resource changes. A PATCH, a PUT or a POST /…/{id}:{verb}
that sends If-Match is checked against the resource as the same caller would read it now (a GET
of /…/{id} with their headers, run in-process); a mismatch answers 412 precondition_failed before
the handler runs. A write whose resource has no GET route, or that sends no If-Match, is not
checked here: A1 makes If-Match a "should", and a route that requires one (POST
/v1/me/clients/{id}:confirm) checks its own.

The comparison is not a lock: two writers that both read the same ETag can still both pass the
check within milliseconds of each other. Tables where that matters keep their own OCC (D5.3,
invariant "OCC thay cho last-write-wins"); this gives every other resource the lost-update guard
A1 asks for without a version column on each table.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request, Response

from services.api.kernel.problems import Problem, problem_response

_VERB = re.compile(r"^(?P<resource>/v1/.+/[^/:]+):[a-z][a-z-]*$")
_WRITES = ("PATCH", "PUT")
# Read back with the caller's own identity and Data Space, nothing else of the write.
_FORWARDED = ("authorization", "x-data-space", "accept-language")


def etag_of(body: bytes) -> str:
    return f'W/"{hashlib.sha256(body).hexdigest()[:32]}"'


def _resource_of(request: Request) -> str | None:
    path = request.url.path
    if request.method in _WRITES:
        return path if path.startswith("/v1/") else None
    if request.method == "POST":
        match = _VERB.match(path)
        return match.group("resource") if match else None
    return None


def _matches(if_match: str, current: str) -> bool:
    # RFC 9110 If-Match uses the strong comparison, but these tags are all weak hashes of the
    # same representation, so they are compared by value; "*" matches any existing resource.
    wanted = {tag.strip().removeprefix("W/") for tag in if_match.split(",")}
    return "*" in wanted or current.removeprefix("W/") in wanted


async def _current_etag(request: Request, resource: str) -> str | None:
    """The ETag a GET of `resource` would carry for this caller, read through the app itself."""
    scope: dict[str, Any] = {
        **request.scope,
        "method": "GET",
        "path": resource,
        "raw_path": resource.encode(),
        "query_string": b"",
        "headers": [
            (k, v) for k, v in request.scope["headers"] if k.decode().lower() in _FORWARDED
        ],
    }
    for key in ("route", "endpoint", "path_params", "state"):
        scope.pop(key, None)
    status = 0
    etag: str | None = None

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        nonlocal status, etag
        if message["type"] == "http.response.start":
            status = message["status"]
            for key, value in message.get("headers", []):
                if key.decode().lower() == "etag":
                    etag = value.decode()

    await request.app(scope, receive, send)
    return etag if status == 200 else None


async def etag_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    if_match = request.headers.get("If-Match")
    resource = _resource_of(request) if if_match else None
    if if_match and resource:
        current = await _current_etag(request, resource)
        if current is not None and not _matches(if_match, current):
            return problem_response(
                Problem(
                    412,
                    "precondition_failed",
                    "The resource changed since you read it; read it again before writing.",
                )
            )
    response = await call_next(request)
    if (
        request.method != "GET"
        or response.status_code != 200
        or response.media_type == "text/event-stream"
        or response.headers.get("content-type", "").startswith("text/event-stream")
        or "etag" in response.headers
    ):
        return response
    chunks = [chunk async for chunk in response.body_iterator]  # type: ignore[attr-defined]
    content = b"".join(c if isinstance(c, bytes) else c.encode() for c in chunks)
    headers = dict(response.headers)
    headers["ETag"] = etag_of(content)
    return Response(
        content=content,
        status_code=response.status_code,
        headers=headers,
        media_type=response.media_type,
    )
