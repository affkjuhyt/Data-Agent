"""The next questions an answer offers (suggestions.py): built from the metric and its allowed cuts,
each read back through the Understander and compiled, so a click is answered."""

from __future__ import annotations

import datetime
import uuid

import pytest

from libs.language import use_locale
from services.worker_agent import suggestions
from services.worker_agent.semantic import compiler as c
from services.worker_agent.semantic.loader import SpaceLayer
from services.worker_agent.understand.resolver import (
    DimensionEntry,
    MetricEntry,
    Vocabulary,
    understand,
)

pytestmark = [pytest.mark.unit]

ORDERS, SALE = uuid.uuid4(), uuid.uuid4()
DAY = c.Dimension(uuid.uuid4(), "ngay", "Ngày", "order_date", SALE, is_time=True)
REGION = c.Dimension(uuid.uuid4(), "khu_vuc", "Khu vực", "region", SALE)
CHANNEL = c.Dimension(uuid.uuid4(), "kenh", "Kênh", "channel", SALE)
HIDDEN = c.Dimension(uuid.uuid4(), "ma_kh", "Mã khách", "customer_id", SALE)
METRIC_ID = uuid.uuid4()
VERSION = c.MetricVersion(
    id=uuid.uuid4(),
    metric_code="DOANH_THU",
    display_name="Doanh thu",
    expression="doanh_thu",
    filters=(),
    unit="USD",
    decimals=0,
    period_semantics="sum_over_period",
    # The customer code is no cut the owner allows: it is never offered.
    allowed_dimension_ids=frozenset({REGION.id, CHANNEL.id}),
    certified=True,
)
LAYER = SpaceLayer(
    vocabulary=Vocabulary(
        metrics=[MetricEntry(METRIC_ID, "DOANH_THU", "Doanh thu", certified=True)],
        dimensions=[
            DimensionEntry(d.id, d.code, d.name, d.is_time) for d in (DAY, REGION, CHANNEL, HIDDEN)
        ],
    ),
    layer=c.Layer(
        measures={"doanh_thu": c.Measure("doanh_thu", "SUM(amount)", SALE)},
        dimensions={d.code: d for d in (DAY, REGION, CHANNEL, HIDDEN)},
        entity_tables={SALE: c.Table(ORDERS, None, "orders")},
    ),
    versions={METRIC_ID: VERSION},
    version_rows={},
    datasets={},
    blocked_metric_ids=frozenset(),
)
TODAY = datetime.date(2014, 1, 15)


def test_an_answer_offers_questions_that_run() -> None:
    u = understand("Doanh thu năm 2013", LAYER.vocabulary, today=TODAY)
    offered = suggestions.follow_ups(u, LAYER, today=TODAY)
    assert offered == [
        "Doanh thu năm 2013 so với kỳ trước",
        "Doanh thu theo tháng năm 2013",
        "Doanh thu theo khu vực năm 2013",
        "Doanh thu theo kênh năm 2013",
    ]
    assert not any("mã khách" in q.lower() for q in offered)


def test_what_the_answer_already_does_is_not_offered_again() -> None:
    u = understand("Doanh thu theo khu vực năm 2013", LAYER.vocabulary, today=TODAY)
    offered = suggestions.follow_ups(u, LAYER, today=TODAY, compared=True)
    assert not any("so với" in q for q in offered)
    assert "Doanh thu theo khu vực năm 2013" not in offered
    assert "Doanh thu theo kênh năm 2013" in offered


def test_offers_follow_the_organisations_language() -> None:
    u = understand("Doanh thu năm 2013", LAYER.vocabulary, today=TODAY)
    with use_locale("en-US"):
        u = understand("Doanh thu năm 2013", LAYER.vocabulary, today=TODAY)
        offered = suggestions.follow_ups(u, LAYER, today=TODAY)
    # English offers read back only where the vocabulary has English words; the metric here is
    # named in Vietnamese only, so an English offer that would not match is dropped, not shown.
    assert all(q.startswith(("Doanh thu", "Monthly", "Top 5")) for q in offered)


def test_a_metric_the_space_does_not_have_offers_nothing() -> None:
    u = understand("Lợi nhuận năm 2013", LAYER.vocabulary, today=TODAY)
    assert suggestions.follow_ups(u, LAYER, today=TODAY) == []
