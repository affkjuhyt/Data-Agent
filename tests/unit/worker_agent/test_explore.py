"""When the asked period has no data, the agent looks for the periods that do (explore.py)."""

from __future__ import annotations

import datetime

import pytest

from services.worker_agent import explore
from services.worker_agent.semantic.compiler import Period

pytestmark = [pytest.mark.unit]

D = datetime.date


@pytest.mark.parametrize(
    ("period", "grain"),
    [
        (Period(D(2026, 1, 1), D(2027, 1, 1)), "year"),
        (Period(D(2026, 4, 1), D(2026, 7, 1)), "quarter"),
        (Period(D(2026, 9, 1), D(2026, 10, 1)), "month"),
        (Period(D(2026, 9, 7), D(2026, 9, 14)), "week"),
        (Period(D(2026, 9, 7), D(2026, 9, 8)), "day"),
    ],
)
def test_the_grain_follows_the_asked_period(period: Period, grain: str) -> None:
    assert explore.grain_of(period) == grain


def test_a_bucket_is_one_grain_long() -> None:
    assert explore.bucket(D(2014, 1, 1), "year") == Period(D(2014, 1, 1), D(2015, 1, 1))
    assert explore.bucket(D(2013, 10, 1), "quarter") == Period(D(2013, 10, 1), D(2014, 1, 1))
    assert explore.bucket(D(2013, 12, 1), "month") == Period(D(2013, 12, 1), D(2014, 1, 1))
    assert explore.bucket(D(2013, 12, 30), "week") == Period(D(2013, 12, 30), D(2014, 1, 6))


def test_rows_of_the_probe_become_the_periods_with_data() -> None:
    rows = [["2012-01-01", 10], ["2013-01-01T00:00:00", None], [D(2014, 1, 1), 3], [None, 4]]
    assert explore.periods_with_data(rows) == [D(2012, 1, 1), D(2014, 1, 1)]


def test_the_compilers_time_labels_are_read_at_every_grain() -> None:
    # semantic/compiler.py _at_grain: '2014', '2014-Q3', '2014-08', '2014-08-04'.
    rows = [["2014", 1], ["2013-Q3", 1], ["2012-08", 1], ["2011-08-04", 1], ["FY2015", 1]]
    assert explore.periods_with_data(rows) == [
        D(2011, 8, 4),
        D(2012, 8, 1),
        D(2013, 7, 1),
        D(2014, 1, 1),
    ]


def test_the_latest_period_before_the_asked_one_is_chosen() -> None:
    asked = Period(D(2026, 1, 1), D(2027, 1, 1))
    found = [D(2011, 1, 1), D(2013, 1, 1), D(2014, 1, 1)]
    assert explore.nearest(found, asked) == D(2014, 1, 1)


def test_a_period_before_all_data_takes_the_first_after_it() -> None:
    asked = Period(D(2005, 1, 1), D(2006, 1, 1))
    assert explore.nearest([D(2011, 1, 1), D(2014, 1, 1)], asked) == D(2011, 1, 1)


def test_no_data_at_all_chooses_nothing() -> None:
    assert explore.nearest([], Period(D(2026, 1, 1), D(2027, 1, 1))) is None


def test_the_note_says_what_was_asked_what_exists_and_what_is_shown() -> None:
    note = explore.note(
        Period(D(2026, 1, 1), D(2027, 1, 1)),
        Period(D(2014, 1, 1), D(2015, 1, 1)),
        [D(2011, 1, 1), D(2014, 1, 1)],
        "year",
    )
    assert note == (
        "Không có dữ liệu cho năm 2026; dữ liệu có từ năm 2011 đến năm 2014. "
        "Dưới đây là số của năm 2014."
    )


def test_the_question_is_retold_for_the_period_answered() -> None:
    # The period's span is the folded words it was read from (understand/packs): "nam nay".
    assert (
        explore.retold("Top 5 khu vực có số lượng bán cao nhất năm nay", "nam nay", "năm 2014")
        == "Top 5 khu vực có số lượng bán cao nhất năm 2014"
    )
    assert explore.retold("Doanh thu NĂM NAY?", "nam nay", "năm 2014") == "Doanh thu năm 2014?"
    # Its words not found: the period answered follows the question.
    assert (
        explore.retold("Doanh thu kỳ này", "nam nay", "năm 2014")
        == "Doanh thu kỳ này (số của năm 2014)"
    )


def test_names_left_unread_are_the_candidates_to_look_up() -> None:
    # A capitalised run of words the reader did not place (its folded words are still in the
    # rest): "Miền Nam". The first word of the question and placed words are not candidates.
    assert explore.unread_names("Tỷ lệ nợ xấu Miền Nam tháng 9/2026", "mien nam") == ["mien nam"]
    assert explore.unread_names("Doanh thu North America năm 2013", "north america") == [
        "north america"
    ]
    assert explore.unread_names("Doanh thu Internet năm 2013", "") == []
    assert explore.unread_names("Tỷ lệ nợ xấu tháng 9", "ty le") == []
    assert explore.unread_names("Số bán ở “Hà Nội” và Đà Nẵng", "ha noi va da nang") == [
        "ha noi",
        "da nang",
    ]


def test_a_member_matches_a_name_by_its_folded_words() -> None:
    assert explore.matching_member(["Miền Bắc", "Miền Nam"], "mien nam") == "Miền Nam"
    assert explore.matching_member(["North America", "Europe"], "america") is None
    assert explore.matching_member([None, "Miền Nam"], "mien nam") == "Miền Nam"


def test_a_name_found_nowhere_is_taken_out_of_the_question_the_model_reads() -> None:
    assert (
        explore.without("Số lượng bán Internet ở Atlantis năm 2013", "atlantis")
        == "Số lượng bán Internet năm 2013"
    )
    assert explore.without("Doanh thu “Hà Nội” năm 2013", "ha noi") == "Doanh thu năm 2013"


def test_a_name_is_shown_as_the_person_wrote_it() -> None:
    assert explore.as_written("Số lượng bán Internet ở Atlantis năm 2013", "atlantis") == "Atlantis"
    assert explore.as_written("Doanh thu ở đâu", "atlantis") == "atlantis"
