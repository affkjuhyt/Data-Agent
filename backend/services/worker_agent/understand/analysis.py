"""An analysis a question asks for over its metric (semantic/analysis.py): a share of the total
("tỷ trọng", "cơ cấu", "chiếm bao nhiêu %") or an accumulation ("luỹ kế", "cộng dồn", "từ đầu
năm", "YTD").

Read before the period and taken out of the question, like a comparison: "doanh thu luỹ kế từ đầu
năm đến tháng 9" asks for January to September, and "từ đầu năm" left in would be read as a period
of its own. A bare "luỹ kế" is from the start of the year, as Vietnamese reports use it ("doanh thu
luỹ kế tháng 9" is the year to September). The words are each language pack's (GEN-31): "share
of total", "year to date", "running total" in English.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.worker_agent.understand.packs import PACKS
from services.worker_agent.understand.packs.base import TO_DATE
from services.worker_agent.understand.text import norm, words_of

__all__ = ["TO_DATE", "FoundAnalysis", "find_analysis"]


@dataclass(frozen=True)
class FoundAnalysis:
    kind: str  # SHARE_OF_TOTAL, TO_DATE or RUNNING_TOTAL
    # TO_DATE: the year, quarter or month it runs from the start of.
    unit: str | None
    # The question without the analysis's words, as the person wrote the rest.
    rest: str
    # A weak colloquial phrase may describe the ratio metric itself, not a share-of-total.
    weak: bool = False


def find_analysis(question: str) -> FoundAnalysis | None:
    text = norm(question)
    for pack in PACKS:
        for pattern, kind, unit, weak in pack.analyses:
            m = pattern.search(text)
            if m is None:
                continue
            if unit == "":
                unit = pack.analysis_units[m.group(1)]
            if weak:
                # "chiếm bao nhiêu %" of a ratio is the ratio itself: the words stay in the
                # question, so the metric is still read from them; it becomes a share only if the
                # metric adds up.
                return FoundAnalysis(kind, unit, question, weak)
            written = words_of(question, m.group(0))
            rest = (
                question.replace(written, " ")
                if written in question
                else text.replace(m.group(0), " ")
            )
            return FoundAnalysis(kind, unit, " ".join(rest.split()), weak)
    return None
