"""Phân rã biến động (UC-03-01): contributions that add up exactly, and the honest wording."""

from __future__ import annotations

import decimal

import pytest

from services.worker_agent.skill_runs import RunFailed, period_of
from services.worker_agent.skills.decomposition import (
    NotDecomposable,
    Shape,
    decompose,
    result_of,
    shape_of,
)

pytestmark = [pytest.mark.unit]

D = decimal.Decimal
MEASURES = {"no_xau": "SUM(CASE WHEN g >= 3 THEN b ELSE 0 END)", "du_no": "SUM(b)", "lai": "AVG(r)"}


@pytest.mark.parametrize(
    ("expression", "shape"),
    [
        ("du_no", Shape("additive", "du_no")),
        ("no_xau / du_no", Shape("ratio", "no_xau", "du_no", D(1))),
        ("no_xau / du_no * 100", Shape("ratio", "no_xau", "du_no", D(100))),
        ("100 * no_xau / du_no", Shape("ratio", "no_xau", "du_no", D(100))),
        ("no_xau * 100 / du_no", Shape("ratio", "no_xau", "du_no", D(100))),
    ],
)
def test_the_shapes_that_decompose_exactly(expression: str, shape: Shape) -> None:
    assert shape_of(expression, MEASURES) == shape


@pytest.mark.parametrize("expression", ["lai", "du_no - no_xau", "no_xau / lai", "du_no / 2 + 1"])
def test_anything_else_is_refused_not_approximated(expression: str) -> None:
    with pytest.raises(NotDecomposable):
        shape_of(expression, MEASURES)


def test_a_ratio_splits_into_rate_and_mix_and_adds_up() -> None:
    # August: only the north, no bad debt. September: north 200/2000, south (new) 100/1000.
    shape = Shape("ratio", "no_xau", "du_no", D(100))
    d = decompose(
        shape,
        {"Miền Bắc": (D(0), D(500))},
        {"Miền Bắc": (D(200), D(2000)), "Miền Nam": (D(100), D(1000))},
    )
    assert (d.base, d.current) == (D(0), D(10))
    by = {c.member: c for c in d.contributions}
    assert round(by["Miền Bắc"].value, 4) == D("6.6667")
    assert round(by["Miền Bắc"].mix or 0, 4) == 0
    # a member present in one period only moved the mix
    assert (by["Miền Nam"].rate, round(by["Miền Nam"].mix or 0, 4)) == (D(0), D("3.3333"))
    assert abs(d.residual) < D("1e-20")


def test_the_result_says_what_moved_and_that_it_is_not_a_cause() -> None:
    shape = Shape("ratio", "no_xau", "du_no", D(100))
    d = decompose(
        shape,
        {"Miền Bắc": (D(0), D(500))},
        {"Miền Bắc": (D(200), D(2000)), "Miền Nam": (D(100), D(1000))},
    )
    result = result_of(
        d,
        shape,
        metric_name="Tỷ lệ nợ xấu",
        dimension_name="vùng",
        unit="%",
        decimals=2,
        base_label="08/2026",
        current_label="09/2026",
        skipped=[],
    )
    assert result["summary"] == (
        "Tỷ lệ nợ xấu tăng từ 0,00% (08/2026) lên 10,00% (09/2026), +10,00 điểm %. "
        "Đóng góp lớn nhất theo vùng: Miền Bắc +6,67 điểm % (67% mức thay đổi). "
        "Trong đó +6,67 điểm % do tỷ lệ trong từng vùng thay đổi, +3,33 điểm % do cơ cấu giữa "
        "các vùng thay đổi."
    )
    assert "không phải nguyên nhân" in result["method"] and result["reconciled"] is True
    w = result["decomposition"]
    assert (w["start"]["value_text"], w["end"]["value_text"]) == ("0,00%", "10,00%")
    assert [s["label"] for s in w["steps"]] == ["Miền Bắc", "Miền Nam"]
    assert w["contributions"][1] == {
        "label": "Miền Nam",
        "points_text": "+3,33 điểm %",
        "share_text": "33%",
    }


def test_an_additive_metric_contributes_its_own_change_and_long_tails_fold() -> None:
    members = {f"CN{i:02d}": (D(i), None) for i in range(12)}
    later = {m: (v + 1, None) for m, (v, _) in members.items()}
    shape = Shape("additive", "du_no")
    d = decompose(shape, members, later)
    assert d.change == 12 and all(c.value == 1 for c in d.contributions)
    result = result_of(
        d,
        shape,
        metric_name="Dư nợ",
        dimension_name="Chi nhánh",
        unit="tỷ đồng",
        decimals=0,
        base_label="08/2026",
        current_label="09/2026",
        skipped=[],
    )
    steps = result["decomposition"]["steps"]
    assert len(steps) == 9 and steps[-1]["label"] == "Khác (4)" and steps[-1]["value"] == 4
    assert "Trong đó" not in result["summary"]


def test_periods_are_months_or_quarters_read_strictly() -> None:
    m = period_of("2026-01", "period")
    assert (m.label, m.end.isoformat(), m.before().label) == ("01/2026", "2026-02-01", "12/2025")
    assert period_of("2026-12", "period").end.isoformat() == "2027-01-01"
    q = period_of("2026-Q1", "period")
    assert (q.label, q.start.isoformat(), q.end.isoformat()) == (
        "Q1/2026",
        "2026-01-01",
        "2026-04-01",
    )
    assert q.before().label == "Q4/2025"
    for bad in ("2026-13", "2026-Q5", "tháng 9", None):
        with pytest.raises(RunFailed):
            period_of(bad, "period")
