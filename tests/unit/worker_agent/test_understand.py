"""The Understander's rule pass: Vietnamese periods, metric/cut/filter chips, clarification."""

from __future__ import annotations

import datetime
import uuid

import pytest

from libs import clock
from services.worker_agent.understand.grain import find_grain
from services.worker_agent.understand.resolver import (
    DimensionEntry,
    MetricEntry,
    ValueEntry,
    Vocabulary,
    understand,
)
from services.worker_agent.understand.text import (
    find_knowledge_hint,
    find_period,
    norm,
    words_of,
)

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


JULY = clock.Calendar(week_start=7, fiscal_year_start_month=7, fiscal_year_named_by="end")
D = datetime.date


@pytest.mark.parametrize(
    ("text", "start", "end", "label"),
    [
        ("năm tài chính 2026", D(2025, 7, 1), D(2026, 7, 1), "FY2026"),
        ("FY2026", D(2025, 7, 1), D(2026, 7, 1), "FY2026"),
        ("quý 2 năm tài chính 2026", D(2025, 10, 1), D(2026, 1, 1), "quý 2 FY2026"),
        ("Q1 FY2027", D(2026, 7, 1), D(2026, 10, 1), "quý 1 FY2027"),
        ("năm tài chính nay", D(2026, 7, 1), D(2027, 7, 1), "FY2027"),
        ("năm tài chính trước", D(2025, 7, 1), D(2026, 7, 1), "FY2026"),
        # 12/10/2026 is a Monday; this calendar's weeks start on Sunday.
        ("tuần này", D(2026, 10, 11), D(2026, 10, 18), "tuần 11/10 đến 17/10/2026"),
        ("tuần trước", D(2026, 10, 4), D(2026, 10, 11), "tuần 04/10 đến 10/10/2026"),
        # A plain year and quarter stay the calendar's.
        ("năm 2025", D(2025, 1, 1), D(2026, 1, 1), "năm 2025"),
        ("quý 1", D(2026, 1, 1), D(2026, 4, 1), "quý 1/2026"),
    ],
)
def test_fiscal_and_week_periods_follow_the_calendar(
    text: str, start: datetime.date, end: datetime.date, label: str
) -> None:
    found = find_period(f"doanh thu {text} là bao nhiêu", TODAY, JULY)
    assert found is not None and (found.start, found.end, found.label) == (start, end, label)


def test_without_a_fiscal_calendar_the_fiscal_year_is_the_calendar_year() -> None:
    found = find_period("doanh thu năm tài chính 2026", TODAY)
    assert found is not None and (found.start, found.end) == (D(2026, 1, 1), D(2027, 1, 1))
    week = find_period("doanh thu tuần này", TODAY)
    assert week is not None and week.start == D(2026, 10, 12)


@pytest.mark.parametrize(
    ("text", "grain"),
    [
        ("doanh thu theo tuần", "week"),
        ("doanh thu theo năm tài chính", "fiscal_year"),
        ("doanh thu từng quý tài chính", "fiscal_quarter"),
        ("doanh thu theo quý", "quarter"),
    ],
)
def test_week_and_fiscal_grains(text: str, grain: str) -> None:
    found = find_grain(text)
    assert found is not None and found.grain == grain and found.rest == "doanh thu"


def test_a_named_day_is_no_grain_and_a_time_cut_it_names_is_asked() -> None:
    # "theo ngày giao hàng": the ship date, which DS-AW does not model, was read as "by day" on
    # the order date (and, without the grain, as the call centre's "Ngày").
    assert find_grain("doanh thu theo ngày giao hàng") is None
    found = find_grain("doanh thu theo ngày tháng 12/2013")
    assert found is not None and found.grain == "day"
    day = DimensionEntry(uuid.uuid4(), "ngay", "Ngày", is_time=True)
    vocab = Vocabulary(metrics=[NPL], dimensions=[day], synonyms={})
    u = understand("Tỷ lệ nợ xấu theo ngày giao hàng tháng 12/2025", vocab, today=TODAY)
    assert u.decision == "clarify" and u.group_by == [] and u.unplaced_cut == "ngay giao hang"
    u = understand("Tỷ lệ nợ xấu theo ngày tháng 12/2025", vocab, today=TODAY)
    assert u.decision == "proceed" and u.group_by == ["ngay"]


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


def test_a_cut_the_space_does_not_know_asks_which_instead_of_being_dropped() -> None:
    # "theo khu vực" when the Space calls it "vùng": answering the total would answer
    # another question than the one asked.
    u = understand("Tỷ lệ nợ xấu theo khu vực tháng 9/2026", VOCAB, today=TODAY)
    assert u.decision == "clarify" and u.metric == NPL
    assert u.unplaced_cut == "khu vuc" and u.options == []
    assert not any(c.kind == "dimension" for c in u.chips)


def test_a_known_cut_leaves_nothing_unplaced() -> None:
    u = understand("Tỷ lệ nợ xấu theo vùng tháng 9/2026", VOCAB, today=TODAY)
    assert u.decision == "proceed" and u.group_by == ["vung"] and u.unplaced_cut is None


def test_each_before_a_known_cut_leaves_nothing_unplaced() -> None:
    # "theo từng vùng": with the region placed, "theo từng" was left and read as a cut named
    # "từng"; DS-AW asked back on "Doanh thu đại lý năm 2013 theo từng nhóm hàng".
    u = understand("Tỷ lệ nợ xấu tháng 9/2026 theo từng vùng", VOCAB, today=TODAY)
    assert u.decision == "proceed" and u.group_by == ["vung"] and u.unplaced_cut is None
    # A cut that is not known is still asked, without the "từng".
    u = understand("Tỷ lệ nợ xấu tháng 9/2026 theo từng khu vực", VOCAB, today=TODAY)
    assert u.decision == "clarify" and u.unplaced_cut == "khu vuc"


def test_a_name_said_in_part_asks_which_of_the_metrics_that_carry_it() -> None:
    # DS-AW: "Lãi gộp năm 2013" names neither "Lãi gộp Internet" nor "Lãi gộp đại lý" whole; it
    # was unknown, when the two metrics that carry those words are the question to ask.
    internet, resellers, revenue = (
        MetricEntry(uuid.uuid4(), code, name, True)
        for code, name in (
            ("LAI_GOP_INTERNET", "Lãi gộp Internet"),
            ("LAI_GOP_DAI_LY", "Lãi gộp đại lý"),
            ("DOANH_THU", "Doanh thu"),
        )
    )
    vocab = Vocabulary(metrics=[internet, resellers, revenue], dimensions=[], synonyms={})
    u = understand("Lãi gộp năm 2013", vocab, today=TODAY)
    assert u.decision == "clarify" and set(u.options) == {internet, resellers}
    assert understand("Lãi gộp Internet năm 2013", vocab, today=TODAY).metric == internet
    # Words of no metric's name leave it unknown, for the model reader.
    assert understand("Chi phí lương năm 2013", vocab, today=TODAY).decision == "unknown"


def test_a_ranked_question_adds_the_dimension_and_limit() -> None:
    product = DimensionEntry(uuid.uuid4(), "product", "Sản phẩm")
    vocab = Vocabulary(
        metrics=[NPL],
        dimensions=[product],
        synonyms={
            "complaint": ("metric", str(NPL.metric_id)),
            "product": ("dimension", str(product.dimension_id)),
        },
    )
    u = understand("complaint top 5 Product xuất hiện nhiều nhất", vocab, today=TODAY)
    assert u.decision == "proceed"
    assert u.group_by == ["product"] and u.top_n == 5 and u.top_desc


def test_a_month_number_is_not_a_top_n_and_a_bare_superlative_is_the_top_one() -> None:
    # GEN-03: "tháng 9 cao nhất" was read as top 9.
    region = DimensionEntry(uuid.uuid4(), "vung", "Vùng")
    vocab = Vocabulary(
        metrics=[NPL],
        dimensions=[region],
        synonyms={
            "no xau": ("metric", str(NPL.metric_id)),
            "vung": ("dimension", str(region.dimension_id)),
        },
    )
    u = understand("Vùng nào có nợ xấu tháng 9/2026 cao nhất?", vocab, today=TODAY)
    assert u.group_by == ["vung"] and u.top_n == 1 and u.top_desc
    assert u.period is not None and u.period.start == datetime.date(2026, 9, 1)
    low = understand("Vùng nào có nợ xấu tháng 9/2026 thấp nhất?", vocab, today=TODAY)
    assert low.top_n == 1 and not low.top_desc


def test_by_the_period_asked_is_about_time_not_a_missing_cut() -> None:
    # "theo kỳ được hỏi": the period is the question's, read by find_period, not a dimension.
    u = understand("Tỷ lệ nợ xấu theo kỳ được hỏi", VOCAB, today=TODAY)
    assert u.unplaced_cut is None and u.decision == "proceed"


def test_the_persons_own_words_are_found_for_a_normalised_phrase() -> None:
    assert words_of("Tỷ lệ nợ xấu theo Khu Vực tháng 9", "khu vuc") == "Khu Vực"
    assert words_of("abc", "khu vuc") == "khu vuc"


def test_latest_is_not_read_as_a_cut() -> None:
    # "mới" (new) and "mỗi" (each) are both "moi" without accents: only "theo" asks for a cut.
    u = understand("Báo tỷ lệ nợ xấu mới nhất cho chủ sở hữu", VOCAB, today=TODAY)
    assert u.unplaced_cut is None and u.decision == "proceed"


def test_as_submitted_phrase_selects_period_close_before_metric_matching() -> None:
    hint = find_knowledge_hint("NPL_RATIO tháng 6/2026 như đã nộp")
    assert hint is not None and hint.mode == "period_close" and hint.span == "nhu da nop"


def test_current_data_phrase_keeps_latest_mode() -> None:
    hint = find_knowledge_hint("NPL_RATIO tháng 6/2026 theo dữ liệu hiện tại")
    assert hint is not None and hint.mode == "latest"


def test_a_value_said_in_part_becomes_its_filter() -> None:
    # R1-05: "Hồ Chí Minh" names the stored value "TP. Hồ Chí Minh".
    vocab = Vocabulary(
        metrics=[BAL],
        dimensions=[REGION],
        values=[ValueEntry("vung", "HCM", "TP. Hồ Chí Minh"), ValueEntry("vung", "HN", "Hà Nội")],
    )
    u = understand("Dư nợ Hồ Chí Minh tháng 9/2026", vocab, today=TODAY)
    assert u.decision == "proceed" and u.filters == {"vung": ["HCM"]}
    assert [c.label for c in u.chips if c.kind == "filter"] == ["TP. Hồ Chí Minh"]
