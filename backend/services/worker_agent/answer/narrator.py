"""The model Narrator with its post-check (tab 5 G5.15).

The main model writes the answer's sentences from the figures it is shown (masked ones are not
shown at all) and may write a number only as a tag: {{F3}}. The worker substitutes each tag with
the figure's value in the Vietnamese format, then checks every number left in the text: it must be
a figure's formatted value, or a number the question, a period or a dimension member already
contains (dates, codes, names). On a stray number the model gets one more try; after that the
sentences holding stray numbers are dropped and the answer says so. Nothing survives that cannot
be traced, so number_check.untraceable_final is 0 by construction, as the database requires.

When no sentence survives, or the gateway is absent or fails, the caller keeps the template text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from services.worker_agent.answer.narrate import Figure, shown_value
from services.worker_agent.llm import Gateway, ModelUnavailable

SYSTEM = """Bạn là chuyên viên phân tích dữ liệu, viết câu trả lời ngắn bằng tiếng Việt (2 đến 4 câu)
từ các số được cho: câu đầu trả lời đúng câu hỏi, các câu sau giải thích bảng và biểu đồ.
Chỉ được viết số dưới dạng thẻ {{F<n>}} của số đó; không viết chữ số nào khác, không tự tính toán.
Các số chênh lệch, tỷ lệ thay đổi, tổng, tỷ trọng đã được tính sẵn trong danh sách; "nhan_dinh"
là các nhận định đã kiểm, hãy dùng chúng thay vì tự suy ra. Không nêu nguyên nhân khi không được cho.
Trả về JSON: {"text": "<câu trả lời>"}"""

_TAG = re.compile(r"\{\{\s*(F\d+)\s*\}\}")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Narration:
    text: str
    numbers_total: int
    untraceable_draft: int
    regenerated: int
    sentences_dropped: int
    model: str
    tokens: int


def _fill(text: str, figures: dict[str, Figure]) -> str | None:
    """Tags replaced by values; None when the text cites a figure it was not given."""
    unknown = [k for k in _TAG.findall(text) if k not in figures]
    if unknown:
        return None
    return _TAG.sub(lambda m: shown_value(figures[m.group(1)]), text)


def _stray(sentence: str, allowed: set[str]) -> list[str]:
    return [n for n in _NUMBER.findall(sentence) if n not in allowed]


async def narrate(
    gateway: Gateway,
    figures: list[Figure],
    *,
    question: str,
    metric_name: str,
    exempt_text: list[str],
    hints: list[str] | None = None,
) -> Narration | None:
    shown = {f.key: f for f in figures if f.display_state == "shown" and f.value is not None}
    if not shown:
        return None
    allowed = {n for f in shown.values() for n in _NUMBER.findall(shown_value(f))}
    # A hint's own words ("trong 3 kỳ") may be repeated; its numbers are tags, removed here.
    plain_hints = [_TAG.sub("", h) for h in hints or []]
    for text in [question, *exempt_text, *plain_hints, *(f.label for f in shown.values())]:
        allowed.update(_NUMBER.findall(text))
    prompt = json.dumps(
        {
            "question": question,
            "metric": metric_name,
            "figures": [
                {"tag": f"{{{{{k}}}}}", "label": f.label, "value": shown_value(f)}
                for k, f in shown.items()
            ],
            "nhan_dinh": hints or [],
        },
        ensure_ascii=False,
    )
    draft_strays = 0
    tokens = 0
    model = ""
    for attempt in range(2):
        try:
            reply = await gateway.json_chat("responder", SYSTEM, prompt, max_tokens=600)
        except ModelUnavailable:
            return None
        tokens += reply.tokens
        model = reply.model
        filled = _fill(str(reply.content.get("text") or ""), shown)
        if filled is None or not filled.strip():
            continue
        sentences = [s for s in _SENTENCE.split(filled.strip()) if s]
        strays = [s for s in sentences if _stray(s, allowed)]
        if attempt == 0:
            draft_strays = sum(len(_stray(s, allowed)) for s in strays)
        if not strays:
            return Narration(
                filled.strip(),
                len(_TAG.findall(str(reply.content.get("text")))),
                draft_strays,
                attempt,
                0,
                model,
                tokens,
            )
        if attempt == 1:
            kept = [s for s in sentences if s not in strays]
            if not kept:
                return None
            note = f"Đã bỏ {len(strays)} câu có số không truy được nguồn."
            return Narration(
                " ".join([*kept, note]),
                len(_TAG.findall(str(reply.content.get("text")))),
                draft_strays,
                1,
                len(strays),
                model,
                tokens,
            )
    return None
