"""AL-08: causes are said only as hypotheses, each with the question that checks it; the answer's
other text loses a sentence that words a cause before it is stored; a recommendation brings no
number of its own."""

from __future__ import annotations

import pytest

from libs.language import causal
from services.worker_agent.agentic import blocks as b
from services.worker_agent.agentic.workspace import Workspace

pytestmark = [pytest.mark.unit, pytest.mark.req("AL-08")]


@pytest.mark.parametrize(
    "sentence",
    [
        "Doanh thu giảm vì vậy lợi nhuận giảm.",
        "Việc tăng giá đã dẫn đến số đơn giảm.",
        "Nguyên nhân là khách hàng rời đi.",
        "Thời tiết xấu khiến lượng khách giảm.",
        "Doanh thu tăng bởi vì mùa lễ.",
    ],
)
def test_a_sentence_that_words_a_cause_is_found(sentence: str) -> None:
    assert causal.is_causal(sentence)


@pytest.mark.parametrize(
    "sentence",
    [
        "Doanh thu tháng 12 tăng 5,2% so với tháng 11.",
        "Phần thay đổi do tỷ lệ chuyển đổi là 40%.",
        "Đây là quan sát thống kê, không phải nguyên nhân.",
    ],
)
def test_a_reading_or_the_disclaimer_is_not_a_cause(sentence: str) -> None:
    assert not causal.is_causal(sentence)


def test_dropping_keeps_the_other_sentences_and_lines() -> None:
    text = "Doanh thu tăng 5%. Điều này dẫn đến lợi nhuận cao hơn.\n- Miền Bắc dẫn đầu.\n- Vì vậy nên mở rộng."
    kept, dropped = causal.without_causes(text)
    assert dropped == 2
    assert "Doanh thu tăng 5%." in kept and "Miền Bắc dẫn đầu" in kept
    assert "dẫn đến" not in kept and "Vì vậy" not in kept


def test_the_eval_scorer_counts_with_the_same_rule() -> None:
    from model.eval.scorers import causal as scorer

    assert scorer.causal_sentences("A tăng. B dẫn đến C.") == ["B dẫn đến C."]


def _draft(**kw: object) -> b.BlockDraft:
    return b.BlockDraft(**kw)  # type: ignore[arg-type]


def test_a_hypothesis_block_holds_at_most_three_checks_and_no_number() -> None:
    ws = Workspace()
    hyps = [
        b.HypothesisDraft(text=f"Giả thuyết {chr(65 + i)}", check=f"Câu hỏi {chr(65 + i)}")
        for i in range(5)
    ]
    block = b.materialize(_draft(type="hypothesis", hypotheses=hyps), ws, "b1")
    assert block["label"] == "Giả thuyết" and len(block["items"]) == b.MAX_HYPOTHESES
    assert block["items"][0] == {
        "text": "Giả thuyết A",
        "check": "Câu hỏi A",
        "status": "unchecked",
    }
    with pytest.raises(b.BlockError):
        b.materialize(
            _draft(type="hypothesis", hypotheses=[b.HypothesisDraft(text="Giảm 12%", check="q")]),
            ws,
            "b1",
        )
    with pytest.raises(b.BlockError):
        b.materialize(
            _draft(type="hypothesis", hypotheses=[b.HypothesisDraft(text="Có", check=" ")]),
            ws,
            "b1",
        )


def test_a_recommendation_block_is_advice() -> None:
    block = b.materialize(
        _draft(
            type="recommendation",
            recommendations=[
                b.RecommendationDraft(action="Rà soát kênh bán", condition=" ", watch="Số đơn")
            ],
        ),
        Workspace(),
        "b1",
    )
    assert block["label"] == "Khuyến nghị"
    assert block["items"] == [{"action": "Rà soát kênh bán", "watch": "Số đơn"}]
