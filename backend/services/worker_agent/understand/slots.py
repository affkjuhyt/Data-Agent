"""What a conversation has settled so far, for the next question to inherit (AE-11, ASK_ENGINE_SPEC
§8.2).

`ConversationSlots` is read from the turn before, whichever path answered it: a governed turn's
plan chips and answer figures, or an agentic turn's dataset reading. Both paths then inherit the
same way: the governed reading through `resolver.follow_up(..., slots=)`, the agent through
`agentic/engine._readings` (a governed turn becomes a reading like its own) and the slots block of
its situation; a question to the documents is rewritten from them first (`rewrite_for_retrieval`).

Slots are derived on every turn from what the turn stored (qa.plan.chips, qa.answer.figures,
qa.plan.steps), not kept as a second copy on qa.conversation: there is one record of what a turn
read, so the slots can never disagree with it.

The rules (§8.2): a period alone keeps the metric, filters, cut and subject; a comparison alone keeps
the metric, period and filters; a cut or a ranking keeps the metric, period and filters; "chi nhánh
đó", "con số này" name the turn before's top-1 (`subject`); a new metric on a turn that continues
the conversation keeps its period when it names none.
"""

from __future__ import annotations

import decimal
import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from libs.dataagent_db.models import qa
from services.worker_agent.understand.text import norm

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from services.worker_agent.understand.resolver import Vocabulary

# "đó", "này", "ấy" after a dimension's name ("nhóm sản phẩm đó"), "that"/"this" before it: the
# turn names the member the one before put first. Folded, "đó" is "do", which is also "do" (because
# of), so the word alone is never enough.
_AFTER = r"(?:do|nay|ay|kia)"
_BEFORE = r"(?:that|this|the same)"
# A turn that continues the one before rather than starting over: "còn …", "… thì sao", "vậy …",
# "what about", "and …". norm() folds diacritics: "còn" is "con", "thì sao" is "thi sao".
_CONTINUES = re.compile(
    r"^(?:con|vay|the con|and|what about|how about)\b|\b(?:thi sao|the nao|ra sao|nhi)\b"
)
# Retrieval: a question with fewer content words than this says too little to search on alone.
SHORT = 4


@dataclass(frozen=True)
class Subject:
    """The member the turn before put first ("chi nhánh đó" after "chi nhánh nào cao nhất")."""

    dimension: str
    value: str
    label: str
    dimension_label: str = ""


@dataclass
class ConversationSlots:
    metric: str | None = None
    metric_label: str | None = None
    period: str | None = None  # "start/end", as a period chip holds it
    period_label: str | None = None
    compare_to: str | None = None
    filters: dict[str, list[str]] = field(default_factory=dict)
    group_by: list[str] = field(default_factory=list)
    subject: Subject | None = None
    source: str | None = None  # metrics, documents, hybrid
    last_answer_id: uuid.UUID | None = None
    last_figures: list[str] = field(default_factory=list)
    # The turn's reading as chips, the form resolver.follow_up inherits from.
    chips: list[dict[str, object]] = field(default_factory=list)

    def empty(self) -> bool:
        return self.metric is None and self.source is None

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["last_answer_id"] = str(self.last_answer_id) if self.last_answer_id else None
        return out


def _top(figures: list[Any], dimension: str) -> tuple[str, Any] | None:
    """The member of `dimension` with the largest figure, among the figures cut by it alone."""
    best: tuple[decimal.Decimal, str, Any] | None = None
    for f in figures:
        if not isinstance(f, dict):
            continue
        dims = f.get("dimensions")
        if not isinstance(dims, dict) or set(dims) != {dimension}:
            continue
        try:
            value = decimal.Decimal(str(f.get("value")))
        except (decimal.InvalidOperation, ValueError):
            continue
        if not value.is_finite():
            continue
        if best is None or value > best[0]:
            best = (value, str(dims[dimension]), f.get("key"))
    return (best[1], best[2]) if best is not None else None


def from_chips(
    chips: list[dict[str, object]],
    *,
    figures: list[Any] | None = None,
    answer_id: uuid.UUID | None = None,
    answer_kind: str | None = None,
    labels: dict[tuple[str, str], str] | None = None,
) -> ConversationSlots:
    """A governed turn: its plan's chips, and its answer's figures for the subject and "F1"…"""
    s = ConversationSlots(chips=[c for c in chips if isinstance(c, dict)])
    for c in s.chips:
        kind, value, label = str(c.get("kind")), str(c.get("value", "")), c.get("label")
        if kind == "metric" and s.metric is None:
            s.metric, s.metric_label = value, str(label or value)
        elif kind == "period":
            s.period, s.period_label = value, str(label or value)
        elif kind == "dimension":
            s.group_by.append(value)
        elif kind == "filter":
            dim, _, member = value.partition("=")
            s.filters.setdefault(dim, []).append(member)
        elif kind == "compare":
            s.compare_to = value
    figs = [f for f in figures or [] if isinstance(f, dict)]
    s.last_figures = [str(f["key"]) for f in figs if f.get("key")]
    s.last_answer_id = answer_id
    if answer_kind in ("document",):
        s.source = "documents"
    elif answer_kind == "mixed":
        s.source = "hybrid"
    elif s.metric is not None:
        s.source = "metrics"
    if s.group_by:
        top = _top(figs, s.group_by[0])
        if top is not None:
            member, _ = top
            label = (labels or {}).get((s.group_by[0], member), member)
            named = next(
                (
                    str(c.get("label") or "")
                    for c in s.chips
                    if c.get("kind") == "dimension" and c.get("value") == s.group_by[0]
                ),
                "",
            )
            s.subject = Subject(s.group_by[0], member, label, named)
    return s


def from_reading(reading: dict[str, Any]) -> ConversationSlots:
    """An agentic turn: a dataset's reading (metric, metric_name, group_by, filters, period)."""
    s = ConversationSlots(
        metric=str(reading["metric"]) if reading.get("metric") else None,
        metric_label=str(reading.get("metric_name") or reading.get("metric") or "") or None,
        period=str(reading["period"]) if reading.get("period") else None,
        group_by=[str(g) for g in reading.get("group_by") or []],
        filters={str(k): [str(m) for m in v] for k, v in (reading.get("filters") or {}).items()},
        compare_to=reading.get("compare_to"),
        source="metrics",
    )
    s.chips = as_chips(s)
    return s


def as_chips(s: ConversationSlots) -> list[dict[str, object]]:
    chips: list[dict[str, object]] = []
    if s.metric:
        chips.append({"kind": "metric", "value": s.metric, "label": s.metric_label or s.metric})
    for dim in s.group_by:
        chips.append({"kind": "dimension", "value": dim, "label": dim})
    for dim, members in s.filters.items():
        chips += [{"kind": "filter", "value": f"{dim}={m}", "label": m} for m in members]
    if s.period:
        chips.append({"kind": "period", "value": s.period, "label": s.period_label or s.period})
    return chips


def as_reading(s: ConversationSlots) -> dict[str, Any] | None:
    """The slots as the agent's own reading of a turn, so a governed turn is one it can follow."""
    if s.metric is None:
        return None
    return {
        "metric": s.metric,
        "metric_name": s.metric_label,
        "group_by": list(s.group_by),
        "filters": {k: list(v) for k, v in s.filters.items()},
        "period": s.period,
    }


def names_subject(question: str, slots: ConversationSlots | None, *names: str) -> bool:
    """The question points at the turn before's top-1: "<dimension> đó/này/ấy", "that <dimension>"
    under any of the dimension's names (its label, the chip's label, a synonym)."""
    if slots is None or slots.subject is None:
        return False
    folded = norm(question)
    for name in {norm(n) for n in (slots.subject.dimension_label, *names) if n}:
        if not name:
            continue
        name_re = re.escape(name)
        if re.search(rf"\b{name_re}\s+{_AFTER}\b", folded) or re.search(
            rf"\b{_BEFORE}\s+{name_re}\b", folded
        ):
            return True
    return False


def _subject_phrase(question: str, slots: ConversationSlots, names: tuple[str, ...]) -> str:
    """The question with "<dimension> đó" replaced by the member it means, as written."""
    assert slots.subject is not None
    for name in {n for n in (slots.subject.dimension_label, *names) if n}:
        label = slots.subject.label
        out, n = re.subn(
            rf"\b({re.escape(name)})\s+(?:đó|này|ấy|kia)\b",
            # The name as the person wrote it (group 1); a backslash in the label is a character.
            rf"\1 {label.replace(chr(92), chr(92) * 2)}",
            question,
            count=1,
            flags=re.I,
        )
        if n:
            return out
    return question


def continues(question: str) -> bool:
    return bool(_CONTINUES.search(norm(question)))


def rewrite_for_retrieval(question: str, slots: ConversationSlots | None, *names: str) -> str:
    """A follow-up to the documents as a query that stands alone (RG-07), deterministically: the
    subject in place of "<dimension> đó", the metric's name and the period in words added when the
    question does not say them. Unchanged without slots or when it already stands alone."""
    if slots is None or slots.empty():
        return question
    text = question
    folded = norm(question)
    back = names_subject(question, slots, *names)
    if back:
        text = _subject_phrase(question, slots, names)
    content = [w for w in folded.split() if len(w) > 2]
    short = len(content) < SHORT
    if not (back or short or continues(question)):
        return question
    extra = []
    if slots.metric_label and norm(slots.metric_label) not in folded:
        extra.append(slots.metric_label)
    if slots.period_label and norm(slots.period_label) not in folded:
        extra.append(slots.period_label)
    return " ".join([text.strip(), *extra]).strip()


def render(slots: ConversationSlots | None) -> str:
    """The slots for the agent's situation: what "đó", "con số này" and a bare period refer to."""
    if slots is None or slots.empty():
        return ""
    lines = []
    if slots.metric:
        lines.append(f"metric: {slots.metric_label or slots.metric} ({slots.metric})")
    if slots.period:
        lines.append(f"period: {slots.period_label or slots.period} = {slots.period}")
    if slots.group_by:
        lines.append(f"group_by: {', '.join(slots.group_by)}")
    for dim, members in slots.filters.items():
        lines.append(f"filter: {dim} = {', '.join(members)}")
    if slots.compare_to:
        lines.append(f"compare_to: {slots.compare_to}")
    if slots.subject is not None:
        s = slots.subject
        lines.append(f'subject ("đó", "này"): {s.dimension} = {s.value} ({s.label})')
    if slots.last_figures:
        lines.append(f"last_figures: {', '.join(slots.last_figures[:10])}")
    return "\n".join(lines)


async def previous_slots(
    session: AsyncSession, question: qa.Question, vocab: Vocabulary | None = None
) -> ConversationSlots | None:
    """What the turn before settled: the question this one follows (its parent, else the one
    before it in the conversation), its latest plan's chips and its answer's figures, the
    subject named by the vocabulary's labels. None on a conversation's first question."""
    earlier = question.parent_question_id or await session.scalar(
        select(qa.Question.id)
        .where(
            qa.Question.conversation_id == question.conversation_id,
            qa.Question.seq < question.seq,
        )
        .order_by(qa.Question.seq.desc())
        .limit(1)
    )
    if earlier is None:
        return None
    chips = await session.scalar(
        select(qa.Plan.chips)
        .where(qa.Plan.question_id == earlier)
        .order_by(qa.Plan.version.desc())
        .limit(1)
    )
    answer = await session.scalar(
        select(qa.Answer)
        .where(qa.Answer.question_id == earlier)
        .order_by(qa.Answer.created_at.desc())
        .limit(1)
    )
    labels = {(v.dimension_code, v.value_code): v.label for v in (vocab.values if vocab else [])}
    return from_chips(
        [c for c in (chips or []) if isinstance(c, dict)],
        figures=list(answer.figures or []) if answer is not None else None,
        answer_id=answer.id if answer is not None else None,
        answer_kind=answer.kind if answer is not None else None,
        labels=labels,
    )
