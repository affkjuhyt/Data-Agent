"""Central clarify policy for Ask (OP-1 / docs/research/agent-ops-from-system-prompts.md).

The planner may propose mode=ask; this module decides whether that ask is allowed, how many
options it may carry, and when a second clarify round must become an assumption instead.
C-level / executive: at most one round and three options (01 §2.1, F6). Analyst: up to three
rounds and five options. Soft "shall we go deeper?" asks are refused: ask only when ≥2
interpretations would change metric, period, or scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from services.worker_agent.agentic import plan as p

Audience = Literal["executive", "analyst"]

# Default Ask surface is analyst-steerable; callers may pass executive when the product knows.
DEFAULT_AUDIENCE: Audience = "analyst"

_ASSUME = (
    "Giả định hợp lý sau khi đã hỏi làm rõ đủ số vòng cho phép; chạy với giả định đó, "
    "không hỏi lại."
)


@dataclass(frozen=True)
class ClarifyLimits:
    max_rounds: int
    max_options: int


_LIMITS: dict[Audience, ClarifyLimits] = {
    "executive": ClarifyLimits(max_rounds=1, max_options=3),
    "analyst": ClarifyLimits(max_rounds=3, max_options=5),
}


def limits_for(audience: Audience | str | None) -> ClarifyLimits:
    key: Audience = "executive" if audience == "executive" else "analyst"
    return _LIMITS[key]


def apply(
    plan: p.Plan,
    *,
    audience: Audience | str | None = None,
    prior_rounds: int = 0,
) -> p.Plan:
    """Return a plan whose ask mode (if any) respects the audience's clarify budget.

    A plan that is not asking is returned unchanged. An ask with no clarification text is
    demoted to answer. After the audience's round budget is spent, ask becomes analyze when
    there are steps, otherwise answer, with the assumption recorded on thought.
    """
    if plan.mode != "ask":
        return plan

    limits = limits_for(audience)
    clarification = (plan.clarification or "").strip()
    options = [o.strip() for o in plan.options if isinstance(o, str) and o.strip()][
        : limits.max_options
    ]

    if not clarification:
        return plan.model_copy(
            update={
                "mode": "answer",
                "clarification": None,
                "options": [],
                "thought": join_thought(plan.thought, "Bỏ hỏi làm rõ vì thiếu nội dung câu hỏi."),
            }
        )

    if prior_rounds >= limits.max_rounds:
        mode: Literal["analyze", "answer"] = "analyze" if plan.steps else "answer"
        return plan.model_copy(
            update={
                "mode": mode,
                "clarification": None,
                "options": [],
                "thought": join_thought(plan.thought, _ASSUME),
            }
        )

    return plan.model_copy(update={"clarification": clarification, "options": options})


def join_thought(existing: str, note: str) -> str:
    existing = (existing or "").strip()
    return f"{existing} {note}".strip() if existing else note
