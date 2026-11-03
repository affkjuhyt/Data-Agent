"""The grain of a cut along time a question asks for (R1-06): "theo tháng", "từng quý", "hằng năm",
"theo tuần", "theo năm tài chính" (GEN-21: on the Data Space's week start and fiscal year); "by
month", "quarterly" in English (understand/packs, GEN-31).

Read before the resolver and taken out of the text, like a comparison: the Data Space's time
dimension is often "Ngày", so "tháng" would otherwise be a cut that names no dimension. "theo tháng 9"
names a period (find_period), not a grain, so a number after the word is left alone.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.worker_agent.understand.packs import PACKS
from services.worker_agent.understand.text import norm, words_of

GRAIN_LABELS = {
    "day": "ngày",
    "week": "tuần",
    "month": "tháng",
    "quarter": "quý",
    "year": "năm",
    "fiscal_quarter": "quý tài chính",
    "fiscal_year": "năm tài chính",
}


@dataclass(frozen=True)
class FoundGrain:
    grain: str
    # The question without the grain's words, as the person wrote the rest.
    rest: str


def find_grain(question: str) -> FoundGrain | None:
    text = norm(question)
    for pack in PACKS:
        for pattern in pack.grains:
            m = pattern.search(text)
            if m is None:
                continue
            following = text[m.end() :].split()[:1]
            if m.group("unit") == "ngay" and following and following[0] not in pack.filler:
                # "theo ngày giao hàng" names a date (the ship date), not a cut by day: a word
                # after "ngày" that is no time or filler word makes it a name.
                continue
            written = words_of(question, m.group(0))
            rest = (
                question.replace(written, " ")
                if written in question
                else text.replace(m.group(0), " ")
            )
            grain = pack.grain_units[m.group("unit")]
            if (
                "fiscal" in pattern.groupindex
                and m.group("fiscal")
                and grain in ("quarter", "year")
            ):
                grain = f"fiscal_{grain}"
            return FoundGrain(grain, " ".join(rest.split()))
    return None
