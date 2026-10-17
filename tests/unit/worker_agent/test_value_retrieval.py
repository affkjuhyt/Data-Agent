"""Semantic value retrieval: a keyword maps to the value the column stores (DeepEye-SQL §3.4)."""

from __future__ import annotations

import pytest

from services.worker_agent.understand.resolver import ValueEntry
from services.worker_agent.understand.value_retrieval import (
    IndexDimension,
    ValueHit,
    dimension_column,
    index_entries,
    index_writes,
    sem_value_retrieval,
)

pytestmark = [pytest.mark.unit]


def test_a_keyword_resolves_to_the_stored_code() -> None:
    hits = sem_value_retrieval(
        "doanh thu Hải Phòng tháng trước",
        [ValueEntry("tinh", "HP01", "Hải Phòng")],
    )
    assert hits == [ValueHit("tinh", "HP01", "Hải Phòng", 1.0)]


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
