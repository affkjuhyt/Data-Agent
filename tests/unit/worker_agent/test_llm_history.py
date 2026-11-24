"""The model reads a question with its conversation (AC-01-04-05): the last ten turns verbatim,
older ones summarised from their plans."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

import pytest

from services.worker_agent.llm import Reply
from services.worker_agent.understand.llm_reader import history_of, read
from services.worker_agent.understand.resolver import (
    DimensionEntry,
    MetricEntry,
    ValueEntry,
    Vocabulary,
)

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


@pytest.mark.req("AC-01-04-05")
def test_older_summary_keeps_filters_periods_and_numbers_from_the_question() -> None:
    from services.worker_agent.understand.compaction import summarize_older

    older = [
        (
            "NPL miền Nam tháng 3 còn 2,5% không?",
            [
                {"kind": "metric", "value": "NPL", "label": "Tỷ lệ nợ xấu"},
                {"kind": "filter", "value": "vung=MN", "label": "Miền Nam"},
                {"kind": "period", "value": "2026-03", "label": "03/2026"},
            ],
        ),
        (
            "so với mục tiêu 2%",
            [{"kind": "metric", "value": "NPL", "label": "Tỷ lệ nợ xấu"}],
        ),
    ]
    summary = summarize_older(older)
    assert summary is not None
    line = summary.as_line()
    assert "Tỷ lệ nợ xấu" in line
    assert "lọc Miền Nam" in line
    assert "kỳ 03/2026" in line
    assert "2,5%" in line and "2%" in line
    assert "2026" not in summary.numbers  # bare years are periods, not figures


async def test_the_model_is_given_the_conversation_and_none_without_one() -> None:
    gateway = Recording()
    today = datetime.date(2026, 10, 17)
    await read("còn miền nam?", VOCAB, gateway, today=today, history=[_turn(5)])
    await read("nợ xấu", VOCAB, gateway, today=today)
    assert "Hội thoại trước (cũ đến mới):\n- câu số 5" in gateway.prompts[0]
    assert "Hội thoại trước" not in gateway.prompts[1]


async def test_what_the_conversation_lends_stops_at_the_questions_own_words() -> None:
    # 24/11: the overview and the listing questions were read as the last metric, filtered on the
    # countries of the answer before, for the current year.
    region = DimensionEntry(uuid.uuid4(), "vung", "vùng")
    vocab = Vocabulary(
        metrics=[NPL],
        dimensions=[region],
        values=[ValueEntry("vung", "MB", "Miền Bắc"), ValueEntry("vung", "MN", "Miền Nam")],
    )

    class Lending:
        async def json_chat(
            self, role: str, system: str, user: str, output: type[Any], *, max_tokens: int = 800
        ) -> Any:
            reading = {"metric": "NPL_RATIO", "filters": {"vung": ["MB"]}, "period": "2026"}
            return Reply(output.model_validate(reading), model="planner", tokens=10, latency_ms=1)

    today = datetime.date(2026, 11, 24)
    history = [_turn(5)]
    # No quantity of its own in a conversation: not read (the rules took the follow-ups).
    assert await read("còn năm nay thì sao", vocab, Lending(), today=today, history=history) is None
    # A quantity of its own: read, without the filter and period it does not say.
    out = await read("nợ xấu", vocab, Lending(), today=today, history=history)
    assert out is not None and out[0].metric == NPL
    assert out[0].filters == {} and out[0].period is None
    # Said, they stay.
    said = await read("nợ xấu Miền Bắc năm nay", vocab, Lending(), today=today, history=history)
    assert said is not None and said[0].filters == {"vung": ["MB"]}
    assert said[0].period is not None
