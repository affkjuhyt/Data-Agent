"""What the rule pass reads of a question: its text with what would be misread taken out first.

"so với tháng trước" (R1-06), a knowledge mode's words, "theo tháng", "tỷ trọng"/"luỹ kế" and
"giá trị trên 1 tỷ" (GEN-22) each say how to read the metric, not which metric or which period:
left in, "tháng trước" would be read as the period asked and "1 tỷ" as nothing. They are found
and taken out in this order, and `understand` reads what is left. The governed path
(questions._understand_and_plan) and the router (router.py, AE-01) read a question this one way,
so the router never decides on a reading the answer does not use.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from services.worker_agent.semantic.loader import SpaceLayer
from services.worker_agent.understand.analysis import FoundAnalysis, find_analysis
from services.worker_agent.understand.comparison import FoundComparison, find_comparison
from services.worker_agent.understand.condition import (
    FoundConditions,
    find_conditions,
    numeric_dimensions,
)
from services.worker_agent.understand.grain import FoundGrain, find_grain
from services.worker_agent.understand.text import find_knowledge_hint, norm


@dataclass(frozen=True)
class Asked:
    text: str
    comparison: FoundComparison | None
    grain: FoundGrain | None
    analysis: FoundAnalysis | None
    conditions: FoundConditions | None


def asked_of(question: str, layer: SpaceLayer) -> Asked:
    comparison = find_comparison(question)
    asked = comparison.rest if comparison is not None else question
    hint = find_knowledge_hint(question)
    if hint is not None:
        asked = re.sub(rf"\b{re.escape(hint.span)}\b", " ", norm(asked), count=1)
    grain = find_grain(asked)
    if grain is not None:
        asked = grain.rest
    analysis = find_analysis(asked)
    if analysis is not None:
        asked = analysis.rest
    conditions = find_conditions(asked, numeric_dimensions(layer.layer.dimensions))
    if conditions is not None:
        asked = conditions.rest
    return Asked(asked, comparison, grain, analysis, conditions)
