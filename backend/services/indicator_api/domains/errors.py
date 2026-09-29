"""The shared list of business errors (P0-09 section 4).

Each code has one business meaning; screens and the agent branch on `code`,
never on the message text.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ErrorCode(StrEnum):
    NO_DATA_FOR_PERIOD = "NO_DATA_FOR_PERIOD"
    FREQUENCY_NOT_AVAILABLE = "FREQUENCY_NOT_AVAILABLE"
    SERIES_NOT_APPROVED = "SERIES_NOT_APPROVED"
    STALE_SOURCE = "STALE_SOURCE"
    ACCESS_DENIED = "ACCESS_DENIED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    UNRESOLVED_ENTITY = "UNRESOLVED_ENTITY"
    VERSION_CONFLICT = "VERSION_CONFLICT"


# Only 409 for VERSION_CONFLICT is fixed by the story (and invariant 10); the
# rest follow HTTP meaning: 404 for "not there", 403 for permission, 422 for a
# request that is well-formed but cannot be answered as asked.
HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.NO_DATA_FOR_PERIOD: 404,
    ErrorCode.FREQUENCY_NOT_AVAILABLE: 422,
    ErrorCode.SERIES_NOT_APPROVED: 422,
    ErrorCode.STALE_SOURCE: 422,
    ErrorCode.ACCESS_DENIED: 403,
    ErrorCode.INSUFFICIENT_EVIDENCE: 422,
    ErrorCode.UNRESOLVED_ENTITY: 404,
    ErrorCode.VERSION_CONFLICT: 409,
}


class DomainError(Exception):
    """Raised by domain code; the API layer turns it into an ErrorBody response."""

    def __init__(
        self, code: ErrorCode, message: str, *, current_version: int | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.current_version = current_version


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    message: str
    current_version: int | None = None
    """Set on VERSION_CONFLICT so the screen can reload and show what changed."""


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: ErrorDetail
