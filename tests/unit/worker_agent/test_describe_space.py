"""An overview is answered from the space the asker may see, never by picking a metric."""

from __future__ import annotations

import uuid

import pytest

from services.worker_agent.ask.contract import NumericIntent, Purpose
from services.worker_agent.ask.describe import (
    SpaceOverview,
    answers_from_context,
    describe_members,
    describe_space,
    listed_dimension,
)
from services.worker_agent.understand.resolver import (
    DimensionEntry,
    MetricEntry,
    ValueEntry,
    Vocabulary,
)

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


def test_asking_which_documents_exist_is_an_overview_on_the_document_route() -> None:
    contract = answers_from_context("DOC_ONLY", "Có tài liệu gì?")
    assert contract is not None
    assert contract.purpose is Purpose.ORIENT
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
def test_a_figure_or_a_governed_question_is_not_an_overview(reason: str, text: str) -> None:
    assert answers_from_context(reason, text) is None


def test_the_question_of_24_11_is_an_overview() -> None:
    # "thế dữ liệu chúng ta đang trao đổi nói về gì" was read as the last metric.
    assert answers_from_context("UNCATALOGUED", "thế dữ liệu chúng ta đang trao đổi nói về gì")


VOCAB = Vocabulary(
    metrics=[MetricEntry(uuid.uuid4(), "DOANH_THU", "Doanh thu", True)],
    dimensions=[
        DimensionEntry(uuid.uuid4(), "nhom_hang", "Nhóm sản phẩm"),
        DimensionEntry(uuid.uuid4(), "ten_san_pham", "Sản phẩm"),
    ],
    values=[ValueEntry("nhom_hang", m, m) for m in ("Bikes", "Accessories", "Clothing")],
)
VOCAB.synonyms = {"mat hang": ("dimension", str(VOCAB.dimensions[1].dimension_id))}


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("Có những nhóm sản phẩm nào?", "nhom_hang"),
        ("nhóm sản phẩm gồm những gì", "nhom_hang"),
        ("mặt hàng đang được bán là mặt hàng nào", "ten_san_pham"),
        ("Doanh thu theo nhóm sản phẩm nào cao nhất", None),
        ("Nhóm sản phẩm năm 2013", None),
        ("Bikes bán ở đâu", None),
    ],
)
def test_a_listing_names_one_dimension_and_asks_nothing_else(text: str, code: str | None) -> None:
    assert listed_dimension(text, VOCAB) == code


def test_members_are_listed_and_the_rest_counted() -> None:
    assert describe_members("Nhóm sản phẩm", ["Accessories", "Bikes"]) == (
        "Nhóm sản phẩm có 2 giá trị: Accessories, Bikes."
    )
    many = [f"M{n:02d}" for n in range(60)]
    assert describe_members("X", many).endswith("M49 và 10 giá trị khác.")
