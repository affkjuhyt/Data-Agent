"""Every error code reads the same on every screen (V3 R0-7, NOP-319).

A screen branches on `code` and renders `next_action`; the sentence comes from
the contract. This snapshot is the review point for that wording: changing a
message or an action without updating it here fails.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from libs.contracts.v1 import (
    HTTP_STATUS,
    NEXT_ACTION,
    USER_MESSAGE_VI,
    ErrorCode,
    NextAction,
    Refusal,
)

pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.req("NOP-319")]

SNAPSHOT = {
    "NO_DATA_FOR_PERIOD": ("Không có dữ liệu trong khoảng thời gian được hỏi.", "rephrase"),
    "FREQUENCY_NOT_AVAILABLE": ("Chỉ số này không có dữ liệu theo tần suất được hỏi.", "rephrase"),
    "SERIES_NOT_APPROVED": (
        "Chuỗi số liệu này chưa được duyệt nên chưa dùng để trả lời.",
        "contact_steward",
    ),
    "STALE_SOURCE": (
        "Nguồn dữ liệu đã quá hạn cập nhật nên chưa dùng để trả lời.",
        "contact_steward",
    ),
    "ACCESS_DENIED": ("Bạn không có quyền xem dữ liệu cần cho câu hỏi này.", "contact_steward"),
    "INSUFFICIENT_EVIDENCE": ("Không đủ bằng chứng để trả lời chắc chắn.", "rephrase"),
    "UNRESOLVED_ENTITY": ("Không xác định được đối tượng được hỏi trong câu hỏi.", "rephrase"),
    "VERSION_CONFLICT": ("Dữ liệu vừa được người khác thay đổi. Hãy tải lại rồi thử lại.", "retry"),
    "AGENT_CONFIG_ERROR": (
        "Không đọc được cấu hình của agent này nên hệ thống chưa trả lời.",
        "retry",
    ),
    "PLANNER_ERROR": ("Hệ thống chưa lập được cách trả lời cho câu hỏi này.", "rephrase"),
    "CONTRACT_MISMATCH": ("Kết quả không đúng dạng câu hỏi yêu cầu.", "rephrase"),
    "NO_JOIN_PATH": (
        "Không tìm được cách nối các bảng dữ liệu cần cho câu hỏi này.",
        "contact_steward",
    ),
    "TOOL_UNAVAILABLE": ("Công cụ cần để trả lời đang tạm thời không dùng được.", "retry"),
}


def test_every_code_has_its_message_action_and_status() -> None:
    assert {c.value: (USER_MESSAGE_VI[c], NEXT_ACTION[c].value) for c in ErrorCode} == SNAPSHOT
    assert set(HTTP_STATUS) == set(ErrorCode)


def test_a_refusal_defaults_to_the_codes_wording_and_may_narrow_it() -> None:
    default = Refusal.of(ErrorCode.PLANNER_ERROR, detail_id="conv-1")
    assert (default.message, default.next_action) == (
        USER_MESSAGE_VI[ErrorCode.PLANNER_ERROR],
        NextAction.REPHRASE,
    )

    narrowed = Refusal.of(
        ErrorCode.AGENT_CONFIG_ERROR,
        message="Agent này chưa được publish.",
        next_action=NextAction.CONTACT_STEWARD,
    )
    assert narrowed.model_dump(mode="json") == {
        "code": "AGENT_CONFIG_ERROR",
        "message": "Agent này chưa được publish.",
        "next_action": "contact_steward",
        "detail_id": "",
    }


def test_an_empty_message_is_refused() -> None:
    with pytest.raises(ValidationError):
        Refusal(code=ErrorCode.PLANNER_ERROR, message="", next_action=NextAction.REPHRASE)
