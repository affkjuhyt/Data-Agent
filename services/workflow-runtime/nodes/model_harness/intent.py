"""Analysis-type taxonomy and the capability policy that acts on it.

The planner classified every question as `question` or `task` and nothing else.
That distinction only ever drove whether the responder appended an options
block, so the system could not tell a one-fact lookup from a trend analysis,
and — the part that matters — could not tell either of them from a request to
**forecast** or to **find anomalies**, neither of which this platform can do.

There is no forecasting model here and no anomaly detection. Asked "doanh thu
quý sau bao nhiêu?", the old path handed the question to the responder, which
answered from the model's own priors dressed in whatever context retrieval
happened to return. A number arrived, it looked like the others, and nothing
marked it as invented. Classifying the request is what makes refusing to invent
it possible.

`analysis_type` is deliberately ORTHOGONAL to `intent`, not a replacement:
"vẽ cho tôi biểu đồ so sánh doanh thu 2 quý" is a task AND a comparison. Folding
them into one field would force a false choice and silently change the
responder's existing behaviour.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AnalysisType(str, Enum):
    """What KIND of analysis the question asks for.

    Closed set. An open string field becomes six spellings of the same thing and
    nothing downstream can branch on it reliably.
    """

    # A single fact, retrieved not computed: "quy định nghỉ phép là gì?"
    LOOKUP = "lookup"
    # Aggregation, breakdown or trend over data: "doanh thu theo tháng".
    ANALYTICAL = "analytical"
    # Two or more things explicitly contrasted — periods, regions, segments.
    # Separate from ANALYTICAL because a comparison that reports only one side
    # is not a partial answer, it is a misleading one.
    COMPARISON = "comparison"
    # Anything about the future: predict, project, estimate, "quý sau".
    FORECAST = "forecast"
    # "What is unusual/wrong/unexpected here" — outliers, spikes, drops.
    ANOMALY = "anomaly"
    # Greeting, chit-chat, meta-questions about the assistant itself.
    OTHER = "other"


def parse(raw: object) -> AnalysisType:
    """Coerce a model-supplied value, defaulting to OTHER.

    OTHER rather than LOOKUP is the safe default: LOOKUP would assert the
    question is answerable from a single retrieved fact, which is a claim, while
    OTHER asserts nothing and leaves the answer shaping untouched.
    """
    try:
        return AnalysisType(str(raw).strip().lower())
    except (ValueError, AttributeError):
        return AnalysisType.OTHER


# ── capability policy ──────────────────────────────────────────────────────
# Deterministic, not model-judged. Whether this platform can forecast is a fact
# about the platform, so it is decided here and not re-litigated per turn by the
# thing that would most like to answer anyway.
#
# Flip an entry to True only when a real implementation lands — a forecasting
# node, an anomaly detector — not when the answers merely start looking
# plausible.
_SUPPORTED: dict[AnalysisType, bool] = {
    AnalysisType.LOOKUP: True,
    AnalysisType.ANALYTICAL: True,
    AnalysisType.COMPARISON: True,
    # No forecasting model exists in this platform. Retrieval can still surface
    # a forecast someone else published in a document, and reporting THAT is
    # legitimate — inventing one is not. The distinction is what the guidance
    # below tells the responder to hold.
    AnalysisType.FORECAST: False,
    # No anomaly detection, no baselines, no statistical thresholds. Same
    # distinction: report an anomaly the data states, do not declare one.
    AnalysisType.ANOMALY: False,
    AnalysisType.OTHER: True,
}

_UNSUPPORTED_REASON: dict[AnalysisType, str] = {
    AnalysisType.FORECAST: (
        "Hệ thống chưa có mô hình dự báo. Chỉ được nêu lại số liệu/dự báo đã có sẵn "
        "trong nguồn dữ liệu và ghi rõ nguồn; TUYỆT ĐỐI không tự đưa ra con số dự báo."
    ),
    AnalysisType.ANOMALY: (
        "Hệ thống chưa có cơ chế phát hiện bất thường (chưa có baseline, ngưỡng hay "
        "kiểm định thống kê). Chỉ được mô tả những gì dữ liệu thể hiện và ghi rõ đây "
        "KHÔNG phải kết luận bất thường đã được kiểm chứng."
    ),
}


@dataclass(frozen=True)
class Capability:
    supported: bool
    reason: str = ""


def capability(analysis_type: AnalysisType) -> Capability:
    """Can the platform actually serve this kind of request?"""
    supported = _SUPPORTED.get(analysis_type, True)
    return Capability(supported=supported, reason="" if supported else _UNSUPPORTED_REASON.get(analysis_type, ""))


# ── answer shaping ─────────────────────────────────────────────────────────
# What a good answer looks like differs per type, and saying so is cheap. These
# are appended to the responder's system prompt; they constrain the SHAPE of the
# answer, never its content.
_GUIDANCE: dict[AnalysisType, str] = {
    AnalysisType.LOOKUP: (
        "Đây là câu hỏi TRA CỨU: trả lời ngắn gọn, đúng một thông tin được hỏi, "
        "kèm trích dẫn nguồn. Không mở rộng sang phân tích không được yêu cầu."
    ),
    AnalysisType.ANALYTICAL: (
        "Đây là câu hỏi PHÂN TÍCH: nêu rõ số liệu, đơn vị, khoảng thời gian và các "
        "bộ lọc đã áp dụng. Nếu không rõ dữ liệu đã được tính theo tiêu chí nào, "
        "phải nói rõ điều đó thay vì suy đoán."
    ),
    AnalysisType.COMPARISON: (
        "Đây là câu hỏi SO SÁNH: phải nêu ĐẦY ĐỦ các vế được so sánh cùng mức chênh "
        "lệch. Nếu chỉ có dữ liệu của một vế, phải nói rõ là thiếu vế còn lại — "
        "KHÔNG được trình bày một vế như thể đã trả lời xong câu hỏi."
    ),
}


def guidance(analysis_type: AnalysisType) -> str:
    """Answer-shaping instruction for this type, or "" when there is nothing
    type-specific to say."""
    capability_check = capability(analysis_type)
    if not capability_check.supported:
        return (
            f"GIỚI HẠN NĂNG LỰC — {capability_check.reason}\n"
            "Nêu rõ giới hạn này trong câu trả lời."
        )
    return _GUIDANCE.get(analysis_type, "")
