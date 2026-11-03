"""Sentences addressed to the model rather than to a reader (UC-05-02 E2, AGC-03).

A pure leaf like the rest of libs.pii: no I/O, so the api can screen a question before it is
stored and worker-document can flag a passage before it is indexed, with the same patterns.

Two strengths, because the two readers can afford different mistakes:

  * `overrides_instructions`: a sentence that tries to replace the model's instructions ("bỏ qua
    mọi hướng dẫn", "ignore previous instructions", "system prompt", "you are now a ..."). Nobody
    asks a data question that way, so a question that says it is refused.
  * `injection_suspected`: those, plus a request to dump everything ("xuất toàn bộ dữ liệu").
    A passage of a document has no business saying that; a question may ("xuất toàn bộ dữ liệu
    doanh thu 2013" is a plain request), so questions are not screened for it.
"""

from __future__ import annotations

import re

_OVERRIDE = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:bỏ qua|phớt lờ|quên)\s+(?:mọi|tất cả|toàn bộ|các)?\s*(?:hướng dẫn|chỉ dẫn|lệnh|quy tắc)",
        r"\bignore\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above)\s+(?:instructions|prompts?|rules)",
        r"\bdisregard\s+(?:all\s+|the\s+)?(?:previous|prior|above)\b",
        r"\b(?:system prompt|lời nhắc hệ thống)\b",
        r"\b(?:bạn là|you are now)\s+(?:một\s+)?(?:trợ lý|mô hình|assistant|model)\b",
    )
]
_DUMP = re.compile(
    r"\b(?:trả|xuất|gửi|in ra)\s+(?:về\s+)?toàn bộ\s+(?:dữ liệu|thông tin)", re.IGNORECASE
)


def overrides_instructions(text: str) -> bool:
    return any(pattern.search(text) for pattern in _OVERRIDE)


def injection_suspected(text: str) -> bool:
    return overrides_instructions(text) or bool(_DUMP.search(text))
