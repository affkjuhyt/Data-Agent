"""Semantic value retrieval: a keyword maps to the value the column stores (DeepEye-SQL §3.4)."""

from __future__ import annotations

import pytest

from services.worker_agent.understand.resolver import ValueEntry
from services.worker_agent.understand.value_retrieval import (
    FUZZY_AT,
    IndexDimension,
    ValueHit,
    dimension_column,
    index_entries,
    index_writes,
    sem_value_retrieval,
    similarity,
)

pytestmark = [pytest.mark.unit]


def test_a_keyword_resolves_to_the_stored_code() -> None:
    hits = sem_value_retrieval(
        "doanh thu Hải Phòng tháng trước",
        [ValueEntry("tinh", "HP01", "Hải Phòng")],
    )
    assert hits == [ValueHit("tinh", "HP01", "Hải Phòng", 1.0, "hai phong")]


def test_a_word_the_index_does_not_hold_is_not_invented() -> None:
    hits = sem_value_retrieval(
        "doanh thu Cần Thơ",
        [ValueEntry("tinh", "HP01", "Hải Phòng")],
    )
    assert hits == []


def test_the_longer_value_wins() -> None:
    hits = sem_value_retrieval(
        "biên lợi nhuận nhóm thời trang",
        [
            ValueEntry("nhom_hang", "TT", "Thời trang"),
            ValueEntry("nhom_hang", "T", "trang"),
        ],
    )
    assert [hit.value_code for hit in hits] == ["TT"]


BRANCHES = [
    ValueEntry("chi_nhanh", "TP. Hồ Chí Minh", "TP. Hồ Chí Minh"),
    ValueEntry("chi_nhanh", "Hà Nội", "Hà Nội"),
    ValueEntry("chi_nhanh", "Đà Nẵng", "Đà Nẵng"),
]


def test_a_value_said_in_part_is_found_by_its_trigrams() -> None:
    # R1-05: "Hồ Chí Minh" is "TP. Hồ Chí Minh" (similarity 0.80, the 01 §3.4 bar).
    [hit] = sem_value_retrieval("doanh thu Hồ Chí Minh tháng 9", BRANCHES)
    assert (hit.dimension_code, hit.value_code, hit.span) == (
        "chi_nhanh",
        "TP. Hồ Chí Minh",
        "ho chi minh",
    )
    assert FUZZY_AT <= hit.score < 1.0


def test_an_exact_value_and_a_near_one_are_both_found() -> None:
    hits = sem_value_retrieval("Hà Nội so với Hồ Chí Minh", BRANCHES)
    assert {(h.value_code, h.score == 1.0) for h in hits} == {
        ("Hà Nội", True),
        ("TP. Hồ Chí Minh", False),
    }


def test_a_typo_of_a_short_label_is_not_guessed() -> None:
    assert sem_value_retrieval("doanh thu Đà Nẵg", BRANCHES) == []
    assert sem_value_retrieval("doanh thu Hải Phòng", BRANCHES) == []


def test_a_near_match_never_picks_between_two_values() -> None:
    twins = [
        ValueEntry("kho", "K1", "Kho Hà Nội Một"),
        ValueEntry("kho", "K2", "Kho Hà Nội Hai"),
    ]
    assert sem_value_retrieval("tồn kho hà nội", twins) == []


def test_similarity_counts_trigrams_as_pg_trgm() -> None:
    assert similarity("ho chi minh", "tp ho chi minh") == pytest.approx(0.8)
    assert similarity("abc", "abc") == 1.0 and similarity("", "") == 0.0


def test_profile_values_become_index_entries() -> None:
    entries = index_entries(
        [
            {"value": "Hà Nội", "count": 40},
            {"value": "2026-09-30", "count": 10},
            {"value": "100", "count": 3},
            {"value": "***", "count": 1},
            "not-a-row",
        ]
    )
    assert [(entry.value_code, entry.label_norm, entry.row_count) for entry in entries] == [
        ("Hà Nội", "ha noi", 40)
    ]


def test_a_dimension_expression_names_its_column() -> None:
    assert dimension_column("branch") == "branch"
    assert dimension_column('"branch"') == "branch"
    assert dimension_column("SUM(qty_on_hand)") is None


def test_a_profile_fills_only_the_dimension_on_that_column() -> None:
    writes = index_writes(
        [
            (
                "sales",
                "branch",
                [{"value": "Hà Nội", "count": 40}, {"value": "2026-09-30", "count": 6}],
            )
        ],
        [
            IndexDimension("chi_nhanh", "space", "sales", "branch", True, False),
            IndexDimension("ngay_ban", "space", "sales", "sold_on", True, True),
            IndexDimension("kho", "space", "stock", "branch", True, False),
            IndexDimension("doanh_thu", "space", "sales", "SUM(net_revenue)", True, False),
        ],
    )
    assert [(write.dimension_id, write.entry.value_code) for write in writes] == [
        ("chi_nhanh", "Hà Nội")
    ]
