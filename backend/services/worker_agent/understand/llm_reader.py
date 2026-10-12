"""The Understander's model pass (tab 5 G5.1): when the rule pass places no metric, the fast model
reads the question against the Data Space's own catalogue and answers chips in JSON.

The model only chooses among codes it is shown (metrics, dimensions, a few values per dimension)
and the answer goes through resolver.from_chips, so a code it invents is refused, never compiled.
Its chips carry confidence 0.8, the proceed threshold: a model reading is enough to answer but
never outranks a name or an approved synonym, and an ambiguous reading becomes a clarification.
The period keeps the rule parser's reading of the text; the model only names one when the text
had none the parser could read.
"""

from __future__ import annotations

import datetime
import json
from typing import Any

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

SYSTEM = """Bạn đọc một câu hỏi số liệu tiếng Việt và chọn đúng các mã trong danh mục được cho.
Chỉ dùng mã có trong danh mục; không đoán mã mới. Trả về JSON đúng dạng:
{"metric": "<mã chỉ số hoặc null>", "alternatives": ["<mã chỉ số khác nếu câu hỏi mơ hồ>"],
 "group_by": ["<mã chiều>"], "filters": {"<mã chiều>": ["<mã giá trị>"]}, "period": "<cụm từ chỉ kỳ hoặc null>"}
Nếu câu hỏi khớp nhiều chỉ số như nhau, đặt metric = null và liệt kê chúng trong alternatives."""


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
    question: str, vocab: Vocabulary, gateway: Gateway, *, today: datetime.date
) -> tuple[Understanding, Reply] | None:
    if not vocab.metrics:
        return None
    try:
        reply = await gateway.json_chat(
            "planner",
            SYSTEM,
            f"Danh mục: {_catalogue(vocab)}\n\nCâu hỏi: {question}",
            max_tokens=400,
        )
    except ModelUnavailable:
        return None
    answer: dict[str, Any] = reply.content
    codes = {m.code: m for m in vocab.metrics}
    alternatives = [codes[c] for c in answer.get("alternatives") or [] if c in codes]
    metric = answer.get("metric")
    if metric not in codes:
        if len(alternatives) > 1:
            return (
                Understanding(
                    decision="clarify", options=sorted(alternatives, key=lambda m: m.name)[:3]
                ),
                reply,
            )
        return None
    chips: list[dict[str, object]] = [{"kind": "metric", "value": metric}]
    chips += [{"kind": "dimension", "value": str(d)} for d in answer.get("group_by") or []]
    filters = answer.get("filters") or {}
    if isinstance(filters, dict):
        chips += [
            {"kind": "filter", "value": f"{dim}={member}"}
            for dim, members in filters.items()
            for member in (members if isinstance(members, list) else [members])
        ]
    period = find_period(question, today) or (
        find_period(str(answer["period"]), today) if answer.get("period") else None
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
