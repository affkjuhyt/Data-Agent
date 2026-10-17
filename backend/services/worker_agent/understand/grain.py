"""The grain of a cut along time a question asks for (R1-06): "theo tháng", "từng quý", "hằng năm".

Read before the resolver and taken out of the text, like a comparison: the Data Space's time
dimension is often "Ngày", so "tháng" would otherwise be a cut that names no dimension. "theo tháng 9"
names a period (find_period), not a grain, so a number after the word is left alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from services.worker_agent.understand.text import norm, words_of

_UNITS = {"ngay": "day", "thang": "month", "quy": "quarter", "nam": "year"}
_GRAIN = re.compile(r"\b(?:theo|tung|hang|moi)\s+(ngay|thang|quy|nam)\b(?!\s*\d)")
GRAIN_LABELS = {"day": "ngày", "month": "tháng", "quarter": "quý", "year": "năm"}


@dataclass(frozen=True)
class FoundGrain:
    grain: str
    # The question without the grain's words, as the person wrote the rest.
    rest: str


def find_grain(question: str) -> FoundGrain | None:
    text = norm(question)
    m = _GRAIN.search(text)
    if m is None:
        return None
    written = words_of(question, m.group(0))
    rest = question.replace(written, " ") if written in question else text.replace(m.group(0), " ")
    return FoundGrain(_UNITS[m.group(1)], " ".join(rest.split()))
