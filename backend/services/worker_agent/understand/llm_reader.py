"""The Understander's model pass (tab 5 G5.1): when the rule pass places no metric, the fast model
reads the question against the Data Space's own catalogue and answers chips in JSON.

The model only chooses among codes it is shown (metrics, dimensions, a few values per dimension)
and the answer goes through resolver.from_chips, so a code it invents is refused, never compiled.
Its chips carry confidence 0.8, the proceed threshold: a model reading is enough to answer but
never outranks a name or an approved synonym, and an ambiguous reading becomes a clarification.
The period keeps the rule parser's reading of the text; the model only names one when the text
had none the parser could read.

A question in a conversation is read with the conversation (AC-01-04-05): the last ten questions
word for word with what each was understood as, older ones as a summary of the metrics and cuts
they asked about. The summary is made from their plans, not by a model.
"""

from __future__ import annotations

import datetime
import json
from collections.abc import Sequence

from pydantic import BaseModel

from services.worker_agent.llm import Gateway, ModelUnavailable, Reply
from services.worker_agent.understand.resolver import (
    Chip,
    Understanding,
    Vocabulary,
    from_chips,
)
from services.worker_agent.understand.text import find_period

MODEL_CONFIDENCE = 0.8
VALUES_PER_DIMENSION = 30
VERBATIM_TURNS = 10

# An earlier question of the conversation: its text and the chips of its plan.
Turn = tuple[str, Sequence[dict[str, object]]]

SYSTEM = """Bạn đọc một câu hỏi số liệu tiếng Việt và chọn đúng các mã trong danh mục được cho.
Chỉ dùng mã có trong danh mục; không đoán mã mới. Trả về JSON đúng dạng:
{"metric": "<mã chỉ số hoặc null>", "alternatives": ["<mã chỉ số khác nếu câu hỏi mơ hồ>"],
 "group_by": ["<mã chiều>"], "filters": {"<mã chiều>": ["<mã giá trị>"]}, "period": "<cụm từ chỉ kỳ hoặc null>"}
Nếu câu hỏi khớp nhiều chỉ số như nhau, đặt metric = null và liệt kê chúng trong alternatives.
Câu hỏi hỏi một đại lượng không có trong danh mục chỉ số (ví dụ số khoản vay, số khách hàng, số
giao dịch) thì metric = null, kể cả khi hội thoại trước có chỉ số.
Câu hỏi có thể nối tiếp hội thoại trước: chỉ khi nó không nêu đại lượng nào (chỉ đổi chiều, bộ lọc
hoặc kỳ, ví dụ "còn theo vùng thì sao?") mới dùng chỉ số của câu gần nhất."""


class Reading(BaseModel):
    metric: str | None = None
    alternatives: list[str] = []
    group_by: list[str] = []
    filters: dict[str, list[str]] = {}
    period: str | None = None


def _labels(chips: Sequence[dict[str, object]], kind: str) -> list[str]:
    return [str(c.get("label") or c.get("value")) for c in chips if c.get("kind") == kind]


def history_of(turns: Sequence[Turn]) -> str:
    """The conversation as the model sees it: older turns summarised, the last ten verbatim."""
    if not turns:
        return ""
    older, recent = turns[:-VERBATIM_TURNS], turns[-VERBATIM_TURNS:]
    lines: list[str] = []
    if older:
        metrics = dict.fromkeys(m for _, chips in older for m in _labels(chips, "metric"))
        cuts = dict.fromkeys(d for _, chips in older for d in _labels(chips, "dimension"))
        summary = f"{len(older)} câu trước đó hỏi về: {', '.join(metrics) or 'không rõ chỉ số'}"
        lines.append(summary + (f"; theo {', '.join(cuts)}" if cuts else "") + ".")
    for text, chips in recent:
        read = ", ".join(
            f"{kind}={label}"
            for kind in ("metric", "dimension", "filter", "period")
            for label in _labels(chips, kind)
        )
        lines.append(f"- {text}" + (f" (đã hiểu: {read})" if read else ""))
    return "\n".join(lines)


def _catalogue(vocab: Vocabulary) -> str:
    values: dict[str, list[dict[str, str]]] = {}
    for v in vocab.values:
        bucket = values.setdefault(v.dimension_code, [])
        if len(bucket) < VALUES_PER_DIMENSION:
            bucket.append({"code": v.value_code, "label": v.label})
    return json.dumps(
        {
            "metrics": [{"code": m.code, "name": m.name} for m in vocab.metrics],
            "dimensions": [
                {"code": d.code, "name": d.name, "values": values.get(d.code, [])}
                for d in vocab.dimensions
                if not d.is_time
            ],
        },
        ensure_ascii=False,
    )


async def read(
    question: str,
    vocab: Vocabulary,
    gateway: Gateway,
    *,
    today: datetime.date,
    history: Sequence[Turn] = (),
) -> tuple[Understanding, Reply[Reading]] | None:
    if not vocab.metrics:
        return None
    talk = history_of(history)
    context = f"\n\nHội thoại trước (cũ đến mới):\n{talk}" if talk else ""
    try:
        reply = await gateway.json_chat(
            "planner",
            SYSTEM,
            f"Danh mục: {_catalogue(vocab)}{context}\n\nCâu hỏi: {question}",
            Reading,
            max_tokens=400,
        )
    except ModelUnavailable:
        return None
    answer = reply.content
    codes = {m.code: m for m in vocab.metrics}
    alternatives = [codes[c] for c in answer.alternatives if c in codes]
    metric = answer.metric
    if metric is None or metric not in codes:
        if len(alternatives) > 1:
            return (
                Understanding(
                    decision="clarify", options=sorted(alternatives, key=lambda m: m.name)[:3]
                ),
                reply,
            )
        return None
    chips: list[dict[str, object]] = [{"kind": "metric", "value": metric}]
    chips += [{"kind": "dimension", "value": d} for d in answer.group_by]
    chips += [
        {"kind": "filter", "value": f"{dim}={member}"}
        for dim, members in answer.filters.items()
        for member in members
    ]
    period = find_period(question, today) or (
        find_period(answer.period, today) if answer.period else None
    )
    if period is not None:
        chips.append(
            {"kind": "period", "value": f"{period.start}/{period.end}", "label": period.label}
        )
    u = from_chips(chips, vocab)
    if u.decision != "proceed":
        return None
    # A model reading is trusted to the proceed threshold, not as a name or a synonym is.
    u.chips = [
        Chip(c.kind, c.value, c.label, "default", min(c.confidence, MODEL_CONFIDENCE))
        for c in u.chips
    ]
    return u, reply
