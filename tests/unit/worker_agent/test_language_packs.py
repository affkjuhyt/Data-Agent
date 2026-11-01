"""The language packs (GEN-31): a question asked in English is read as its Vietnamese twin is,
once the Data Space's vocabulary has the English words for its own metrics and values (synonyms,
as a steward adds them). What the packs own is everything else: the period, the grain, the
comparison, the analysis, the ranking, the cut, the knowledge mode."""

from __future__ import annotations

import datetime
import uuid

import pytest

from libs.language import norm
from services.worker_agent.understand.analysis import find_analysis
from services.worker_agent.understand.comparison import find_comparison
from services.worker_agent.understand.grain import find_grain
from services.worker_agent.understand.resolver import (
    DimensionEntry,
    MetricEntry,
    ValueEntry,
    Vocabulary,
    understand,
)
from services.worker_agent.understand.text import find_knowledge_hint, find_period

pytestmark = [pytest.mark.unit]

TODAY = datetime.date(2026, 10, 12)
REVENUE = MetricEntry(uuid.uuid4(), "DOANH_THU", "Doanh thu", certified=True)
REGION = DimensionEntry(uuid.uuid4(), "khu_vuc", "Khu vực")
PRODUCT = DimensionEntry(uuid.uuid4(), "san_pham", "Sản phẩm")
VOCAB = Vocabulary(
    metrics=[REVENUE],
    dimensions=[REGION, PRODUCT],
    values=[ValueEntry("khu_vuc", "EU", "Châu Âu")],
    synonyms={
        "revenue": ("metric", str(REVENUE.metric_id)),
        "region": ("dimension", str(REGION.dimension_id)),
        "product": ("dimension", str(PRODUCT.dimension_id)),
        "products": ("dimension", str(PRODUCT.dimension_id)),
        "europe": ("value", "khu_vuc", "EU"),
    },
)


def reading(question: str) -> tuple[object, ...]:
    """What the rule pass and the readers before it take from a question."""
    # In the order questions.py reads them, each taking its words out.
    asked = question
    readers = []
    for read in (find_comparison, find_grain, find_analysis):
        found = read(asked)
        readers.append(found)
        if found is not None:
            asked = found.rest
    u = understand(asked, VOCAB, today=TODAY)
    period = (u.period.start, u.period.end) if u.period else None
    return (
        u.decision,
        u.metric.code if u.metric else None,
        tuple(u.group_by),
        tuple(sorted((k, tuple(v)) for k, v in u.filters.items())),
        period,
        u.top_n,
        u.top_desc,
        tuple(
            getattr(f, "compare_to", None) or getattr(f, "grain", None) or getattr(f, "kind", None)
            for f in readers
        ),
    )


@pytest.mark.parametrize(
    ("vi", "en"),
    [
        ("Doanh thu tháng 9/2026", "Revenue in September 2026"),
        ("Doanh thu quý 3 năm 2026 theo khu vực", "Revenue in Q3 2026 by region"),
        ("Doanh thu năm 2025 ở Châu Âu", "Revenue in 2025 in Europe"),
        ("Doanh thu tháng trước", "Revenue last month"),
        ("Doanh thu quý trước", "Revenue last quarter"),
        ("Doanh thu năm ngoái theo sản phẩm", "Revenue last year by product"),
        ("Doanh thu tuần này", "Revenue this week"),
        ("Doanh thu hôm nay", "Revenue today"),
        ("Doanh thu năm 2026 theo tháng", "Monthly revenue in 2026"),
        ("Doanh thu năm 2026 theo quý", "Revenue by quarter in 2026"),
        ("Doanh thu tháng 9/2026 so với tháng trước", "Revenue in September 2026 vs last month"),
        ("Doanh thu quý 3 năm 2026 so với cùng kỳ năm trước", "Revenue in Q3 2026 year over year"),
        ("Doanh thu luỹ kế từ đầu năm đến nay", "Revenue year to date"),
        ("Doanh thu luỹ kế theo tháng năm 2026", "Cumulative revenue by month in 2026"),
        ("Tỷ trọng doanh thu theo khu vực năm 2025", "Revenue share of total by region in 2025"),
        ("Top 5 sản phẩm doanh thu cao nhất năm 2025", "Top 5 products by revenue in 2025"),
        ("Khu vực doanh thu thấp nhất năm 2025", "Region with the lowest revenue in 2025"),
        ("Doanh thu năm tài chính 2026", "Revenue in fiscal year 2026"),
    ],
)
def test_an_english_question_reads_as_its_vietnamese_twin(vi: str, en: str) -> None:
    assert reading(en) == reading(vi)
    assert reading(vi)[0] == "proceed"


def test_iso_periods_read_in_any_language() -> None:
    for text, start, end in (
        ("doanh thu 2026-09-30", datetime.date(2026, 9, 30), datetime.date(2026, 10, 1)),
        ("revenue 2026-09", datetime.date(2026, 9, 1), datetime.date(2026, 10, 1)),
        ("revenue 2026-Q3", datetime.date(2026, 7, 1), datetime.date(2026, 10, 1)),
    ):
        found = find_period(text, TODAY)
        assert found is not None and (found.start, found.end) == (start, end), text


def test_may_alone_is_not_a_month() -> None:
    # "may" is a verb: only with its year, or as a day, is it the month.
    assert find_period("what may revenue be", TODAY) is None
    found = find_period("revenue in May 2026", TODAY)
    assert found is not None and found.start == datetime.date(2026, 5, 1)


def test_knowledge_modes_in_english() -> None:
    filed = find_knowledge_hint("Revenue for Q3 2026 as filed")
    assert filed is not None and filed.mode == "period_close"
    current = find_knowledge_hint("Revenue for Q3 2026 on current data")
    assert current is not None and current.mode == "latest"


def test_a_share_in_a_metric_name_is_not_an_analysis() -> None:
    assert find_analysis("market share by region") is None


def test_one_normal_form() -> None:
    # The api's phrase_norm and the worker's question text meet (punctuation to spaces).
    assert norm("Doanh-thu (thuần)") == norm("doanh thu thuan") == "doanh thu thuan"
    assert norm("Tỷ lệ % 9/2026") == "ty le % 9/2026"


def test_a_figure_reads_in_the_organisations_locale() -> None:
    import decimal

    from libs.language import number

    value = decimal.Decimal("1234567.891")
    assert number(value, 2) == "1.234.567,89"  # vi-VN, the default
    assert number(value, 2, "en-US") == "1,234,567.89"
    nbsp = chr(0xA0)
    assert number(value, 0, "fr-FR") == f"1{nbsp}234{nbsp}568"
    assert number(decimal.Decimal("-2.5"), 0, "en-US") == "-3"
