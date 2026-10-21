"""The model reads a question with its conversation (AC-01-04-05): the last ten turns verbatim,
older ones summarised from their plans."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

import pytest

from services.worker_agent.llm import Reply
from services.worker_agent.understand.llm_reader import history_of, read
from services.worker_agent.understand.resolver import DimensionEntry, MetricEntry, Vocabulary

pytestmark = [pytest.mark.unit]

NPL = MetricEntry(uuid.uuid4(), "NPL_RATIO", "Tỷ lệ nợ xấu", certified=True)
VOCAB = Vocabulary(metrics=[NPL], dimensions=[DimensionEntry(uuid.uuid4(), "vung", "vùng")])


def _turn(n: int) -> tuple[str, list[dict[str, object]]]:
    metric = "Dư nợ" if n < 3 else "Tỷ lệ nợ xấu"
    return f"câu số {n}", [
        {"kind": "metric", "value": "M", "label": metric},
        {"kind": "dimension", "value": "vung", "label": "vùng"},
    ]


class Recording:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def json_chat(
        self, role: str, system: str, user: str, output: type[Any], *, max_tokens: int = 800
    ) -> Any:
        self.prompts.append(user)
        return Reply(
            output.model_validate({"metric": "NPL_RATIO"}), model="planner", tokens=10, latency_ms=1
        )


@pytest.mark.req("AC-01-04-05")
def test_twelve_turns_keep_the_last_ten_verbatim_and_summarise_the_rest() -> None:
    text = history_of([_turn(n) for n in range(1, 13)])
    lines = text.splitlines()
    assert lines[0] == "2 câu trước đó hỏi về: Dư nợ; theo vùng."
    assert lines[1:] == [
        f"- câu số {n} (đã hiểu: metric=Tỷ lệ nợ xấu, dimension=vùng)" for n in range(3, 13)
    ]
    assert "câu số 1 " not in text and "câu số 2 " not in text


async def test_the_model_is_given_the_conversation_and_none_without_one() -> None:
    gateway = Recording()
    today = datetime.date(2026, 10, 17)
    await read("còn miền nam?", VOCAB, gateway, today=today, history=[_turn(5)])
    await read("nợ xấu", VOCAB, gateway, today=today)
    assert "Hội thoại trước (cũ đến mới):\n- câu số 5" in gateway.prompts[0]
    assert "Hội thoại trước" not in gateway.prompts[1]
