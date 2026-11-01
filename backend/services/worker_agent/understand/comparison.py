"""A comparison a question asks for (R1-06 compare_to): "so với tháng trước", "so với cùng kỳ năm
trước", "YoY", "vs last month" (the packs, GEN-31).

Read before the period, and taken out of the question: "nợ xấu tháng 9 so với tháng trước" asks
for September against August, and "tháng trước" left in would be read as the period itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.worker_agent.understand.packs import PACKS
from services.worker_agent.understand.text import norm, words_of


@dataclass(frozen=True)
class FoundComparison:
    compare_to: str
    # The question without the comparison's words, as the person wrote the rest.
    rest: str


def find_comparison(question: str) -> FoundComparison | None:
    text = norm(question)
    for pattern, compare_to in (p for pack in PACKS for p in pack.comparisons):
        if m := pattern.search(text):
            written = words_of(question, m.group(0))
            rest = (
                question.replace(written, " ")
                if written in question
                else text.replace(m.group(0), " ")
            )
            return FoundComparison(compare_to, " ".join(rest.split()))
    return None
