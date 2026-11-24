"""AE-11 / RG-07 (ASK_ENGINE_SPEC §8.2): a follow-up to the documents is searched as a question that
stands alone, rewritten deterministically from what the conversation settled."""

from __future__ import annotations

import pytest

from services.worker_agent.understand import slots as sl

pytestmark = [pytest.mark.unit]

SLOTS = sl.from_chips(
    [
        {"kind": "metric", "value": "DOANH_THU", "label": "Doanh thu"},
        {"kind": "dimension", "value": "nhom_hang", "label": "Nhóm sản phẩm"},
        {"kind": "period", "value": "2013-01-01/2014-01-01", "label": "năm 2013"},
    ],
    figures=[
        {"key": "F1", "value": "700000", "dimensions": {"nhom_hang": "Accessories"}},
        {"key": "F2", "value": "28000000", "dimensions": {"nhom_hang": "Bikes"}},
    ],
)


def test_the_subject_takes_the_place_of_that() -> None:
    assert (
        sl.rewrite_for_retrieval("Tài liệu nào định nghĩa nhóm sản phẩm đó?", SLOTS)
        == "Tài liệu nào định nghĩa nhóm sản phẩm Bikes? Doanh thu năm 2013"
    )


def test_a_short_follow_up_gets_the_metric_and_period() -> None:
    assert sl.rewrite_for_retrieval("định nghĩa là gì?", SLOTS) == (
        "định nghĩa là gì? Doanh thu năm 2013"
    )


def test_what_it_already_says_is_not_added_again() -> None:
    assert sl.rewrite_for_retrieval("Còn doanh thu năm 2013 được tính thế nào", SLOTS) == (
        "Còn doanh thu năm 2013 được tính thế nào"
    )


def test_a_question_that_stands_alone_is_searched_as_asked() -> None:
    asked = "Chính sách đổi trả hàng của công ty quy định những gì"
    assert sl.rewrite_for_retrieval(asked, SLOTS) == asked
    assert sl.rewrite_for_retrieval("định nghĩa là gì?", None) == "định nghĩa là gì?"
    assert sl.rewrite_for_retrieval("định nghĩa là gì?", sl.ConversationSlots()) == (
        "định nghĩa là gì?"
    )
