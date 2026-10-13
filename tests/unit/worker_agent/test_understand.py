"""The Understander's rule pass: Vietnamese periods, metric/cut/filter chips, clarification."""

from __future__ import annotations

import datetime
import uuid

import pytest

from services.worker_agent.understand.resolver import (
    DimensionEntry,
    MetricEntry,
    ValueEntry,
    Vocabulary,
    understand,
)
from services.worker_agent.understand.text import find_period, norm

pytestmark = [pytest.mark.unit]

TODAY = datetime.date(2026, 10, 12)
NPL = MetricEntry(uuid.uuid4(), "NPL_RATIO", "Tỷ lệ nợ xấu", certified=True)
BAL = MetricEntry(uuid.uuid4(), "DU_NO", "Dư nợ", certified=True)
BAL2 = MetricEntry(uuid.uuid4(), "DU_NO_GOP", "Dư nợ gộp", certified=False)
REGION = DimensionEntry(uuid.uuid4(), "vung", "vùng")
VOCAB = Vocabulary(
    metrics=[NPL, BAL, BAL2],
    dimensions=[REGION],
    values=[ValueEntry("vung", "MB", "Miền Bắc"), ValueEntry("vung", "MN", "Miền Nam")],
    synonyms={"no xau": ("metric", str(NPL.metric_id))},
)


@pytest.mark.parametrize(
    ("text", "start", "end", "label"),
    [
        ("tháng 9/2026", datetime.date(2026, 9, 1), datetime.date(2026, 10, 1), "tháng 9/2026"),
        (
            "tháng 12 năm 2025",
            datetime.date(2025, 12, 1),
            datetime.date(2026, 1, 1),
            "tháng 12/2025",
        ),
        ("quý III năm 2026", datetime.date(2026, 7, 1), datetime.date(2026, 10, 1), "quý 3/2026"),
        ("quý 4", datetime.date(2026, 10, 1), datetime.date(2027, 1, 1), "quý 4/2026"),
        ("năm 2025", datetime.date(2025, 1, 1), datetime.date(2026, 1, 1), "năm 2025"),
        ("tháng trước", datetime.date(2026, 9, 1), datetime.date(2026, 10, 1), "tháng 9/2026"),
        ("quý trước", datetime.date(2026, 7, 1), datetime.date(2026, 10, 1), "quý 3/2026"),
        ("năm ngoái", datetime.date(2025, 1, 1), datetime.date(2026, 1, 1), "năm 2025"),
        ("ngày 30/09/2026", datetime.date(2026, 9, 30), datetime.date(2026, 10, 1), "30/09/2026"),
    ],
)
def test_vietnamese_periods(
    text: str, start: datetime.date, end: datetime.date, label: str
) -> None:
    found = find_period(f"nợ xấu {text} là bao nhiêu", TODAY)
    assert found is not None and (found.start, found.end, found.label) == (start, end, label)


def test_norm_strips_accents_and_d() -> None:
    assert norm("Đà Nẵng, Dư NỢ!") == "da nang du no"


def test_a_metric_cut_by_region_filtered_and_in_a_month() -> None:
    u = understand("Tỷ lệ nợ xấu theo vùng ở Miền Bắc tháng 9/2026", VOCAB, today=TODAY)
    assert u.decision == "proceed" and u.metric == NPL
    assert u.group_by == ["vung"] and u.filters == {"vung": ["MB"]}
    assert u.period is not None and u.period.label == "tháng 9/2026"
    assert u.confidence == 1.0


def test_a_synonym_is_trusted_a_little_less() -> None:
    u = understand("nợ xấu quý trước", VOCAB, today=TODAY)
    assert u.decision == "proceed" and u.metric == NPL and u.confidence == 0.9


def test_the_longer_name_wins() -> None:
    u = understand("dư nợ gộp năm nay", VOCAB, today=TODAY)
    assert u.metric == BAL2


def test_two_metrics_named_as_well_as_each_other_ask_which() -> None:
    u = understand("so sánh dư nợ và tỷ lệ nợ xấu", VOCAB, today=TODAY)
    assert u.decision == "clarify" and {m.code for m in u.options} == {"DU_NO", "NPL_RATIO"}


def test_nothing_known_is_unknown_and_reported() -> None:
    u = understand("doanh thu bảo hiểm nhân thọ tháng này", VOCAB, today=TODAY)
    assert u.decision == "unknown" and u.unmapped
