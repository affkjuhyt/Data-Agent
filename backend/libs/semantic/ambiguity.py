"""Deterministic underspecification checks.

Clarification was entirely LLM-decided: `input_refine` asked a model whether a
question was clear enough. That catches vague phrasing, and misses the two cases
that silently produce a wrong number rather than a confusing one —

  * **no time range.** "doanh thu bao nhiêu?" against a time-bound metric runs
    over all history. A plausible number comes back, nobody asked "since when",
    and it answers a question the user did not ask.
  * **an underspecified metric.** "doanh thu" when the registry holds both
    `revenue_gross` and `revenue_net` resolves to whichever term matched. Both
    are legitimate readings and they differ by exactly the refunds — the
    confusion the Semantic Layer exists to end, reappearing at the door.

Neither is *vague*. Both read as perfectly clear questions, which is why asking
a model "is this ambiguous?" does not catch them. They are structural gaps,
visible in the slots, so they are checked structurally here.

**Every rule is gated on "would the answer actually differ?"** A clarification
that fires on a question with one possible answer is pure friction, and a
system that asks needlessly trains people to ignore it — at which point the one
question that mattered gets ignored too.

Pure: no I/O, no model call. The caller turns a Question into whatever its
transport uses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from libs.semantic.models import MetricDefinition
from libs.semantic.slots import AnalyticalSlots

# Analysis types where a missing period actually changes the answer. A lookup
# ("quy định nghỉ phép là gì?") has no period to miss.
_TIME_SENSITIVE = frozenset({"analytical", "comparison"})


class Reason(str, Enum):
    MISSING_TIME_RANGE = "missing_time_range"
    AMBIGUOUS_METRIC = "ambiguous_metric"
    COMPARISON_WITHOUT_BASELINE = "comparison_without_baseline"


@dataclass(frozen=True)
class Question:
    """One thing to ask before running anything."""

    reason: Reason
    text: str
    # Concrete choices the UI can render as buttons. A clarification the user
    # has to compose free text for is one they will abandon; these also map
    # straight back onto slots on the next turn.
    options: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, object]:
        return {"reason": self.reason.value, "text": self.text, "options": list(self.options)}


def _computes_differently(a: MetricDefinition, b: MetricDefinition) -> bool:
    """True when two metrics would produce different numbers.

    Two registered names for the same arithmetic are a naming choice, not an
    ambiguity worth interrupting someone over.
    """
    return (
        a.aggregation is not b.aggregation
        or a.expression != b.expression
        or a.source_table != b.source_table
        or set(map(str, a.default_filters)) != set(map(str, b.default_filters))
    )


def check(
    *,
    analysis_type: str,
    slots: AnalyticalSlots,
    definition: MetricDefinition | None,
    metric_alternatives: tuple[MetricDefinition, ...] = (),
) -> list[Question]:
    """Structural gaps in this request, most consequential first.

    `metric_alternatives` are other metrics whose synonyms also cover what the
    user typed — supplied by the caller, which owns the catalog.

    An empty list means "nothing structural is missing"; it does not mean the
    question is answerable, only that these particular rules found no gap.
    """
    questions: list[Question] = []

    # ── the metric itself is underspecified ────────────────────────────────
    # First, because picking the wrong metric makes every other slot moot.
    if definition is not None and metric_alternatives:
        differing = [m for m in metric_alternatives if _computes_differently(definition, m)]
        if differing:
            names = [definition.name or definition.key] + [m.name or m.key for m in differing]
            questions.append(Question(
                reason=Reason.AMBIGUOUS_METRIC,
                text=(
                    "Câu hỏi có thể hiểu theo nhiều chỉ số khác nhau và các chỉ số này "
                    "cho ra con số KHÁC nhau. Bạn muốn dùng chỉ số nào?"
                ),
                options=tuple(names),
            ))

    # ── no period on a time-bound metric ───────────────────────────────────
    if (
        analysis_type in _TIME_SENSITIVE
        and definition is not None
        and definition.time_column
        and not slots.time_range
        and not slots.relative_period
    ):
        questions.append(Question(
            reason=Reason.MISSING_TIME_RANGE,
            text=(
                "Bạn muốn xem trong khoảng thời gian nào? Nếu không chọn, hệ thống sẽ "
                "tính trên TOÀN BỘ dữ liệu lịch sử — thường không phải điều bạn muốn."
            ),
            # Mirror libs.semantic.slots.RELATIVE_PERIODS so an answer maps
            # straight back onto a slot with no free-text parsing.
            options=("this_month", "last_month", "this_quarter", "last_quarter", "this_year"),
        ))

    # ── a comparison with nothing to compare against ───────────────────────
    if analysis_type == "comparison" and not slots.compare_to:
        # Two values on one dimension ("miền Bắc và miền Nam") IS the baseline —
        # the comparison is between them, and asking would be obtuse.
        has_multi_value_filter = any(len(f.values) > 1 for f in slots.filters)
        if not has_multi_value_filter:
            questions.append(Question(
                reason=Reason.COMPARISON_WITHOUT_BASELINE,
                text="Bạn muốn so sánh với mốc nào?",
                options=("previous_period", "same_period_last_year"),
            ))

    return questions


def to_payload(questions: list[Question]) -> dict[str, object]:
    """Wire shape for the runtime, mirroring the orchestrator's
    `__clarification` frame so a client already handling that needs no change."""
    return {
        "clarification_questions": [q.text for q in questions],
        # Structured alongside the prose so a UI can render choices, and so
        # "how often do we have to ask about a missing period?" is answerable
        # from stored traffic rather than by parsing sentences.
        "clarification_details": [q.to_dict() for q in questions],
    }
