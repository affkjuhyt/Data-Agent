"""A comparison a question asks for (R1-06 compare_to): "so với tháng trước", "so với cùng kỳ năm
trước", "YoY".

Read before the period, and taken out of the question: "nợ xấu tháng 9 so với tháng trước" asks
for September against August, and "tháng trước" left in would be read as the period itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from services.worker_agent.semantic.compare import PREVIOUS_PERIOD, SAME_PERIOD_LAST_YEAR
from services.worker_agent.understand.text import norm, words_of

_AGAINST = r"so voi"
_PATTERNS = (
    (
        re.compile(r"\b(?:so voi |so )?cung (?:ky|thoi diem)(?: (?:nam truoc|nam ngoai))?\b"),
        SAME_PERIOD_LAST_YEAR,
    ),
    (re.compile(r"\b(?:yoy|y o y)\b"), SAME_PERIOD_LAST_YEAR),
    (
        re.compile(
            rf"\b{_AGAINST} (?:(?:thang|quy|nam|ky|tuan|ngay) (?:truoc|lien truoc)"
            r"|nam ngoai|hom qua)\b"
        ),
        PREVIOUS_PERIOD,
    ),
    (re.compile(r"\b(?:mom|qoq|m o m|q o q)\b"), PREVIOUS_PERIOD),
)


@dataclass(frozen=True)
class FoundComparison:
    compare_to: str
    # The question without the comparison's words, as the person wrote the rest.
    rest: str


def find_comparison(question: str) -> FoundComparison | None:
    text = norm(question)
    for pattern, compare_to in _PATTERNS:
        if m := pattern.search(text):
            written = words_of(question, m.group(0))
            rest = (
                question.replace(written, " ")
                if written in question
                else text.replace(m.group(0), " ")
            )
            return FoundComparison(compare_to, " ".join(rest.split()))
    return None
