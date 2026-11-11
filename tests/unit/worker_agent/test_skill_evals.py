"""Scoring one case of a skill version's eval (UC-03-04, BR-95): the run's figures against what
the item expects, within the item's tolerance or 0.01 % of the expected value."""

from __future__ import annotations

import decimal
import uuid
from typing import Any

import pytest

from libs.dataagent_db.models import eval as ev
from services.worker_agent.skill_evals import score

pytestmark = [pytest.mark.unit]

CASE = uuid.UUID(int=7)
RESULT = {
    "summary": "Dư nợ tăng từ 1.000 lên 3.500.",
    "dimension": "vung",
    "figures": {
        "base": "1000",
        "current": "3500",
        "change": "2500",
        "contributions": [
            {"member": "Miền Bắc", "value": "1500"},
            {"member": "Miền Nam", "value": "1000"},
        ],
    },
}


def _item(expected: dict[str, Any], tolerance: str | None = None) -> ev.EvalItem:
    return ev.EvalItem(
        input={"params": {}},
        expected=expected,
        tolerance=decimal.Decimal(tolerance) if tolerance else None,
    )


@pytest.mark.req("AC-03-04-04")
@pytest.mark.parametrize(
    ("expected", "passed"),
    [
        # 1 000 000 000 expected: 1 000 050 000 (0.005 % off) passes, 1 000 200 000 (0.02 %) not.
        ("1000050000", True),
        ("1000200000", False),
    ],
)
def test_an_amount_passes_within_a_hundredth_of_a_percent(expected: str, passed: bool) -> None:
    result = {**RESULT, "figures": {**RESULT["figures"], "current": "1000000000"}}
    scored = score(_item({"figures": {"current": expected}}), "succeeded", result, None, CASE)
    assert scored.passed is passed and scored.value_ok is passed


def test_a_count_off_by_one_fails() -> None:
    result = {**RESULT, "figures": {**RESULT["figures"], "current": "3501"}}
    assert not score(_item({"figures": {"current": 3500}}), "succeeded", result, None, CASE).passed


def test_the_items_tolerance_is_absolute() -> None:
    item = _item({"contributions": {"Miền Bắc": 1490}}, tolerance="10")
    assert score(item, "succeeded", RESULT, None, CASE).passed


def test_contributions_dimension_and_summary_are_each_checked() -> None:
    item = _item(
        {
            "contributions": {"Miền Bắc": 1500, "Miền Nam": 1000},
            "dimension": "san_pham",
            "text_contains": ["Dư nợ tăng"],
        }
    )
    scored = score(item, "succeeded", RESULT, None, CASE)
    assert (scored.understanding_ok, scored.value_ok, scored.behavior_ok) == (False, True, True)
    assert not scored.passed
    # The cause names the part, never a value.
    assert scored.cause == "the result's dimension is not what the item expects"
    assert scored.actual["skill_run_id"] == str(CASE)


def test_a_missing_member_fails() -> None:
    item = _item({"contributions": {"Miền Trung": 0}})
    assert not score(item, "succeeded", RESULT, None, CASE).passed


def test_a_failed_run_fails_its_case_and_keeps_why_in_actual() -> None:
    scored = score(_item({"figures": {"change": 1}}), "failed", None, "Không có dữ liệu", CASE)
    assert not scored.passed and scored.cause == "the skill run failed"
    assert scored.actual["error"] == "Không có dữ liệu"


def test_a_case_that_expects_a_refusal_passes_when_the_run_fails() -> None:
    item = _item({"behavior": "refuse"})
    assert score(item, "failed", None, "Tham số period …", CASE).passed
    assert not score(item, "succeeded", RESULT, None, CASE).passed


def test_a_case_that_expects_nothing_never_passes() -> None:
    scored = score(_item({}), "succeeded", RESULT, None, CASE)
    assert not scored.passed and scored.cause == "the item states no expected result"
