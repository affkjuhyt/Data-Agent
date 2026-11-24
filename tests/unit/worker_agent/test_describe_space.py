"""An overview is answered from the space the asker may see, never by picking a metric."""

from __future__ import annotations

import pytest

from services.worker_agent.ask.contract import NumericIntent, Purpose
from services.worker_agent.ask.describe import SpaceOverview, answers_from_context, describe_space

pytestmark = [pytest.mark.unit]


def test_disabled_tables_are_named_and_not_offered_as_a_metric_menu() -> None:
    text = describe_space(
        SpaceOverview(
            readable=(),
            disabled=("tlc.yellow_trip", "tlc.green_trip"),
            certified=(),
            drafts=(),
            documents=(),
        )
    )
    assert "tlc.yellow_trip" in text
    assert "tắt" in text
    assert "chọn" not in text
    assert "không nhận ra chỉ số" not in text


def test_a_readable_space_names_tables_metrics_and_documents() -> None:
    text = describe_space(
        SpaceOverview(
            readable=("aw.fact_internet_sales",),
            disabled=(),
            certified=("Doanh thu",),
            drafts=("Lãi gộp Internet",),
            documents=("Chính sách đổi trả",),
        )
    )
    assert "aw.fact_internet_sales" in text
    assert "Doanh thu" in text
    assert "Lãi gộp Internet" in text
    assert "Chính sách đổi trả" in text


def test_an_empty_space_says_there_is_nothing_to_read() -> None:
    text = describe_space(SpaceOverview((), (), (), (), ()))
    assert "không có" in text
    assert "chọn" not in text


@pytest.mark.parametrize(
    "text",
    [
        "Các dữ liệu ở đây là về gì?",
        "Có những bảng nào?",
        "Có tài liệu gì?",
    ],
)
def test_an_overview_has_no_numeric_intent(text: str) -> None:
    contract = answers_from_context("UNCATALOGUED", text)
    assert contract is not None
    assert contract.purpose is Purpose.ORIENT
    assert contract.numeric_intent is NumericIntent.NONE
    assert contract.needs_metric_choice() is False


@pytest.mark.parametrize(
    ("reason", "text"),
    [
        ("UNCATALOGUED", "Giá cổ phiếu của công ty hôm nay"),
        ("AMBIGUOUS", "Lãi gộp năm 2013"),
        ("RESOLVED", "Doanh thu năm 2013"),
        ("DOC_ONLY", "Chính sách đổi trả hàng là gì?"),
    ],
)
def test_asking_which_documents_exist_is_an_overview_on_the_document_route() -> None:
    contract = answers_from_context("DOC_ONLY", "Có tài liệu gì?")
    assert contract is not None
    assert contract.purpose is Purpose.ORIENT
    assert contract.needs_metric_choice() is False


def test_a_figure_or_a_governed_question_is_not_an_overview(reason: str, text: str) -> None:
    assert answers_from_context(reason, text) is None
