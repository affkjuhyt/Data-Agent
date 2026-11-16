"""Central clarify policy for Ask (OP-1 / docs/research/agent-ops-from-system-prompts.md).

The planner may propose mode=ask; this module decides whether that ask is allowed, how many
options it may carry, and when a second clarify round must become an assumption instead.
C-level / executive: at most one round and three options (01 §2.1, F6). Analyst: up to three
rounds and five options. Soft "shall we go deeper?" asks are refused: ask only when ≥2
interpretations would change metric, period, or scope.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from services.worker_agent.agentic import plan as p
from services.worker_agent.agentic.tools.base import Clarify
from services.worker_agent.semantic.loader import SpaceLayer
from services.worker_agent.understand import analyst
from services.worker_agent.understand.text import norm

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


# A quantity no certified metric measures is asked back with this many metrics to choose from.
UNNAMED_OPTIONS = 4
_TRAILING = re.compile(r"[?.!,;:]+$")


def unnamed_quantity(
    question: str, unnamed: list[str], layer: SpaceLayer, *, max_options: int = UNNAMED_OPTIONS
) -> Clarify | None:
    """The question back when it asks a quantity no certified metric measures ("Khách hàng rời
    bỏ là bao nhiêu?", E4): the closest metrics as options, each the question again with that
    metric in place of the quantity, so a pick is asked as it reads. It used to be refused, or
    answered with another quantity the planner swapped in. None when the unnamed words are a
    cut's or a member's (the reading missed a cut, not a metric) or the question cannot be split."""
    vocab = layer.vocabulary
    cut_words: set[str] = set()
    for d in vocab.dimensions:
        cut_words |= set(norm(d.name).split())
    for v in vocab.values:
        cut_words |= set(norm(v.label).split())
    tokens = question.split()
    # A word written as a name inside the sentence ("nhóm Bikes", "Mountain-200") is a member the
    # catalogue's value list does not hold, not a quantity: "Doanh thu nhóm Bikes năm 2013" was
    # asked back as if "Bikes" were a metric (17/11).
    members = {
        norm(_TRAILING.sub("", t))
        for i, t in enumerate(tokens)
        if (i > 0 and t[:1].isupper()) or any(ch.isdigit() for ch in t)
    }
    wanted = set(unnamed) - members
    if not wanted or wanted <= cut_words:
        return None
    at = [i for i, t in enumerate(tokens) if norm(_TRAILING.sub("", t)) in wanted]
    if not at:
        return None
    first, last = at[0], at[-1]
    phrase = _TRAILING.sub("", " ".join(tokens[first : last + 1]))
    end = m.group(0) if (m := _TRAILING.search(tokens[last])) else ""
    found = [m["name"] for m in analyst.search_metrics(layer, question) if m["certified"]]

    def asked_with(name: str) -> str:
        said = name if first == 0 else name[:1].lower() + name[1:]
        return " ".join([*tokens[:first], said + end, *tokens[last + 1 :]])

    return Clarify(
        f"Chưa có chỉ số chuẩn nào đo “{phrase}”. Bạn muốn xem chỉ số nào dưới đây?",
        [asked_with(name) for name in found[:max_options]],
    )


def join_thought(existing: str, note: str) -> str:
    existing = (existing or "").strip()
    return f"{existing} {note}".strip() if existing else note
