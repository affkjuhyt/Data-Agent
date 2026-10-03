"""The shared business errors (NOP-150).

Each code has one business meaning; screens and the agent branch on `code`, never
on the message text. Raising them is the owning service's job — the exception
class lives there, only the wire shape lives here.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from libs.contracts.v1.base import ContractModel, NonEmptyStr


class ErrorCode(StrEnum):
    NO_DATA_FOR_PERIOD = "NO_DATA_FOR_PERIOD"
    FREQUENCY_NOT_AVAILABLE = "FREQUENCY_NOT_AVAILABLE"
    SERIES_NOT_APPROVED = "SERIES_NOT_APPROVED"
    STALE_SOURCE = "STALE_SOURCE"
    ACCESS_DENIED = "ACCESS_DENIED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    UNRESOLVED_ENTITY = "UNRESOLVED_ENTITY"
    VERSION_CONFLICT = "VERSION_CONFLICT"


# 409 for VERSION_CONFLICT is fixed by invariant 10; the rest follow HTTP meaning:
# 404 "not there", 403 permission, 400 a valid request that cannot be answered as
# asked. Never 422: FastAPI answers a malformed request with 422 and its own body,
# and one status must map to one body shape for the frontend's generated types.
HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.NO_DATA_FOR_PERIOD: 404,
    ErrorCode.FREQUENCY_NOT_AVAILABLE: 400,
    ErrorCode.SERIES_NOT_APPROVED: 400,
    ErrorCode.STALE_SOURCE: 400,
    ErrorCode.ACCESS_DENIED: 403,
    ErrorCode.INSUFFICIENT_EVIDENCE: 400,
    ErrorCode.UNRESOLVED_ENTITY: 404,
    ErrorCode.VERSION_CONFLICT: 409,
}


class ErrorDetail(ContractModel):
    code: ErrorCode
    message: NonEmptyStr
    current_version: int | None = Field(
        default=None,
        ge=1,
        description="Set on VERSION_CONFLICT so the client can reload and show what changed",
    )
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorBody(ContractModel):
    error: ErrorDetail
