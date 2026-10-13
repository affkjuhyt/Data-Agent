"""Unit tests for backend/libs/indicator/verifier.py (NC-T5 in docs/v2/12-numeric-core.md).

The four checks are independent, and the tests are organised to show why that
matters: each one is made to fail while the other three pass, because a single
combined score would not tell a reader which of four different things went
wrong.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from libs.indicator.numeric import Tolerance, parse
from libs.indicator.verifier import (
    EvidenceItem,
    citations_resolved,
    numbers_grounded,
    precision_supported,
    verify,
    vintage_consistent,
)

pytestmark = pytest.mark.unit


def evidence(token: str, **kwargs) -> EvidenceItem:
    defaults = {"doc_id": "doc-17", "page": 12}
    return EvidenceItem(readings=parse(token), **{**defaults, **kwargs})


DEBT_RATIO = evidence("34,7", series="public_debt_to_gdp", period="2024", release_id="rel-1")
DEBT_LEVEL = evidence("3.109,2", series="debt", period="2024", release_id="rel-1")


# ── numbers_grounded ──────────────────────────────────────────────────────────


def test_a_figure_taken_from_the_evidence_is_grounded():
    check = numbers_grounded("Nợ công/GDP năm 2024 là 34,7%.", [DEBT_RATIO])

    assert check.passed


def test_a_fabricated_figure_is_named():
    check = numbers_grounded("Nợ công/GDP năm 2024 là 41,2%.", [DEBT_RATIO])

    assert not check.passed
    assert check.offending == ("41,2",)


def test_a_magnitude_error_is_not_grounded():
    """The defect the string-matching judge lets through: the evidence says
    128,4 and the answer says 1284."""
    check = numbers_grounded("Doanh thu 1284 tỷ.", [evidence("128,4")])

    assert not check.passed


def test_citation_markers_are_not_treated_as_figures():
    check = numbers_grounded("Tỷ lệ là 34,7% [1].", [DEBT_RATIO])

    assert check.passed


def test_list_markers_are_not_treated_as_figures():
    answer = (
        "Các cách:\n\n1. **Website SJC**: 34,7% [1].\n  2) Báo tài chính.\n3. Ứng dụng 41,2 lần."
    )

    check = numbers_grounded(answer, [DEBT_RATIO])

    # The markers go; a figure on a numbered line is still checked.
    assert check.offending == ("41,2",)


def test_the_wrong_year_is_an_ungrounded_figure():
    """A correct value stated about the wrong period. None of the other three
    checks would notice, because the value itself is right."""
    check = numbers_grounded("Nợ công/GDP năm 2023 là 34,7%.", [DEBT_RATIO])

    assert not check.passed
    assert check.offending == ("2023",)


def test_rounding_to_the_published_precision_is_grounded_not_overstated():
    """34,74 matches 34,7 at the coarser of the two precisions, which is how
    "2,5%" is allowed to stand for a computed 2,5157%. Whether stating that
    extra digit was legitimate is `precision_supported`'s question, not this
    one — the two checks divide the work deliberately."""
    assert numbers_grounded("Tỷ lệ khoảng 34,74%.", [DEBT_RATIO]).passed
    assert not precision_supported("Tỷ lệ khoảng 34,74%.", [DEBT_RATIO]).passed


def test_a_declared_tolerance_widens_the_match():
    answer = "Tỷ lệ khoảng 34,9%."

    assert not numbers_grounded(answer, [DEBT_RATIO]).passed
    assert numbers_grounded(answer, [DEBT_RATIO], Tolerance("absolute", Decimal("0.25"))).passed


def test_a_refusal_states_no_figures_and_passes():
    assert numbers_grounded("Chỉ tiêu này chưa được công bố cho kỳ đó.", []).passed


# ── precision_supported ───────────────────────────────────────────────────────


def test_quoting_at_the_published_precision_is_fine():
    assert precision_supported("Tỷ lệ là 34,7%.", [DEBT_RATIO]).passed


def test_rounding_coarser_than_the_source_is_fine():
    assert precision_supported("Tỷ lệ khoảng 35%.", [DEBT_RATIO]).passed


def test_inventing_digits_the_source_never_published_is_caught():
    """34,7123% matches 34,7 within published precision — so it IS grounded —
    while claiming three digits of certainty the bulletin never stated."""
    check = precision_supported("Tỷ lệ là 34,7123%.", [DEBT_RATIO])

    assert not check.passed
    assert check.offending == ("34,7123",)


def test_an_ungrounded_figure_is_not_reported_twice():
    """numbers_grounded owns that failure; reporting it here as well would make
    one mistake look like two."""
    assert precision_supported("Tỷ lệ là 99,9999%.", [DEBT_RATIO]).passed


# ── vintage_consistent ────────────────────────────────────────────────────────


def test_one_release_across_the_evidence_is_consistent():
    assert vintage_consistent([DEBT_RATIO, DEBT_LEVEL]).passed


def test_two_releases_of_the_same_series_and_period_conflict():
    revised = evidence("3.200,0", series="debt", period="2024", release_id="rel-2")

    check = vintage_consistent([DEBT_LEVEL, revised])

    assert not check.passed
    assert check.offending == ("debt@2024",)


def test_the_same_series_at_different_periods_is_not_a_conflict():
    last_year = evidence("3.000,0", series="debt", period="2023", release_id="rel-2")

    assert vintage_consistent([DEBT_LEVEL, last_year]).passed


def test_different_series_from_different_releases_is_not_a_conflict():
    """Two sources publish on their own schedules; that is normal."""
    gdp = evidence("8.960,2", series="gdp", period="2024", release_id="rel-9")

    assert vintage_consistent([DEBT_LEVEL, gdp]).passed


def test_evidence_without_provenance_is_skipped_not_failed():
    assert vintage_consistent([EvidenceItem(readings=parse("34,7"))]).passed


# ── citations_resolved ────────────────────────────────────────────────────────


def test_a_marker_pointing_at_a_document_page_resolves():
    assert citations_resolved("Tỷ lệ là 34,7% [1].", [DEBT_RATIO]).passed


def test_a_marker_past_the_end_of_the_evidence_does_not_resolve():
    check = citations_resolved("Tỷ lệ là 34,7% [3].", [DEBT_RATIO])

    assert not check.passed
    assert check.offending == ("[3]",)


def test_a_marker_pointing_at_a_nameless_item_does_not_resolve():
    nameless = EvidenceItem(readings=parse("34,7"), doc_id="", page=None)

    assert not citations_resolved("Tỷ lệ là 34,7% [1].", [nameless]).passed


def test_a_missing_page_is_reported_but_does_not_fail_the_answer():
    """Fault has to land on whoever can fix it. A document ingested before
    coordinates were recorded is the corpus's gap, not the answer's mistake —
    failing here would make the check something to switch off."""
    pageless = EvidenceItem(readings=parse("34,7"), doc_id="doc-17", page=None)

    check = citations_resolved("Tỷ lệ là 34,7% [1].", [pageless])

    assert check.passed
    assert "no page recorded" in check.detail


def test_a_resolved_citation_with_a_page_reports_nothing():
    assert citations_resolved("Tỷ lệ là 34,7% [1].", [DEBT_RATIO]).detail == ""


def test_an_answer_with_no_markers_has_nothing_to_resolve():
    assert citations_resolved("Tỷ lệ là 34,7%.", [DEBT_RATIO]).passed


# ── the verdict ───────────────────────────────────────────────────────────────


def test_a_good_answer_passes_all_four():
    verdict = verify(
        "Nợ công/GDP năm 2024 là 34,7%, dư nợ 3.109,2 nghìn tỷ đồng [1].", [DEBT_RATIO, DEBT_LEVEL]
    )

    assert verdict.passed
    assert [c.name for c in verdict.checks] == [
        "numbers_grounded",
        "precision_supported",
        "vintage_consistent",
        "citations_resolved",
    ]


def test_each_failure_is_reported_separately():
    """One combined score would hide which of four things went wrong."""
    revised = evidence("3.200,0", series="debt", period="2024", release_id="rel-2")

    verdict = verify("Tỷ lệ là 41,2% [9].", [DEBT_LEVEL, revised])

    assert not verdict.passed
    assert {c.name for c in verdict.failures} == {
        "numbers_grounded",
        "vintage_consistent",
        "citations_resolved",
    }
    assert verdict.check("precision_supported").passed


def test_the_verdict_exposes_each_check_by_name():
    verdict = verify("Tỷ lệ là 34,7% [1].", [DEBT_RATIO])

    assert verdict.check("numbers_grounded").passed
    with pytest.raises(KeyError):
        verdict.check("no_such_check")
