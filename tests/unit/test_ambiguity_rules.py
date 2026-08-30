"""Deterministic underspecification rules (backend/libs/semantic/ambiguity.py).

Clarification was entirely LLM-decided, which catches vague phrasing and misses
the two cases that read as perfectly clear while producing the wrong number:
no time range on a time-bound metric, and a metric name covering two different
formulas.

Detection is the easy half. The hard half — and the one with more tests here —
is **not asking when the answer would not differ**. A system that interrupts
needlessly trains people to dismiss it, and then the one question that mattered
gets dismissed too.
"""
from __future__ import annotations

import pytest

from libs.semantic import (
    Aggregation,
    AnalyticalSlots,
    FilterOp,
    MetricDefinition,
    MetricFilter,
    MetricResolver,
    SlotFilter,
    TimeGrain,
    ambiguity,
    build_catalog,
)
from libs.semantic.ambiguity import Reason

pytestmark = pytest.mark.unit


def _metric(key, name, *, expression="amount", aggregation=Aggregation.SUM,
            filters=(), time_column="transaction_date") -> MetricDefinition:
    return MetricDefinition(
        key=key, version=1, name=name, aggregation=aggregation,
        source_table="orders", expression=expression, time_column=time_column,
        default_grain=TimeGrain.NONE, unit="VND", default_filters=filters,
        owner="finance@example.com",
    )


GROSS = _metric("revenue_gross", "Doanh thu gộp")
NET = _metric(
    "revenue_net", "Doanh thu thuần",
    filters=(MetricFilter("status", FilterOp.NOT_IN, ["refunded"]),),
)
# Same arithmetic as GROSS under a different name — a naming choice, not an
# ambiguity worth interrupting anyone over.
TURNOVER = _metric("turnover", "Doanh số")
STOCK = _metric("stock_level", "Tồn kho", time_column="")


def _check(analysis_type="analytical", slots=None, definition=GROSS, alternatives=()):
    return ambiguity.check(
        analysis_type=analysis_type,
        slots=slots if slots is not None else AnalyticalSlots(),
        definition=definition,
        metric_alternatives=alternatives,
    )


def _reasons(questions):
    return {q.reason for q in questions}


class TestMissingTimeRange:
    def test_analytical_question_with_no_period_is_flagged(self):
        """"doanh thu bao nhiêu?" over all history returns a plausible number
        for a question nobody asked."""
        assert Reason.MISSING_TIME_RANGE in _reasons(_check())

    def test_an_absolute_range_satisfies_it(self):
        slots = AnalyticalSlots(time_range=("2026-06-01", "2026-07-01"))

        assert Reason.MISSING_TIME_RANGE not in _reasons(_check(slots=slots))

    def test_a_relative_period_satisfies_it(self):
        """A follow-up saying "tháng trước" has scoped the question even though
        no dates exist yet."""
        slots = AnalyticalSlots(relative_period="last_month")

        assert Reason.MISSING_TIME_RANGE not in _reasons(_check(slots=slots))

    def test_a_metric_with_no_business_date_is_never_asked_about(self):
        """A stock level has no period to miss."""
        assert Reason.MISSING_TIME_RANGE not in _reasons(_check(definition=STOCK))

    def test_a_lookup_is_never_asked_about(self):
        """"quy định nghỉ phép là gì?" has no time dimension."""
        assert _check(analysis_type="lookup") == []

    def test_the_options_map_onto_slot_values(self):
        """An answer has to become a slot without free-text parsing, or the
        clarification cannot actually resolve anything."""
        from libs.semantic.slots import RELATIVE_PERIODS

        question = next(q for q in _check() if q.reason is Reason.MISSING_TIME_RANGE)

        assert question.options
        for option in question.options:
            assert option in RELATIVE_PERIODS


class TestAmbiguousMetric:
    def test_two_metrics_that_compute_differently_are_flagged(self):
        """Gross and net differ by exactly the refunds — the confusion the
        Semantic Layer exists to end, reappearing at the door."""
        questions = _check(alternatives=(NET,))

        assert Reason.AMBIGUOUS_METRIC in _reasons(questions)

    def test_metrics_that_compute_identically_are_not_flagged(self):
        """Two registered names for the same arithmetic are a naming choice.
        Asking here is pure friction."""
        questions = _check(alternatives=(TURNOVER,))

        assert Reason.AMBIGUOUS_METRIC not in _reasons(questions)

    def test_no_alternatives_means_no_question(self):
        assert Reason.AMBIGUOUS_METRIC not in _reasons(_check(alternatives=()))

    def test_the_options_name_both_readings(self):
        question = next(q for q in _check(alternatives=(NET,)) if q.reason is Reason.AMBIGUOUS_METRIC)

        assert "Doanh thu gộp" in question.options
        assert "Doanh thu thuần" in question.options

    @pytest.mark.parametrize("differing", [
        _metric("x", "X", aggregation=Aggregation.COUNT_DISTINCT, expression="customer_id"),
        _metric("y", "Y", expression="net_amount"),
    ])
    def test_a_different_aggregation_or_expression_counts_as_differing(self, differing):
        assert Reason.AMBIGUOUS_METRIC in _reasons(_check(alternatives=(differing,)))

    def test_metric_ambiguity_is_reported_before_the_missing_period(self):
        """Picking the wrong metric makes every other slot moot, so it is the
        first thing worth resolving."""
        questions = _check(alternatives=(NET,))

        assert questions[0].reason is Reason.AMBIGUOUS_METRIC


class TestComparisonBaseline:
    def test_a_comparison_with_nothing_to_compare_against_is_flagged(self):
        slots = AnalyticalSlots(time_range=("2026-06-01", "2026-07-01"))

        questions = _check(analysis_type="comparison", slots=slots)

        assert Reason.COMPARISON_WITHOUT_BASELINE in _reasons(questions)

    def test_an_explicit_compare_to_satisfies_it(self):
        slots = AnalyticalSlots(
            time_range=("2026-06-01", "2026-07-01"), compare_to="same_period_last_year",
        )

        questions = _check(analysis_type="comparison", slots=slots)

        assert Reason.COMPARISON_WITHOUT_BASELINE not in _reasons(questions)

    def test_two_values_on_one_dimension_are_their_own_baseline(self):
        """"miền Bắc và miền Nam" IS the comparison. Asking would be obtuse."""
        slots = AnalyticalSlots(
            time_range=("2026-06-01", "2026-07-01"),
            filters=(SlotFilter("miền", "in", ("Bắc", "Nam")),),
        )

        questions = _check(analysis_type="comparison", slots=slots)

        assert Reason.COMPARISON_WITHOUT_BASELINE not in _reasons(questions)

    def test_a_single_value_filter_is_not_a_baseline(self):
        slots = AnalyticalSlots(
            time_range=("2026-06-01", "2026-07-01"),
            filters=(SlotFilter("miền", "eq", ("Bắc",)),),
        )

        questions = _check(analysis_type="comparison", slots=slots)

        assert Reason.COMPARISON_WITHOUT_BASELINE in _reasons(questions)


class TestNoiseControl:
    def test_a_fully_specified_question_asks_nothing(self):
        slots = AnalyticalSlots(
            metric="revenue_net", time_range=("2026-06-01", "2026-07-01"),
        )

        assert _check(slots=slots, definition=NET) == []

    def test_an_unresolved_metric_asks_nothing(self):
        """An unrecognised question is the general path's business, not
        something to interrupt over."""
        assert _check(definition=None) == []

    def test_forecast_and_anomaly_are_not_time_range_prompted(self):
        """Those already carry a capability limitation; adding a period prompt
        on top would be two interruptions for one unanswerable question."""
        for kind in ("forecast", "anomaly", "other"):
            assert Reason.MISSING_TIME_RANGE not in _reasons(_check(analysis_type=kind))

    def test_check_is_deterministic(self):
        assert _check(alternatives=(NET,)) == _check(alternatives=(NET,))


class TestResolverSpecificityLookup:
    """`resolve` returns one winner; detecting underspecification needs the
    alternatives the winner hid."""

    def _resolver(self):
        return MetricResolver(build_catalog(
            [GROSS, NET],
            {"revenue_gross": ["doanh thu"], "revenue_net": ["doanh thu thuần"]},
        ))

    def test_a_vague_term_surfaces_the_more_specific_metric(self):
        found = self._resolver().more_specific_than("doanh thu")

        assert [d.key for d in found] == ["revenue_net"]

    def test_the_specific_term_surfaces_nothing(self):
        """Someone who said "doanh thu thuần" has already chosen."""
        assert self._resolver().more_specific_than("doanh thu thuần") == []

    def test_the_matched_metric_is_never_its_own_alternative(self):
        found = self._resolver().more_specific_than("doanh thu")

        assert all(d.key != "revenue_gross" for d in found)

    def test_a_substring_that_is_not_a_word_boundary_does_not_match(self):
        """"thu" must not pull in "doanh thu" as an extension of an unrelated
        word."""
        resolver = MetricResolver(build_catalog([GROSS], {"revenue_gross": ["doanh thu"]}))

        assert resolver.more_specific_than("oanh") == []


class TestPayloadShape:
    def test_payload_mirrors_the_orchestrator_clarification_frame(self):
        """A client already handling `__clarification` needs no change."""
        payload = ambiguity.to_payload(_check(alternatives=(NET,)))

        assert isinstance(payload["clarification_questions"], list)
        assert all(isinstance(t, str) for t in payload["clarification_questions"])

    def test_payload_also_carries_structured_details(self):
        """So a UI can render choices, and so "how often do we ask about a
        missing period?" is answerable without parsing sentences."""
        payload = ambiguity.to_payload(_check())

        detail = payload["clarification_details"][0]
        assert detail["reason"] == Reason.MISSING_TIME_RANGE.value
        assert detail["options"]
