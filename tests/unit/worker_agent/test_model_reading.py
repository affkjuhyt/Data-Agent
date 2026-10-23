"""The model proposes, the question's words decide (llm_reader.read, resolver.grounding): a reading
that names its metric only in part, with quantity words the name does not explain, is asked back
rather than answered (R0-13 defect 10); a full name or a paraphrase sharing no word is answered as
the model read it (G5.1)."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

import pytest

from services.worker_agent.llm import Reply
from services.worker_agent.understand.llm_reader import read
from services.worker_agent.understand.resolver import (
    DimensionEntry,
    MetricEntry,
    Understanding,
    Vocabulary,
    grounding,
)

pytestmark = [pytest.mark.unit]

TODAY = datetime.date(2026, 10, 23)
REVENUE = MetricEntry(uuid.uuid4(), "NET_REVENUE", "Doanh thu thuần", certified=True)
COMPLAINTS = MetricEntry(uuid.uuid4(), "COMPLAINT_COUNT", "Số khiếu nại", certified=True)
PROFIT = MetricEntry(uuid.uuid4(), "NET_PROFIT", "Lãi ròng", certified=True)
REGION = DimensionEntry(uuid.uuid4(), "khu_vuc", "Khu vực")
VOCAB = Vocabulary(
    metrics=[REVENUE, COMPLAINTS, PROFIT],
    dimensions=[REGION],
    synonyms={"loi nhuan": ("metric", str(PROFIT.metric_id))},
)


class Model:
    def __init__(self, **reading: Any) -> None:
        self.reading = reading

    async def json_chat(
        self, role: str, system: str, user: str, output: type[Any], *, max_tokens: int = 800
    ) -> Any:
        return Reply(output.model_validate(self.reading), model="planner", tokens=10, latency_ms=1)


async def reads(question: str, **reading: Any) -> Understanding | None:
    out = await read(question, VOCAB, Model(**reading), today=TODAY)  # type: ignore[arg-type]
    return out[0] if out else None


def tied(question: str, metric: MetricEntry, **kw: Any) -> str:
    return grounding(question, Understanding("proceed", metric=metric, **kw), VOCAB, today=TODAY)


def test_every_quantity_word_named_by_the_metric_or_its_synonym_is_full() -> None:
    assert tied("Doanh thu thuần tháng này", REVENUE) == "full"
    assert tied("lợi nhuận quý 3", PROFIT) == "full"  # an approved synonym
    assert tied("doanh thu theo khu vực tháng 9/2026", REVENUE, group_by=["khu_vuc"]) == "full"
    # A follow-up names no quantity of its own.
    assert tied("còn theo khu vực thì sao?", REVENUE, group_by=["khu_vuc"]) == "full"


def test_words_the_metric_does_not_explain_make_it_partial_and_none_shared_make_it_none() -> None:
    assert tied("Doanh thu phí bảo hiểm nhân thọ tháng này", REVENUE) == "partial"
    assert tied("Tỷ lệ nợ xấu tháng này", REVENUE) == "none"


async def test_a_reading_the_question_only_half_names_is_asked_back_not_answered() -> None:
    u = await reads("Doanh thu phí bảo hiểm nhân thọ tháng này", metric="NET_REVENUE")
    assert u is not None and u.decision == "clarify" and u.options == [REVENUE] and u.metric is None


async def test_a_paraphrase_sharing_no_word_is_the_models_reading_to_make() -> None:
    u = await reads("Tiền bán hàng tháng này", metric="NET_REVENUE")
    assert u is not None and u.decision == "proceed" and u.metric == REVENUE
    # One word in common is not a paraphrase: "Lãi sau thuế" may not be "Lãi ròng", so it asks.
    asked = await reads("Lãi sau thuế quý 3", metric="NET_PROFIT")
    assert asked is not None and asked.decision == "clarify" and asked.options == [PROFIT]


async def test_a_named_reading_is_answered_as_before() -> None:
    u = await reads(
        "Doanh thu thuần theo khu vực tháng này", metric="NET_REVENUE", group_by=["khu_vuc"]
    )
    assert u is not None and u.decision == "proceed" and u.metric == REVENUE
