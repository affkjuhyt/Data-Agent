"""An overview is answered from the space the asker may see, never by picking a metric."""

from __future__ import annotations

import uuid

import pytest

from services.worker_agent.agentic.corpus import CorpusBrief, CorpusDoc, CorpusMetric, CorpusTable
from services.worker_agent.ask.contract import NumericIntent, Purpose
from services.worker_agent.ask.describe import (
    TIME_CUT,
    SpaceOverview,
    answers_from_context,
    asks_for_tables,
    describe_members,
    describe_space,
    listed_dimension,
    overview_of,
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
            tables_asked=True,
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
            tables_asked=True,
        )
    )
    assert "aw.fact_internet_sales" in text
    assert "Doanh thu" in text
    assert "Lãi gộp Internet" in text
    assert "Chính sách đổi trả" in text


def _brief(tables: list[str]) -> CorpusBrief:
    return CorpusBrief(
        tables=tuple(CorpusTable(t, t, None, ()) for t in tables),
        joins=(),
        metrics=(
            CorpusMetric("DOANH_THU", "Doanh thu", True),
            CorpusMetric("SO_DON", "Số đơn Internet", True),
            CorpusMetric("TIP", "Tiền tip", False),
        ),
        dimensions=(),
        documents=(CorpusDoc("Chính sách và báo cáo kinh doanh", ("pdf",)),),
        pages=(),
    )


AW = "Kho dữ liệu mẫu AdventureWorksDW của Microsoft (nhà sản xuất xe đạp): bán qua Internet."


def test_what_the_data_is_about_is_its_description_and_what_can_be_asked_not_its_tables() -> None:
    # 25/11: "bộ dữ liệu này đang nói về gì" was answered with 46 table names, each twice.
    tables = ["public.fact_internet_sales", "public.dim_product", "public.fact_internet_sales"]
    text = describe_space(
        overview_of(
            _brief(tables),
            ("public.dim_currency", "public.dim_product"),
            name="AdventureWorks",
            description=AW,
            dimensions=(TIME_CUT, "Nhóm sản phẩm", "Khu vực"),
        )
    )
    assert text.startswith(f"**AdventureWorks**: {AW}")
    assert "2 chỉ số đã chứng nhận: Doanh thu, Số đơn Internet." in text
    assert "1 chỉ số chưa chứng nhận" in text and "Tiền tip" in text
    assert "Xem theo: thời gian (ngày, tháng, quý, năm), Nhóm sản phẩm, Khu vực." in text
    assert "Chính sách và báo cáo kinh doanh" in text
    # Counted, not named; a table readable through one source is not also "đang tắt".
    assert "public." not in text
    assert "2 bảng truy vấn được, 1 bảng đang tắt" in text
    assert "“Doanh thu theo tháng”" in text and "“Số đơn Internet theo nhóm sản phẩm”" in text


def test_asked_for_its_tables_each_is_named_once() -> None:
    tables = ["public.fact_internet_sales", "public.dim_product", "public.fact_internet_sales"]
    space = overview_of(
        _brief(tables),
        ("public.dim_currency", "public.dim_product"),
        tables_asked=asks_for_tables("Có những bảng nào?"),
    )
    text = describe_space(space)
    assert text.count("public.fact_internet_sales") == 1
    assert "2 bảng truy vấn được: public.fact_internet_sales, public.dim_product." in text
    assert "1 bảng đang tắt, chưa truy vấn được: public.dim_currency." in text
    assert not asks_for_tables("bộ dữ liệu này đang nói về gì")


def test_an_empty_space_says_there_is_nothing_to_read() -> None:
    text = describe_space(SpaceOverview((), (), (), (), ()))
    assert "không có" in text
    assert "chọn" not in text


@pytest.mark.parametrize(
    "text",
    [
        "bộ dữ liệu này đang nói về gì",
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
