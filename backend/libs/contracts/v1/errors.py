"""The shared business errors (NOP-150, extended by V3 R0-7).

Each code has one business meaning; screens and the agent branch on `code`, never
on the message text. Raising them is the owning service's job — the exception
class lives there, only the wire shape lives here.

Every code also carries the Vietnamese sentence a user is shown and the one thing
they can do about it (`NEXT_ACTION`), so a refusal reads the same on every screen
and no screen invents its own wording for a code.
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
    # V3 R0-7: why an agent turn ended without an answer.
    AGENT_CONFIG_ERROR = "AGENT_CONFIG_ERROR"
    PLANNER_ERROR = "PLANNER_ERROR"
    CONTRACT_MISMATCH = "CONTRACT_MISMATCH"
    NO_JOIN_PATH = "NO_JOIN_PATH"
    TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"


class NextAction(StrEnum):
    """What the user can do about a refusal; screens render one control per value."""

    RETRY = "retry"
    REPHRASE = "rephrase"
    CONTACT_STEWARD = "contact_steward"


# 409 for VERSION_CONFLICT is fixed by invariant 10; the rest follow HTTP meaning:
# 404 "not there", 403 permission, 400 a valid request that cannot be answered as
# asked, 502 a model upstream returned something unusable, 503 a dependency this
# service needs is down. Never 422: FastAPI answers a malformed request with 422
# and its own body, and one status must map to one body shape for the frontend's
# generated types.
HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.NO_DATA_FOR_PERIOD: 404,
    ErrorCode.FREQUENCY_NOT_AVAILABLE: 400,
    ErrorCode.SERIES_NOT_APPROVED: 400,
    ErrorCode.STALE_SOURCE: 400,
    ErrorCode.ACCESS_DENIED: 403,
    ErrorCode.INSUFFICIENT_EVIDENCE: 400,
    ErrorCode.UNRESOLVED_ENTITY: 404,
    ErrorCode.VERSION_CONFLICT: 409,
    ErrorCode.AGENT_CONFIG_ERROR: 503,
    ErrorCode.PLANNER_ERROR: 502,
    ErrorCode.CONTRACT_MISMATCH: 400,
    ErrorCode.NO_JOIN_PATH: 400,
    ErrorCode.TOOL_UNAVAILABLE: 503,
}

# Shown to the person who asked. No stack, SQL or internal table name ever goes
# here: the detail is behind `detail_id`, for whoever operates the platform.
USER_MESSAGE_VI: dict[ErrorCode, str] = {
    ErrorCode.NO_DATA_FOR_PERIOD: "Không có dữ liệu trong khoảng thời gian được hỏi.",
    ErrorCode.FREQUENCY_NOT_AVAILABLE: "Chỉ số này không có dữ liệu theo tần suất được hỏi.",
    ErrorCode.SERIES_NOT_APPROVED: "Chuỗi số liệu này chưa được duyệt nên chưa dùng để trả lời.",
    ErrorCode.STALE_SOURCE: "Nguồn dữ liệu đã quá hạn cập nhật nên chưa dùng để trả lời.",
    ErrorCode.ACCESS_DENIED: "Bạn không có quyền xem dữ liệu cần cho câu hỏi này.",
    ErrorCode.INSUFFICIENT_EVIDENCE: "Không đủ bằng chứng để trả lời chắc chắn.",
    ErrorCode.UNRESOLVED_ENTITY: "Không xác định được đối tượng được hỏi trong câu hỏi.",
    ErrorCode.VERSION_CONFLICT: "Dữ liệu vừa được người khác thay đổi. Hãy tải lại rồi thử lại.",
    ErrorCode.AGENT_CONFIG_ERROR: "Không đọc được cấu hình của agent này nên hệ thống chưa trả lời.",
    ErrorCode.PLANNER_ERROR: "Hệ thống chưa lập được cách trả lời cho câu hỏi này.",
    ErrorCode.CONTRACT_MISMATCH: "Kết quả không đúng dạng câu hỏi yêu cầu.",
    ErrorCode.NO_JOIN_PATH: "Không tìm được cách nối các bảng dữ liệu cần cho câu hỏi này.",
    ErrorCode.TOOL_UNAVAILABLE: "Công cụ cần để trả lời đang tạm thời không dùng được.",
}

NEXT_ACTION: dict[ErrorCode, NextAction] = {
    ErrorCode.NO_DATA_FOR_PERIOD: NextAction.REPHRASE,
    ErrorCode.FREQUENCY_NOT_AVAILABLE: NextAction.REPHRASE,
    ErrorCode.SERIES_NOT_APPROVED: NextAction.CONTACT_STEWARD,
    ErrorCode.STALE_SOURCE: NextAction.CONTACT_STEWARD,
    ErrorCode.ACCESS_DENIED: NextAction.CONTACT_STEWARD,
    ErrorCode.INSUFFICIENT_EVIDENCE: NextAction.REPHRASE,
    ErrorCode.UNRESOLVED_ENTITY: NextAction.REPHRASE,
    ErrorCode.VERSION_CONFLICT: NextAction.RETRY,
    ErrorCode.AGENT_CONFIG_ERROR: NextAction.RETRY,
    ErrorCode.PLANNER_ERROR: NextAction.REPHRASE,
    ErrorCode.CONTRACT_MISMATCH: NextAction.REPHRASE,
    ErrorCode.NO_JOIN_PATH: NextAction.CONTACT_STEWARD,
    ErrorCode.TOOL_UNAVAILABLE: NextAction.RETRY,
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


class Refusal(ContractModel):
    """Why an agent turn ended without an answer — the `__refusal` stream frame.

    `message` and `next_action` default to the code's entry in USER_MESSAGE_VI /
    NEXT_ACTION; a producer narrows them only when it knows a more precise case
    of the same code (an agent that was never published is AGENT_CONFIG_ERROR,
    but retrying will not publish it). `detail_id` finds the turn's trace.
    """

    code: ErrorCode
    message: NonEmptyStr
    next_action: NextAction
    detail_id: str = ""

    @classmethod
    def of(
        cls,
        code: ErrorCode,
        *,
        detail_id: str = "",
        message: str | None = None,
        next_action: NextAction | None = None,
    ) -> Refusal:
        return cls(
            code=code,
            message=message or USER_MESSAGE_VI[code],
            next_action=next_action or NEXT_ACTION[code],
            detail_id=detail_id,
        )
