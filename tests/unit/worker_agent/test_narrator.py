"""The model Narrator's post-check: numbers only through figure tags, one retry, then drop."""

from __future__ import annotations

import decimal
from typing import Any

import pytest

from services.worker_agent.answer.narrate import Figure
from services.worker_agent.answer.narrator import narrate
from services.worker_agent.llm import Reply

pytestmark = [pytest.mark.unit]


class Scripted:
    def __init__(self, *texts: str) -> None:
        self.texts = list(texts)

    async def json_chat(
        self, role: str, system: str, user: str, output: type[Any], *, max_tokens: int = 800
    ) -> Any:
        return Reply(
            output.model_validate({"text": self.texts.pop(0)}),
            model="responder",
            tokens=50,
            latency_ms=3,
        )


FIGS = [
    Figure(
        "F1",
        "Tỷ lệ nợ xấu, vùng Miền Bắc, tháng 9/2026",
        decimal.Decimal("10.5"),
        "%",
        2,
        0,
        "value",
        {"vung": "Miền Bắc"},
    ),
    Figure(
        "F2",
        "Tỷ lệ nợ xấu, vùng Miền Nam, tháng 9/2026",
        decimal.Decimal("3.25"),
        "%",
        2,
        1,
        "value",
        {"vung": "Miền Nam"},
    ),
]


async def run(*texts: str) -> Any:
    return await narrate(
        Scripted(*texts),
        FIGS,
        question="Tỷ lệ nợ xấu theo vùng tháng 9/2026",
        metric_name="Tỷ lệ nợ xấu",
        exempt_text=["tháng 9/2026"],
    )


async def test_tags_become_vietnamese_values() -> None:
    out = await run("Tháng 9/2026, Miền Bắc cao nhất với {{F1}}, Miền Nam {{F2}}.")
    assert out.text == "Tháng 9/2026, Miền Bắc cao nhất với 10,50%, Miền Nam 3,25%."
    assert (out.regenerated, out.sentences_dropped, out.numbers_total) == (0, 0, 2)


async def test_a_stray_number_gets_one_more_try() -> None:
    out = await run("Miền Bắc là 10,5% còn Miền Nam {{F2}}.", "Miền Bắc {{F1}}, Miền Nam {{F2}}.")
    assert out.text == "Miền Bắc 10,50%, Miền Nam 3,25%." and out.regenerated == 1
    assert out.untraceable_draft == 1


@pytest.mark.req("AC-01-01-09")
async def test_a_sentence_still_untraceable_is_dropped_and_said() -> None:
    out = await run(
        "Chênh lệch là 7 điểm. Miền Bắc {{F1}}.",
        "Chênh lệch 7,25 điểm. Miền Bắc {{F1}}.",
    )
    assert out.text == "Miền Bắc 10,50%. Đã bỏ 1 câu có số không truy được nguồn."
    assert out.sentences_dropped == 1


async def test_a_tag_it_was_not_given_is_never_filled() -> None:
    assert await run("Toàn hệ thống {{F9}}.", "Toàn hệ thống {{F9}}.") is None
