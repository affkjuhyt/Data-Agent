"""Analytical slots and the follow-up merge (libs/semantic/slots.py).

The case that drove this: "chỉ lấy miền Bắc" is not a new question, it is the
previous one plus a filter. With only a chat transcript to work from, answering
it meant re-inventing the whole query, and every re-invention was free to drop a
filter, switch the date column, or change the aggregation — the number moved and
nothing said why.

The merge rules are the correctness surface, so they get the most tests. Two in
particular would produce confidently wrong answers if they regressed:
filters replacing by dimension (rather than accumulating into an impossible
AND), and an absent field being inherited (rather than cleared).
"""
from __future__ import annotations

import pytest

from libs.semantic import AnalyticalSlots, SlotFilter, merge
from libs.semantic.models import FilterOp, MetricFilter

pytestmark = pytest.mark.unit


def _slots(**kw) -> AnalyticalSlots:
    return AnalyticalSlots(**kw)


def _f(dimension, *values, op="eq") -> SlotFilter:
    return SlotFilter(dimension=dimension, op=op, values=tuple(values))


BASE = _slots(
    metric="revenue_net",
    dimensions=("miền",),
    filters=(_f("kênh", "online"),),
    time_range=("2026-06-01", "2026-07-01"),
    grain="month",
)


class TestTheFollowUpCase:
    def test_adding_a_filter_keeps_everything_else(self):
        """"chỉ lấy miền Bắc" — the period, the metric, the grain and the
        existing channel filter must all survive."""
        result = merge(BASE, _slots(filters=(_f("miền", "Bắc"),)))

        assert result.metric == "revenue_net"
        assert result.time_range == ("2026-06-01", "2026-07-01")
        assert result.grain == "month"
        assert result.filter_for("kênh").values == ("online",)
        assert result.filter_for("miền").values == ("Bắc",)

    def test_a_second_region_replaces_the_first_it_does_not_and_them(self):
        """"chỉ lấy miền Bắc" then "chỉ lấy miền Nam" means South.
        Accumulating would emit `region = 'Bắc' AND region = 'Nam'` — always
        empty, and an empty result is the hardest kind of wrong answer to spot."""
        north = merge(BASE, _slots(filters=(_f("miền", "Bắc"),)))

        south = merge(north, _slots(filters=(_f("miền", "Nam"),)))

        assert south.filter_for("miền").values == ("Nam",)
        assert len([f for f in south.filters if f.dimension == "miền"]) == 1

    def test_changing_the_period_keeps_the_filters(self):
        """"còn tháng 7 thì sao?" — the region filter must not be silently
        dropped along with the old dates."""
        north = merge(BASE, _slots(filters=(_f("miền", "Bắc"),)))

        july = merge(north, _slots(time_range=("2026-07-01", "2026-08-01")))

        assert july.time_range == ("2026-07-01", "2026-08-01")
        assert july.filter_for("miền").values == ("Bắc",)
        assert july.filter_for("kênh").values == ("online",)

    def test_removing_a_filter_is_explicit_and_leaves_the_rest(self):
        """"bỏ lọc miền" — removal has to be requested, because "I said nothing
        about it" and "drop it" mean opposite things."""
        north = merge(BASE, _slots(filters=(_f("miền", "Bắc"),)))

        result = merge(north, _slots(remove_filters=("miền",)))

        assert result.filter_for("miền") is None
        assert result.filter_for("kênh").values == ("online",)
        assert result.time_range == ("2026-06-01", "2026-07-01")

    def test_a_new_metric_starts_fresh_rather_than_inheriting_filters(self):
        """"và số khách hàng thì sao?" is a new question. Inheriting revenue's
        filters would silently scope it to whatever the last question looked at."""
        north = merge(BASE, _slots(filters=(_f("miền", "Bắc"),)))

        result = merge(north, _slots(metric="customer_count"))

        assert result.metric == "customer_count"
        assert result.filters == ()
        assert result.time_range is None

    def test_the_same_metric_named_again_is_still_a_refinement(self):
        """Repeating the metric name is not a reset — "doanh thu miền Bắc" after
        "doanh thu tháng 6" should keep June."""
        result = merge(BASE, _slots(metric="revenue_net", filters=(_f("miền", "Bắc"),)))

        assert result.time_range == ("2026-06-01", "2026-07-01")
        assert result.filter_for("miền").values == ("Bắc",)


class TestInheritanceRules:
    def test_absent_fields_are_inherited_not_cleared(self):
        result = merge(BASE, _slots(filters=(_f("miền", "Bắc"),)))

        assert result.dimensions == ("miền",)
        assert result.grain == "month"

    def test_dimensions_replace_wholesale_when_given(self):
        """"theo tỉnh" replaces "theo miền" rather than grouping by both —
        which is what the words mean."""
        result = merge(BASE, _slots(dimensions=("tỉnh",)))

        assert result.dimensions == ("tỉnh",)

    def test_a_relative_period_supersedes_an_inherited_absolute_range(self):
        """Otherwise "tháng trước" would be ANDed with June and return nothing."""
        result = merge(BASE, _slots(relative_period="last_month"))

        assert result.relative_period == "last_month"
        assert result.time_range is None

    def test_an_absolute_range_supersedes_an_inherited_relative_period(self):
        previous = _slots(metric="revenue_net", relative_period="last_month")

        result = merge(previous, _slots(time_range=("2026-01-01", "2026-02-01")))

        assert result.time_range == ("2026-01-01", "2026-02-01")
        assert result.relative_period == ""

    def test_comparison_is_not_inherited(self):
        """"so với cùng kỳ" applies to the turn that asked. Carrying it forward
        would turn every later follow-up into a comparison nobody requested."""
        compared = merge(BASE, _slots(compare_to="same_period_last_year"))
        assert compared.compare_to == "same_period_last_year"

        after = merge(compared, _slots(filters=(_f("miền", "Bắc"),)))

        assert after.compare_to == ""

    def test_no_previous_state_returns_the_incoming_slots(self):
        incoming = _slots(metric="revenue_net", filters=(_f("miền", "Bắc"),))

        assert merge(None, incoming).metric == "revenue_net"
        assert merge(AnalyticalSlots(), incoming).metric == "revenue_net"

    def test_remove_filters_never_survives_a_merge(self):
        """It is an instruction for one turn, not state. Persisting it would
        re-delete the filter on every later turn."""
        result = merge(BASE, _slots(remove_filters=("kênh",)))

        assert result.remove_filters == ()

    def test_merge_is_deterministic(self):
        incoming = _slots(filters=(_f("miền", "Bắc"),))

        assert merge(BASE, incoming) == merge(BASE, incoming)


class TestParsing:
    def test_unknown_grain_and_period_are_dropped_not_passed_through(self):
        """Model-supplied values must not reach the compiler unvalidated."""
        parsed = AnalyticalSlots.from_dict({
            "metric": "revenue_net", "grain": "fortnight", "relative_period": "someday",
            "compare_to": "vibes",
        })

        assert parsed.grain == ""
        assert parsed.relative_period == ""
        assert parsed.compare_to == ""

    def test_valid_values_survive(self):
        parsed = AnalyticalSlots.from_dict({
            "grain": "quarter", "relative_period": "last_month",
            "compare_to": "same_period_last_year",
        })

        assert parsed.grain == "quarter"
        assert parsed.relative_period == "last_month"
        assert parsed.compare_to == "same_period_last_year"

    def test_a_scalar_filter_value_is_accepted_as_a_single_value(self):
        parsed = AnalyticalSlots.from_dict({"filters": [{"dimension": "miền", "value": "Bắc"}]})

        assert parsed.filters[0].values == ("Bắc",)

    def test_nameless_filters_are_discarded_not_fatal(self):
        parsed = AnalyticalSlots.from_dict({"filters": [{"value": "Bắc"}, {"dimension": "miền", "value": "Nam"}]})

        assert len(parsed.filters) == 1
        assert parsed.filters[0].dimension == "miền"

    def test_malformed_time_range_is_dropped(self):
        assert AnalyticalSlots.from_dict({"time_range": ["2026-06-01"]}).time_range is None
        assert AnalyticalSlots.from_dict({"time_range": "june"}).time_range is None

    def test_garbage_input_yields_empty_slots(self):
        for garbage in (None, [], "text", 42):
            assert AnalyticalSlots.from_dict(garbage).is_empty()

    def test_round_trips_through_dict(self):
        original = merge(BASE, _slots(filters=(_f("miền", "Bắc"),)))

        assert AnalyticalSlots.from_dict(original.to_dict()) == original


class TestBindingToTheCompiler:
    def test_single_value_becomes_an_equality(self):
        bound = _f("miền", "Bắc").to_metric_filter("region_name")

        assert bound == MetricFilter("region_name", FilterOp.EQ, "Bắc")

    def test_multiple_values_under_eq_become_an_in_not_a_dropped_value(self):
        """"miền Bắc và miền Nam" is an IN. Emitting `= 'Bắc'` and silently
        discarding the rest answers a different question."""
        bound = _f("miền", "Bắc", "Nam").to_metric_filter("region_name")

        assert bound.op is FilterOp.IN
        assert bound.value == ["Bắc", "Nam"]

    def test_explicit_in_is_preserved(self):
        bound = _f("miền", "Bắc", "Nam", op="in").to_metric_filter("region_name")

        assert bound.op is FilterOp.IN

    def test_null_ops_need_no_value(self):
        bound = _f("miền", op="is_null").to_metric_filter("region_name")

        assert bound.op is FilterOp.IS_NULL

    def test_the_bound_column_is_validated(self):
        """Dimension names come from a model; the physical column they bind to
        must still be a real identifier."""
        from libs.semantic.models import InvalidMetricDefinition

        with pytest.raises(InvalidMetricDefinition):
            _f("miền", "Bắc").to_metric_filter("region; DROP TABLE x")
