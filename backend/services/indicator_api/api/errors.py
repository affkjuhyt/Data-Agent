"""Turns a DomainError into the shared error body."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from services.indicator_api.domains.errors import HTTP_STATUS, DomainError, ErrorBody, ErrorDetail

# Declared on every route so the error body is in openapi.json and the screens
# get its type generated like any other response (P0-09 AC-5).
ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    status: {"model": ErrorBody} for status in sorted(set(HTTP_STATUS.values()))
}


async def domain_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)
    body = ErrorBody(
        error=ErrorDetail(code=exc.code, message=exc.message, current_version=exc.current_version)
    )
    return JSONResponse(
        status_code=HTTP_STATUS[exc.code], content=body.model_dump(mode="json", exclude_none=True)
    )
