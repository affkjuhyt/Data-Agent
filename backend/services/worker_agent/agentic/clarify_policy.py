"""Central clarify policy for Ask (OP-1 / docs/research/agent-ops-from-system-prompts.md).

The planner may propose mode=ask; this module decides whether that ask is allowed, how many
options it may carry, and when a second clarify round must become an assumption instead.
C-level / executive: at most one round and three options (01 §2.1, F6). Analyst: up to three
rounds and five options. Soft "shall we go deeper?" asks are refused: ask only when ≥2
interpretations would change metric, period, or scope.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from typing import Literal

from services.worker_agent.agentic import plan as p
from services.worker_agent.agentic.grounded import ANALYSIS_WORDS
from services.worker_agent.agentic.tools.base import Clarify
from services.worker_agent.semantic.loader import SpaceLayer
from services.worker_agent.understand import analyst
from services.worker_agent.understand.packs import PACKS
from services.worker_agent.understand.resolver import metric_words, quantity_words
from services.worker_agent.understand.text import find_period, norm

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
_FILLER = frozenset[str]().union(*(pack.filler for pack in PACKS))
_ANALYSIS_PHRASE = re.compile(
    r"\b(?:so sanh|vi sao|tai sao|dong gop|bien dong|co cau|ty trong|xu huong|chenh lech"
    r"|nguyen nhan|phan tich|cao nhat|thap nhat|luy ke)\b"
)


def _closest(question: str, layer: SpaceLayer) -> list[str]:
    """The certified metrics that share a quantity word with the question, closest first.
    Matched on every word, "Khách hàng rời bỏ là bao nhiêu?" was offered "Số đơn Internet" and
    "Tồn kho" beside "Số khách hàng mua qua Internet" (17/11): only "khách hàng" says what is asked."""
    vocab = layer.vocabulary
    asked = set(quantity_words(question, vocab, today=datetime.date.today()))
    by_code = {m.code: m for m in vocab.metrics}
    scored = [
        (len(metric_words(by_code[m["metric"]], vocab) & asked), m["name"])
        for m in analyst.search_metrics(layer, question)
        if m["certified"] and m["metric"] in by_code
    ]
    # Only the best-matched: folded, "hàng" of "khách hàng" is also the "hàng" of a synonym of
    # "Số đơn Internet" ("đơn hàng") and "Tồn kho" ("hàng tồn"), one word against two.
    best = max((score for score, _ in scored), default=0)
    return [name for score, name in scored if best and score == best]


def _members(tokens: list[str]) -> set[str]:
    """Words written as names inside the sentence ("nhóm Bikes", "Mountain-200"): members the
    catalogue's value list may not hold, never quantities. "Doanh thu nhóm Bikes năm 2013" was
    asked back as if "Bikes" were a metric (17/11)."""
    return {
        norm(_TRAILING.sub("", t))
        for i, t in enumerate(tokens)
        if (i > 0 and t[:1].isupper()) or any(ch.isdigit() for ch in _TRAILING.sub("", t))
    } - {""}


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
    wanted = set(unnamed) - _members(tokens)
    if not wanted or wanted <= cut_words:
        return None
    at = [i for i, t in enumerate(tokens) if norm(_TRAILING.sub("", t)) in wanted]
    if not at:
        return None
    first, last = at[0], at[-1]
    phrase = _TRAILING.sub("", " ".join(tokens[first : last + 1]))
    end = m.group(0) if (m := _TRAILING.search(tokens[last])) else ""
    found = _closest(question, layer)

    def asked_with(name: str) -> str:
        said = name if first == 0 else name[:1].lower() + name[1:]
        return " ".join([*tokens[:first], said + end, *tokens[last + 1 :]])

    return Clarify(
        f"Chưa có chỉ số chuẩn nào đo “{phrase}”. Bạn muốn xem chỉ số nào dưới đây?",
        [asked_with(name) for name in found[:max_options]],
    )


def same_named(question: str, layer: SpaceLayer, today: datetime.date) -> Clarify | None:
    """F2: the question names a quantity that two or more certified metrics are named after and
    none is exactly ("Lãi gộp năm 2013": Internet or đại lý; "Số tài chính": ngân sách or thực tế):
    asked back with those metrics, each the question again with its name in place. The planner
    picked one alone and answered it (F0, 17/11).

    Matched on whole phrases (a metric's name or one of its synonyms), not on the union of their
    words: "số lượng khách hàng", a synonym of the customers, holds "lượng" too. None when a word
    of the quantity is in no metric's phrase ("Giá cổ phiếu": not this check's to answer), when a
    metric is named exactly, or when fewer than two are named."""
    vocab = layer.vocabulary
    visible = analyst._visible(layer)
    phrases: dict[str, list[set[str]]] = {
        code: [set(norm(m.name).split())] for code, m in visible.items() if m.certified
    }
    for phrase, target in vocab.synonyms.items():
        for code, m in visible.items():
            if code in phrases and target[:2] == ("metric", str(m.metric_id)):
                phrases[code].append(set(phrase.split()))
    known = set().union(*(w for ps in phrases.values() for w in ps)) if phrases else set()
    # A cut's words ("theo khu vực") are no unknown quantity; they are not set aside either, as the
    # space's cuts are named with its metrics' words ("Năm tài chính", "Ngày đặt hàng (đại lý)").
    cuts: set[str] = set()
    for d in vocab.dimensions:
        cuts |= set(norm(d.name).split())
    content = [w for w in quantity_words(question, vocab, today=today) if w not in ANALYSIS_WORDS]
    if not any(w in known for w in content) or any(w not in known | cuts for w in content):
        return None
    # The question's words any metric is named with, fillers kept: "bán" folds to "bạn", a filler,
    # and "Số lượng bán" read as "lượng" alone also matched "số lượng khách hàng".
    # Analysis is set aside by its phrases: its single words are metric words too ("gộp" of
    # "đóng góp", "tài" of "tại sao", "số" of "so sánh", "biên" of "biến động").
    found = find_period(question, today, vocab.calendar)
    text = norm(question).replace(found.span, " ") if found else norm(question)
    asked = {w for w in _ANALYSIS_PHRASE.sub(" ", text).split() if w in known}
    named = {
        code: min(len(p - asked - _FILLER) for p in ps if asked <= p)
        for code, ps in phrases.items()
        if any(asked <= p for p in ps)
    }
    if len(named) < 2 or 0 in named.values():
        return None
    tokens = question.split()
    at = [i for i, t in enumerate(tokens) if norm(_TRAILING.sub("", t)) in asked]
    if not at:
        return None
    first, last = at[0], at[-1]
    end = m.group(0) if (m := _TRAILING.search(tokens[last])) else ""
    phrase = _TRAILING.sub("", " ".join(tokens[first : last + 1]))

    def asked_with(name: str) -> str:
        said = name if first == 0 else name[:1].lower() + name[1:]
        return " ".join([*tokens[:first], said + end, *tokens[last + 1 :]])

    names = sorted(visible[code].name for code in named)[:UNNAMED_OPTIONS]
    return Clarify(
        f"“{phrase}” có ở nhiều chỉ số chuẩn. Bạn muốn xem chỉ số nào?",
        [asked_with(name) for name in names],
    )


def spoken_period(start: datetime.date, end: datetime.date) -> str:
    """A period as a question says it: "năm 2013", "quý 4/2013", "tháng 12/2013"."""
    months = (end.year - start.year) * 12 + end.month - start.month
    if start.day == 1 and end.day == 1:
        if months == 12 and start.month == 1:
            return f"năm {start.year}"
        if months == 3 and start.month % 3 == 1:
            return f"quý {(start.month - 1) // 3 + 1}/{start.year}"
        if months == 1:
            return f"tháng {start.month}/{start.year}"
    last = end - datetime.timedelta(days=1)
    return f"từ {start:%d/%m/%Y} đến {last:%d/%m/%Y}"


def missing_period(
    question: str,
    span: str,
    asked: str,
    metric: str,
    nearest: tuple[datetime.date, datetime.date],
    held: tuple[datetime.date, datetime.date] | None = None,
) -> Clarify:
    """The question back when the period it names holds no data of its metric at all (F1): the
    nearest whole period of the same length offered as the question again, said with that period
    in place of the asked one. It used to be read for the nearest period without asking, and the
    answer gave 12/2013's figure to "tháng 3/2015" (F0, 17/11); a period beyond the data is the
    person's to move, not the agent's."""
    said = spoken_period(*nearest)
    tokens = question.split()
    words = span.split()
    folded = [norm(_TRAILING.sub("", t)) for t in tokens]
    option = f"{metric} {said}"
    for i in range(len(tokens) - len(words) + 1):
        if folded[i : i + len(words)] == words:
            last = tokens[i + len(words) - 1]
            end = m.group(0) if (m := _TRAILING.search(last)) else ""
            option = " ".join([*tokens[:i], said + end, *tokens[i + len(words) :]])
            break
    if held is not None:
        # No whole period of that length to offer: say where the data is, offer the nearest one.
        last = held[1] - datetime.timedelta(days=1)
        return Clarify(
            f"Chưa có dữ liệu {metric} cho {asked}; dữ liệu chỉ có từ tháng"
            f" {held[0].month}/{held[0].year} đến tháng {last.month}/{last.year}."
            f" Bạn muốn xem {said}?",
            [option],
        )
    return Clarify(
        f"Chưa có dữ liệu {metric} cho {asked}. Kỳ đầy đủ gần nhất có dữ liệu là {said}."
        " Bạn muốn xem kỳ đó?",
        [option],
    )


def nothing_read(
    question: str, layer: SpaceLayer, period: str | None, *, max_options: int = UNNAMED_OPTIONS
) -> Clarify | None:
    """The question back when the turn tried the data and read nothing, and no word of the
    question stands out as unknown: "Khách hàng rời bỏ" folds to "khach hang roi bo", whose last
    two words are fillers ("rồi") and whose first two a metric names, so only its meaning — not
    its words — has no metric. The closest certified metrics are offered, each asked with the
    question's period; None when there is none to offer."""
    # A question naming a member read nothing because the member was not found, not the metric:
    # offering metrics would drop the member ("Doanh thu nhóm Bikes năm 2013" → "Doanh thu năm
    # 2013 là bao nhiêu?"). A year is digits, not a member.
    named = {w for w in _members(question.split()) if not w.isdigit()}
    if named:
        return None
    found = _closest(question, layer)
    if not found:
        return None
    when = f" {period}" if period else ""
    return Clarify(
        "Chưa có chỉ số chuẩn nào đo đúng điều bạn hỏi. Bạn muốn xem chỉ số nào dưới đây?",
        [f"{name}{when} là bao nhiêu?" for name in found[:max_options]],
    )


def join_thought(existing: str, note: str) -> str:
    existing = (existing or "").strip()
    return f"{existing} {note}".strip() if existing else note
