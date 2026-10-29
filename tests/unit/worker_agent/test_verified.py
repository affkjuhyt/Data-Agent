"""Which question an approved query answers, and the period it binds (sqlgen/verified.py, R1-08)."""

from __future__ import annotations

import datetime
import uuid

import pytest

from services.worker_agent.sqlgen.verified import bind, match

pytestmark = [pytest.mark.unit]

TODAY = datetime.date(2026, 10, 30)
BY_CHANNEL = uuid.uuid4()
FIXED = uuid.uuid4()
EXAMPLES = [
    (
        BY_CHANNEL,
        "Doanh thu theo kênh tháng 9/2026",
        "SELECT kenh, sum(tien) AS doanh_thu FROM ban_hang"
        " WHERE ngay >= :period_start AND ngay < :period_end GROUP BY kenh",
    ),
    (FIXED, "Số khách hàng đang hoạt động", "SELECT count(*) FROM khach_hang WHERE active"),
]


def test_a_question_with_another_period_takes_the_approved_query_and_its_own_dates() -> None:
    found = match("Doanh thu theo kênh tháng này?", EXAMPLES, TODAY)
    assert found is not None and found.example_id == BY_CHANNEL
    assert "ngay >= '2026-10-01' AND ngay < '2026-11-01'" in found.sql
    assert found.period is not None and found.period.label == "tháng 10/2026"


def test_a_question_without_a_period_binds_the_examples_own() -> None:
    found = match("doanh thu theo kênh", EXAMPLES, TODAY)
    assert found is not None
    assert "'2026-09-01'" in found.sql and "'2026-10-01'" in found.sql


def test_a_fixed_query_answers_only_its_own_question_word_for_word() -> None:
    assert match("Số khách hàng đang hoạt động", EXAMPLES, TODAY) is not None
    assert match("Số khách hàng đang hoạt động tháng này", EXAMPLES, TODAY) is None


def test_another_question_matches_nothing() -> None:
    assert match("Doanh thu theo vùng tháng này", EXAMPLES, TODAY) is None


def test_no_period_anywhere_binds_the_current_month() -> None:
    sql = bind("SELECT 1 WHERE d >= :period_start AND d < :period_end", None, TODAY)
    assert sql == "SELECT 1 WHERE d >= '2026-10-01' AND d < '2026-11-01'"
