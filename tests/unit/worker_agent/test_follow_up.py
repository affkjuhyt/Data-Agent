"""A follow-up read against the question before it (UC-01-04)."""

from __future__ import annotations

import datetime
import uuid

import pytest

from services.worker_agent.understand.resolver import (
    DimensionEntry,
    MetricEntry,
    ValueEntry,
    Vocabulary,
    follow_up,
    understand,
)

pytestmark = [pytest.mark.unit]

TODAY = datetime.date(2026, 10, 12)
NPL = MetricEntry(uuid.uuid4(), "NPL_RATIO", "Tỷ lệ nợ xấu", certified=True)
BAL = MetricEntry(uuid.uuid4(), "DU_NO", "Dư nợ", certified=True)
VOCAB = Vocabulary(
    metrics=[NPL, BAL],
    dimensions=[
        DimensionEntry(uuid.uuid4(), "vung", "vùng"),
        DimensionEntry(uuid.uuid4(), "chi_nhanh", "chi nhánh"),
    ],
    values=[ValueEntry("vung", "MB", "Miền Bắc"), ValueEntry("vung", "MN", "Miền Nam")],
)


def _ask(text: str, previous: str) -> tuple[list[tuple[str, str, str]], str]:
    before = understand(previous, VOCAB, today=TODAY)
    chips = [chip.__dict__ for chip in before.chips]
    u = follow_up(understand(text, VOCAB, today=TODAY), chips, VOCAB)
    return [(c.kind, c.value, c.source) for c in u.chips], u.decision


@pytest.mark.req("AC-01-04-01")
def test_a_new_cut_keeps_the_metric_and_period_from_before() -> None:
    chips, decision = _ask("còn theo vùng thì sao?", "Tỷ lệ nợ xấu tháng 9/2026")
    assert decision == "proceed"
    assert chips == [
        ("metric", "NPL_RATIO", "inherited"),
        ("period", "2026-09-01/2026-10-01", "inherited"),
        ("dimension", "vung", "default"),
    ]


@pytest.mark.req("AC-01-04-02")
def test_a_drill_down_filters_the_member_and_replaces_the_cut() -> None:
    chips, decision = _ask("Đi sâu Miền Bắc theo chi nhánh", "Tỷ lệ nợ xấu theo vùng tháng 9/2026")
    assert decision == "proceed"
    assert ("dimension", "vung", "inherited") not in chips
    assert ("filter", "vung=MB", "default") in chips
    assert ("dimension", "chi_nhanh", "default") in chips
    assert ("metric", "NPL_RATIO", "inherited") in chips


@pytest.mark.req("AC-01-04-07")
def test_a_filter_of_its_own_replaces_the_inherited_one_on_that_dimension() -> None:
    chips, _ = _ask("còn Miền Nam thì sao", "Dư nợ Miền Bắc tháng 9/2026")
    filters = [c for c in chips if c[0] == "filter"]
    assert filters == [("filter", "vung=MN", "default")]


@pytest.mark.req("AC-01-04-03")
def test_a_question_naming_its_own_metric_is_a_new_question() -> None:
    chips, _ = _ask("Dư nợ theo vùng tháng 8/2026", "Tỷ lệ nợ xấu tháng 9/2026")
    assert all(source != "inherited" for _, _, source in chips)


def test_nothing_the_space_knows_is_not_a_follow_up() -> None:
    chips, decision = _ask("cảm ơn nhé", "Tỷ lệ nợ xấu tháng 9/2026")
    assert (chips, decision) == ([], "unknown")


def test_no_earlier_metric_nothing_to_inherit() -> None:
    chips, decision = _ask("còn theo vùng thì sao?", "xin chào")
    assert decision == "unknown" and all(source != "inherited" for _, _, source in chips)
