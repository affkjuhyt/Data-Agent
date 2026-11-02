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


# Exploratory rows (sqlgen/answer.py): a label is a row's cells, data and not the question; its
# numbers stay with that row's figure, so another column's cell cannot be written as a figure.
ROWS = [
    Figure("F1", "2012, 4401", decimal.Decimal("120"), None, 0, 0, "orders", {"y": "2012"}),
    Figure("F2", "2013, 9305", decimal.Decimal("310"), None, 0, 1, "orders", {"y": "2013"}),
]


async def told(*texts: str) -> Any:
    return await narrate(
        Scripted(*texts),
        ROWS,
        question="Số đơn theo năm",
        metric_name="",
        exempt_text=[],
        labels_per_sentence=True,
    )


async def test_a_rows_label_may_be_written_beside_its_own_figure() -> None:
    out = await told("Năm 2013 có {{F2}} đơn, năm 2012 có {{F1}}.")
    assert out.text == "Năm 2013 có 310 đơn, năm 2012 có 120." and out.regenerated == 0


async def test_a_rows_label_is_not_a_number_anywhere_else() -> None:
    out = await told(
        "Tổng là 9305 đơn. Năm 2013 có {{F2}}.", "Tổng là 9305 đơn. Năm 2013 có {{F2}}."
    )
    assert out.text == "Năm 2013 có 310. Đã bỏ 1 câu có số không truy được nguồn."
    assert out.untraceable_draft == 1


async def test_a_unit_the_model_writes_after_its_tag_is_not_written_twice() -> None:
    # "{{F1}} khách hàng mua…": the tag is filled with "17.429 khách hàng" already.
    customers = [Figure("F1", "Số khách hàng", decimal.Decimal(17429), "khách hàng", 0, 0, "v")]
    out = await narrate(
        Scripted("Năm 2013 có {{F1}} khách hàng mua qua Internet."),
        customers,
        question="Có bao nhiêu khách hàng mua qua Internet năm 2013",
        metric_name="Số khách hàng mua qua Internet",
        exempt_text=[],
    )
    assert out.text == "Năm 2013 có 17.429 khách hàng mua qua Internet."
