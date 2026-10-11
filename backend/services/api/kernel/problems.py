"""Errors as application/problem+json (RFC 9457), the shape of tab 4 A1.7.

Every error the api returns goes through here, so a client reads one body
shape whatever failed: our own `Problem` raised in a module, FastAPI's request
validation, a Starlette HTTPException (404 for an unknown path, 405), or an
unexpected exception. `detail` never carries internals (A1.7: no table names,
no data outside the caller's rights); the trace id is what support follows.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import DBAPIError
from starlette.exceptions import HTTPException as StarletteHTTPException

from services.api.kernel.tracing import current_trace_id

logger = logging.getLogger(__name__)

MEDIA_TYPE = "application/problem+json"
TYPE_BASE = "https://docs.dataagent/errors/"

# A1.7's table: the stable code for each status when the raiser gives none.
DEFAULT_CODES: dict[int, str] = {
    400: "invalid_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "invalid_request",
    409: "conflict",
    410: "gone",
    412: "precondition_failed",
    413: "payload_too_large",
    422: "validation_failed",
    423: "period_locked",
    429: "rate_limited",
    503: "source_unavailable",
}

TITLES: dict[str, str] = {
    "invalid_request": "Invalid request",
    "data_space_required": "Data Space required",
    "unauthenticated": "Unauthenticated",
    "forbidden": "Forbidden",
    "policy_denied": "Denied by policy",
    "purpose_mismatch": "Purpose mismatch",
    "not_found": "Not found",
    "conflict": "Conflict",
    "invalid_state": "Invalid state",
    "gone": "Gone",
    "precondition_failed": "Precondition failed",
    "payload_too_large": "Payload too large",
    "validation_failed": "Validation failed",
    "business_rule_violated": "Business rule violated",
    "idempotency_mismatch": "Idempotency key reused with a different body",
    "period_locked": "Period locked",
    "rate_limited": "Rate limited",
    "quota_exceeded": "Quota exceeded",
    "source_unavailable": "Source unavailable",
    "model_unavailable": "Model unavailable",
    "internal_error": "Internal error",
}


class FieldError(BaseModel):
    field: str
    code: str
    message: str


class ProblemBody(BaseModel):
    """The documented error body; modules list it in their route `responses`."""

    type: str
    title: str
    status: int
    code: str
    detail: str | None = None
    trace_id: str | None = None
    errors: list[FieldError] | None = None


class Problem(Exception):
    """Raise from any module to answer with a problem+json body."""

    def __init__(
        self,
        status: int,
        code: str | None = None,
        detail: str | None = None,
        *,
        errors: list[FieldError] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.code = code or DEFAULT_CODES.get(status, "internal_error")
        self.detail = detail
        self.errors = errors
        self.headers = headers or {}
        super().__init__(f"{status} {self.code}: {detail or ''}")


def problem_body(
    status: int, code: str, detail: str | None = None, errors: list[FieldError] | None = None
) -> dict[str, Any]:
    body = ProblemBody(
        type=TYPE_BASE + code,
        title=TITLES.get(code, code.replace("_", " ").capitalize()),
        status=status,
        code=code,
        detail=detail,
        trace_id=current_trace_id(),
        errors=errors,
    )
    return body.model_dump(exclude_none=True)


def problem_response(problem: Problem) -> JSONResponse:
    return JSONResponse(
        problem_body(problem.status, problem.code, problem.detail, problem.errors),
        status_code=problem.status,
        media_type=MEDIA_TYPE,
        headers=problem.headers,
    )


def _field_name(loc: tuple[Any, ...]) -> str:
    # Drop the "body"/"query"/"header" prefix FastAPI puts first; the client
    # knows where it sent the field.
    parts = [str(p) for p in loc[1:]] if len(loc) > 1 else [str(p) for p in loc]
    return ".".join(parts)


async def _on_problem(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, Problem)
    return problem_response(exc)


async def _on_validation(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = [
        FieldError(
            field=_field_name(tuple(e.get("loc", ()))),
            code=str(e.get("type", "invalid")),
            message=str(e.get("msg", "")),
        )
        for e in exc.errors()
    ]
    return problem_response(
        Problem(422, "validation_failed", "The request does not match the schema.", errors=errors)
    )


async def _on_http(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    # Starlette's own detail ("Not Found", "Method Not Allowed") is safe to pass on.
    detail = exc.detail if isinstance(exc.detail, str) else None
    return problem_response(Problem(exc.status_code, None, detail, headers=dict(exc.headers or {})))


# The database is where most business rules live (CHECKs and triggers of backend/schema, tab 3b
# D4), so its refusals are the api's: SQLSTATE → A1.7 status and code.
_SQLSTATE: dict[str, tuple[int, str]] = {
    "23514": (422, "business_rule_violated"),  # CHECK or a trigger's RAISE ... check_violation
    "23505": (409, "conflict"),  # unique
    "23503": (422, "validation_failed"),  # a reference to a row that does not exist
    "23502": (422, "validation_failed"),  # NOT NULL
    "22P02": (400, "invalid_request"),  # malformed value
    "42501": (403, "forbidden"),  # grant or row-level security
    "40001": (409, "conflict"),  # serialisation failure: the client retries
    "40P01": (409, "conflict"),  # deadlock victim
}


def _sqlstate(exc: DBAPIError) -> tuple[str | None, Any]:
    """The SQLSTATE and the driver's exception, whichever adapter wraps it."""
    orig = exc.orig
    # asyncpg's own error (the adapter's __cause__) carries message, constraint_name, table_name.
    for candidate in (getattr(orig, "__cause__", None), orig):
        state = getattr(candidate, "sqlstate", None) or getattr(candidate, "pgcode", None)
        if state:
            return state, candidate
    return None, orig


async def _on_database(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DBAPIError)
    state, driver_error = _sqlstate(exc)
    if state not in _SQLSTATE:
        return await _on_unexpected(_, exc)
    status, code = _SQLSTATE[state]
    # A trigger's own message (RAISE EXCEPTION, no constraint) is written for people and cites
    # its BR; a constraint's message names the table and constraint, which A1.7 keeps private.
    raised = (
        getattr(driver_error, "constraint_name", None) is None
        and getattr(driver_error, "table_name", None) is None
    )
    message = str(getattr(driver_error, "message", "") or "").strip()
    # PostgreSQL's own 42501 ("permission denied for table …", "new row violates row-level security
    # policy for table …") names the table; only a trigger's message is passed on.
    own = not message.startswith(("permission denied", "new row violates"))
    detail = message if raised and own and message and state in ("23514", "42501") else None
    return problem_response(Problem(status, code, detail))


async def _on_unexpected(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error", exc_info=exc)
    return problem_response(
        Problem(500, "internal_error", "Unexpected error; quote the trace_id to support.")
    )


def install_problem_handlers(app: FastAPI) -> None:
    app.add_exception_handler(Problem, _on_problem)
    app.add_exception_handler(RequestValidationError, _on_validation)
    app.add_exception_handler(StarletteHTTPException, _on_http)
    app.add_exception_handler(DBAPIError, _on_database)
    app.add_exception_handler(Exception, _on_unexpected)
