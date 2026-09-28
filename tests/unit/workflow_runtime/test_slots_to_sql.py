"""Slots -> SQL: that a follow-up compiles deterministically.

test_analytical_slots.py covers the merge rules in isolation. This covers the
thing that actually matters to a user: after "doanh thu thuần tháng 6 theo miền"
then "chỉ lấy miền Bắc", does the SECOND query keep June, keep the metric's own
net-revenue filter, and add exactly one region predicate?

That question had no deterministic answer before. The follow-up was handled by
re-reading the transcript and re-writing SQL, so the June bound, the refund
exclusion, or the aggregation could each quietly change between turns.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

from libs.semantic import (
    Aggregation,
    AnalyticalSlots,
    FilterOp,
    MetricDefinition,
    MetricDimension,
    MetricFilter,
    MetricResolver,
    SlotFilter,
    TimeGrain,
    build_catalog,
    compile_metric,
    merge,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "workflow_runtime")

pytestmark = pytest.mark.unit

REVENUE_NET = MetricDefinition(
    key="revenue_net",
    version=1,
    name="Doanh thu thuần",
    aggregation=Aggregation.SUM,
    source_table="orders",
    expression="amount",
    time_column="transaction_date",
    default_grain=TimeGrain.NONE,
    unit="VND",
    dimensions=(
        MetricDimension(key="miền", column="region_name"),
        MetricDimension(key="kênh", column="channel"),
    ),
    default_filters=(MetricFilter("status", FilterOp.NOT_IN, ["refunded"]),),
    owner="finance@example.com",
)


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


def _semantic_layer():
    import semantic_layer
    return semantic_layer


def _compile(slots: AnalyticalSlots):
    """Run slots through the same binding the service uses."""
    layer = _semantic_layer()
    dimensions, grain, time_range, extra = layer.slots_to_compiler_args(REVENUE_NET, slots)
    return compile_metric(
        REVENUE_NET, dimensions=dimensions, grain=grain,
        time_range=time_range, extra_filters=extra,
    )


TURN_1 = AnalyticalSlots(
    metric="doanh thu thuần",
    dimensions=("miền",),
    time_range=("2026-06-01", "2026-07-01"),
)


class TestTheFollowUpProducesCorrectSql:
    def test_turn_one_compiles_the_base_question(self):
        sql = _compile(TURN_1).sql

        assert "SUM(amount)" in sql
        assert "region_name AS miền" in sql
        assert "transaction_date >= '2026-06-01'" in sql
        assert "status NOT IN ('refunded')" in sql

    def test_chi_lay_mien_bac_keeps_the_period_and_adds_one_filter(self):
        """The case that named this work."""
        turn_2 = merge(TURN_1, AnalyticalSlots(filters=(SlotFilter("miền", "eq", ("Bắc",)),)))

        sql = _compile(turn_2).sql

        assert "region_name = 'Bắc'" in sql
        assert "transaction_date >= '2026-06-01'" in sql   # period survived
        assert "transaction_date < '2026-07-01'" in sql
        assert "status NOT IN ('refunded')" in sql          # contract filter survived
        assert "SUM(amount)" in sql                          # aggregation unchanged

    def test_switching_region_replaces_rather_than_contradicts(self):
        """`region = 'Bắc' AND region = 'Nam'` returns nothing — an empty result
        is the hardest wrong answer to notice."""
        north = merge(TURN_1, AnalyticalSlots(filters=(SlotFilter("miền", "eq", ("Bắc",)),)))
        south = merge(north, AnalyticalSlots(filters=(SlotFilter("miền", "eq", ("Nam",)),)))

        sql = _compile(south).sql

        assert "region_name = 'Nam'" in sql
        assert "'Bắc'" not in sql

    def test_changing_the_month_keeps_the_region_filter(self):
        north = merge(TURN_1, AnalyticalSlots(filters=(SlotFilter("miền", "eq", ("Bắc",)),)))
        july = merge(north, AnalyticalSlots(time_range=("2026-07-01", "2026-08-01")))

        sql = _compile(july).sql

        assert "region_name = 'Bắc'" in sql
        assert "transaction_date >= '2026-07-01'" in sql
        assert "'2026-06-01'" not in sql

    def test_removing_the_filter_widens_back_but_keeps_the_period(self):
        north = merge(TURN_1, AnalyticalSlots(filters=(SlotFilter("miền", "eq", ("Bắc",)),)))
        widened = merge(north, AnalyticalSlots(remove_filters=("miền",)))

        sql = _compile(widened).sql

        assert "region_name = " not in sql
        assert "transaction_date >= '2026-06-01'" in sql

    def test_two_regions_become_an_in_clause(self):
        both = merge(TURN_1, AnalyticalSlots(filters=(SlotFilter("miền", "eq", ("Bắc", "Nam")),)))

        sql = _compile(both).sql

        assert "region_name IN ('Bắc', 'Nam')" in sql

    def test_adding_a_second_dimension_filter_ands_them(self):
        """Different dimensions accumulate; only the SAME dimension replaces."""
        north = merge(TURN_1, AnalyticalSlots(filters=(SlotFilter("miền", "eq", ("Bắc",)),)))
        online = merge(north, AnalyticalSlots(filters=(SlotFilter("kênh", "eq", ("online",)),)))

        sql = _compile(online).sql

        assert "region_name = 'Bắc'" in sql
        assert "channel = 'online'" in sql

    def test_identical_slots_compile_to_identical_sql(self):
        """Determinism is the deliverable: the same request must not drift
        between turns or between replicas."""
        turn_2 = merge(TURN_1, AnalyticalSlots(filters=(SlotFilter("miền", "eq", ("Bắc",)),)))

        assert _compile(turn_2).sql == _compile(turn_2).sql


class TestUnknownDimensionsFallBack:
    def test_a_dimension_the_contract_does_not_define_raises(self):
        """Silently dropping the filter would return the whole country under a
        question that asked for one region."""
        from libs.semantic.models import UnsupportedRequest

        layer = _semantic_layer()
        slots = AnalyticalSlots(filters=(SlotFilter("màu sắc", "eq", ("đỏ",)),))

        with pytest.raises(UnsupportedRequest, match="no dimension"):
            layer.slots_to_compiler_args(REVENUE_NET, slots)

    async def test_match_question_falls_back_rather_than_dropping_a_filter(self, monkeypatch):
        layer = _semantic_layer()
        layer.invalidate()

        async def fake_resolver(tenant_id):
            return MetricResolver(build_catalog([REVENUE_NET], {"revenue_net": ["doanh thu thuần"]}))

        monkeypatch.setattr(layer, "get_resolver", fake_resolver)

        result = await layer.match_question(
            "t-1", "doanh thu thuần",
            slots=AnalyticalSlots(metric="doanh thu thuần", filters=(SlotFilter("màu sắc", "eq", ("đỏ",)),)),
        )

        assert result is None


class TestMetricCarriesAcrossTurns:
    async def test_a_followup_that_never_names_the_metric_still_reaches_it(self, monkeypatch):
        """"chỉ lấy miền Bắc" contains no metric word at all. Resolution has to
        come from the merged slots, not from re-reading the question."""
        layer = _semantic_layer()
        layer.invalidate()

        async def fake_resolver(tenant_id):
            return MetricResolver(build_catalog([REVENUE_NET], {"revenue_net": ["doanh thu thuần"]}))

        monkeypatch.setattr(layer, "get_resolver", fake_resolver)

        carried = merge(TURN_1, AnalyticalSlots(filters=(SlotFilter("miền", "eq", ("Bắc",)),)))
        match = await layer.match_question("t-1", "chỉ lấy miền Bắc", slots=carried)

        assert match is not None
        assert match.compiled.metric_ref == "revenue_net@1"
        assert "region_name = 'Bắc'" in match.compiled.sql
        assert "transaction_date >= '2026-06-01'" in match.compiled.sql


class TestPlannerContract:
    def test_planner_output_accepts_slots_and_defaults_empty(self):
        import nodes.model_harness.planner as planner

        assert planner._PlannerOutput().slots == {}
        assert planner._PlannerOutput(slots={"metric": "x"}).slots == {"metric": "x"}

    def test_planner_prompt_teaches_the_followup_pattern(self):
        """The model cannot emit a delta it was never shown. A prompt that
        stopped documenting this would make every follow-up re-state everything
        — and re-stating it wrongly is exactly the failure mode."""
        prompt = (REPO_ROOT / "backend" / "services" / "workflow_runtime" / "prompts" / "planner_system.txt").read_text(
            encoding="utf-8"
        )

        assert "remove_filters" in prompt
        assert "relative_period" in prompt
        assert "chỉ lấy miền Bắc" in prompt
        assert "kế thừa" in prompt
