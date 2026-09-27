"""Unit tests for libs/indicator/numeric.py and the grounding scorer's use of it
(E2 in docs/archive/v1-ankt/TECH-DESIGN.md).

The defect being pinned: the scorer compared figures as digit strings with
separators deleted, so "128,4" and "1284" were one token. These tests assert
the two halves of the fix — that unambiguous Vietnamese decimals no longer
collapse into their magnitude, and that a figure may be quoted at a coarser
precision than it was computed at without being called fabricated.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from libs.indicator.numeric import Reading, Tolerance, close, parse, readings
from model.eval.scorers.grounding import case_tolerance, unsupported_numbers

pytestmark = pytest.mark.unit


# ── parsing ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("token", "expected"), [
    ("128,4", [Decimal("128.4")]),          # one digit after the separator:
    ("34,7", [Decimal("34.7")]),            # cannot be a thousands group, so
    ("7,09", [Decimal("7.09")]),            # there is only one reading
    ("0,05", [Decimal("0.05")]),
    ("2024", [Decimal("2024")]),
    ("1.234.567", [Decimal("1234567")]),    # repeated separator only groups
    ("3.109,2", [Decimal("3109.2")]),       # both present: last one decides
    ("3,109.2", [Decimal("3109.2")]),       # ... in either convention
])
def test_parse_unambiguous(token, expected):
    assert [r.value for r in parse(token)] == expected


def test_parse_keeps_both_readings_when_genuinely_ambiguous():
    # "12.720" is twelve thousand grouped, or twelve-point-seven-two-zero.
    # Nothing in the token settles it, so both survive rather than one being
    # guessed; the integer reading leads because the corpus is Vietnamese.
    assert [r.value for r in parse("12.720")] == [Decimal("12720"), Decimal("12.720")]


def test_parse_never_reads_a_one_digit_decimal_as_a_magnitude():
    """The E2 defect itself: 128,4 must not be able to mean 1284."""
    assert Decimal("1284") not in [r.value for r in parse("128,4")]


@pytest.mark.parametrize("token", ["", "abc", ".", ","])
def test_parse_rejects_non_numbers(token):
    assert parse(token) == ()


def test_parse_trims_trailing_punctuation():
    assert [r.value for r in parse("128,4.")] == [Decimal("128.4")]


def test_readings_keeps_the_original_token_for_reporting():
    assert [t for t, _ in readings("doanh thu 128,4 tỷ, tăng 12%")] == ["128,4", "12"]


# ── tolerance ─────────────────────────────────────────────────────────────────

def test_published_precision_compares_at_the_coarser_of_the_two():
    # 2,5% quoted against a computed 2,5157% is correct as stated.
    assert close(Reading(Decimal("2.5"), 1), Reading(Decimal("2.516"), 3))


def test_published_precision_still_separates_different_figures():
    assert not close(Reading(Decimal("2.5"), 1), Reading(Decimal("2.6"), 1))
    assert not close(Reading(Decimal("128.4"), 1), Reading(Decimal("1284"), 0))


def test_published_precision_rounds_half_up_not_to_even():
    # Decimal's default would round 2.5 to 2 and disagree with the source at
    # exactly the boundary the comparison exists to cover.
    assert close(Reading(Decimal("2.5"), 1), Reading(Decimal("3"), 0))


def test_absolute_tolerance():
    tol = Tolerance("absolute", Decimal("0.05"))
    assert close(Reading(Decimal("34.70"), 2), Reading(Decimal("34.74"), 2), tol)
    assert not close(Reading(Decimal("34.70"), 2), Reading(Decimal("34.80"), 2), tol)


def test_relative_tolerance():
    tol = Tolerance("relative", Decimal("0.01"))
    assert close(Reading(Decimal("1000"), 0), Reading(Decimal("1005"), 0), tol)
    assert not close(Reading(Decimal("1000"), 0), Reading(Decimal("1100"), 0), tol)


def test_case_tolerance_defaults_to_published_precision():
    assert case_tolerance({}).kind == "published_precision"


def test_case_tolerance_reads_the_declared_one():
    tol = case_tolerance({"tolerance": {"type": "absolute", "value": 0.05}})
    assert (tol.kind, tol.value) == ("absolute", Decimal("0.05"))


# ── the scorer ────────────────────────────────────────────────────────────────

def test_figure_quoted_from_the_context_is_grounded():
    ctx = ["Báo cáo Q1/2024: tổng doanh thu đạt 128,4 tỷ đồng."]
    assert unsupported_numbers("Doanh thu là 128,4 tỷ đồng.", ctx) == []


def test_magnitude_error_is_caught():
    """What the old scorer let through: the source says 128,4, the answer 1284."""
    ctx = ["Báo cáo Q1/2024: tổng doanh thu đạt 128,4 tỷ đồng."]
    assert unsupported_numbers("Doanh thu là 1284 tỷ đồng.", ctx) == ["1284"]


def test_citation_markers_are_not_figures():
    ctx = ["Tổng doanh thu đạt 128,4 tỷ đồng."]
    assert unsupported_numbers("Doanh thu 128,4 tỷ. Trích dẫn: [1]", ctx) == []


def test_gnd_006_rounded_derivation_is_grounded():
    """Corpus case gnd-006, which the string scorer marked unsupported.

    320 hoàn / (12.400 + 320) = 2,5157%; the answer states 2,5%. The case
    declares 2.516 as a permitted derivation, and 2,5 is that figure at the
    precision it was stated.
    """
    ctx = ["Tháng 3/2024: 12.400 đơn giao thành công, 320 đơn bị hoàn."]
    allowed = ["100", "12.720", "2.516"]
    assert unsupported_numbers("Tỷ lệ hoàn đơn khoảng 2,5%.", ctx, allowed) == []


def test_gnd_014_integer_against_declared_decimal_is_grounded():
    """Corpus case gnd-014: allowed declares "12.0", the answer says "12"."""
    ctx = ["Q1/2024: doanh thu 128,4 tỷ; chi phí marketing 15,4 tỷ."]
    assert unsupported_numbers("Chi phí marketing chiếm khoảng 12% doanh thu.", ctx, ["100", "12.0"]) == []


def test_invented_figure_is_still_unsupported():
    ctx = ["Q1/2024: doanh thu 128,4 tỷ; chi phí marketing 15,4 tỷ."]
    assert unsupported_numbers("Lợi nhuận đạt 47,2 tỷ.", ctx, ["100", "12.0"]) == ["47,2"]


def test_declared_tolerance_widens_the_match():
    ctx = ["Nợ công so với GDP năm 2024: 34,7%."]
    tol = Tolerance("absolute", Decimal("0.05"))
    assert unsupported_numbers("Tỷ lệ là 34,74%.", ctx, None, tol) == []
    assert unsupported_numbers("Tỷ lệ là 34,9%.", ctx, None, tol) == ["34,9"]
