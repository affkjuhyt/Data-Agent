"""Semantic Layer (backend/libs/semantic) — resolution and SQL compilation.

The audit's charge was that business meaning lived only in prompts, so "the
system cannot distinguish gross revenue from net, row count from distinct-entity
count, created-at from business date, or current from same-period-last-year".
Each of those four is a test class below; if any of them regresses the layer has
stopped doing the one thing it exists for.
"""
from __future__ import annotations

import pytest

from libs.semantic import (
    Aggregation,
    FilterOp,
    InvalidMetricDefinition,
    MetricDefinition,
    MetricDimension,
    MetricFilter,
    MetricResolver,
    MetricStatus,
    TimeGrain,
    UnsupportedRequest,
    build_catalog,
    compile_metric,
    normalize,
)

pytestmark = pytest.mark.unit


def _metric(**overrides) -> MetricDefinition:
    base = {
        "key": "revenue_gross",
        "version": 1,
        "name": "Doanh thu gộp",
        "aggregation": Aggregation.SUM,
        "source_table": "orders",
        "expression": "amount",
        "time_column": "transaction_date",
        "default_grain": TimeGrain.NONE,
        "unit": "VND",
        "owner": "finance@example.com",
    }
    base.update(overrides)
    return MetricDefinition(**base)


REVENUE_GROSS = _metric()
REVENUE_NET = _metric(
    key="revenue_net",
    name="Doanh thu thuần",
    default_filters=(MetricFilter("status", FilterOp.NOT_IN, ["refunded", "cancelled"]),),
)


# ── The four confusions the layer exists to end ────────────────────────────

class TestGrossVersusNet:
    def test_they_are_different_metrics_with_different_sql(self):
        gross = compile_metric(REVENUE_GROSS)
        net = compile_metric(REVENUE_NET)

        assert "WHERE" not in gross.sql
        assert "status NOT IN ('refunded', 'cancelled')" in net.sql

    def test_net_phrasing_does_not_resolve_to_gross(self):
        """Longest synonym wins. Substring matching would send every net
        question to the gross metric and quietly overstate the number."""
        catalog = build_catalog(
            [REVENUE_GROSS, REVENUE_NET],
            {"revenue_gross": ["doanh thu"], "revenue_net": ["doanh thu thuần"]},
        )
        resolver = MetricResolver(catalog)

        assert resolver.resolve("doanh thu thuần quý 3").definition.key == "revenue_net"
        assert resolver.resolve("doanh thu quý 3").definition.key == "revenue_gross"

    def test_the_default_filter_cannot_be_dropped_by_the_caller(self):
        """Contract filters are unconditional — a caller adding their own must
        get them ANDed on, never substituted."""
        compiled = compile_metric(
            REVENUE_NET, extra_filters=[MetricFilter("region", FilterOp.EQ, "North")],
        )

        assert "status NOT IN" in compiled.sql
        assert "region = 'North'" in compiled.sql


class TestRowsVersusDistinctEntities:
    def test_count_and_count_distinct_are_separate_contracts(self):
        orders = _metric(key="order_count", aggregation=Aggregation.COUNT, expression="*")
        customers = _metric(
            key="customer_count", aggregation=Aggregation.COUNT_DISTINCT, expression="customer_id",
        )

        assert "COUNT(*)" in compile_metric(orders).sql
        assert "COUNT(DISTINCT customer_id)" in compile_metric(customers).sql

    def test_star_is_rejected_for_any_aggregation_but_count(self):
        """SUM(*) is not SQL; catching it at definition time keeps a broken
        contract out of the registry rather than out of one query."""
        with pytest.raises(InvalidMetricDefinition, match="only valid for COUNT"):
            _metric(aggregation=Aggregation.SUM, expression="*")


class TestBusinessDateVersusCreatedAt:
    def test_time_filtering_uses_the_contract_column_not_a_guess(self):
        compiled = compile_metric(REVENUE_GROSS, time_range=("2026-06-01", "2026-07-01"))

        assert "transaction_date >= '2026-06-01'" in compiled.sql
        assert "created_at" not in compiled.sql

    def test_a_metric_without_a_business_date_refuses_to_be_time_filtered(self):
        """Better a fallback than a total silently computed over the wrong
        column."""
        stock = _metric(key="stock_level", time_column="", default_grain=TimeGrain.NONE)

        with pytest.raises(UnsupportedRequest, match="no time_column"):
            compile_metric(stock, time_range=("2026-06-01", "2026-07-01"))

    def test_a_grain_without_a_time_column_is_rejected_at_definition_time(self):
        with pytest.raises(InvalidMetricDefinition, match="no time_column"):
            _metric(time_column="", default_grain=TimeGrain.MONTH)

    def test_range_is_half_open_so_a_boundary_row_is_not_double_counted(self):
        """BETWEEN would include the end date in both June and July."""
        compiled = compile_metric(REVENUE_GROSS, time_range=("2026-06-01", "2026-07-01"))

        assert ">= '2026-06-01'" in compiled.sql
        assert "< '2026-07-01'" in compiled.sql
        assert "BETWEEN" not in compiled.sql


class TestPeriodComparison:
    def test_the_same_contract_serves_both_periods_identically(self):
        """Same-period-last-year is only meaningful if both halves are computed
        the same way — which is exactly what a shared contract guarantees."""
        this_year = compile_metric(REVENUE_NET, time_range=("2026-06-01", "2026-07-01"))
        last_year = compile_metric(REVENUE_NET, time_range=("2025-06-01", "2025-07-01"))

        assert this_year.sql.replace("2026", "YYYY") == last_year.sql.replace("2025", "YYYY")
        assert this_year.metric_ref == last_year.metric_ref


# ── Resolution ─────────────────────────────────────────────────────────────

class TestNormalization:
    def test_case_and_punctuation_are_folded(self):
        assert normalize("  Doanh Thu!!  ") == "doanh thu"

    def test_vietnamese_tones_are_preserved(self):
        """Stripping diacritics would fold words that differ only by tone into
        one, letting a metric answer for another."""
        assert normalize("doanh thu thuần") == "doanh thu thuần"
        assert normalize("doanh thu thuan") != normalize("doanh thu thuần")

    def test_decomposed_and_precomposed_forms_match(self):
        """Same word, two Unicode spellings: precomposed U+1EC7 versus
        e + combining dot-below + circumflex. Without NFC these are different
        dict keys, and a synonym typed on one keyboard silently fails to match
        the same word typed on another."""
        precomposed = "Vi" + chr(0x1EC7) + "t"
        decomposed = "Vie" + chr(0x323) + chr(0x302) + "t"
        assert precomposed != decomposed
        assert normalize(precomposed) == normalize(decomposed)


class TestResolver:
    def _resolver(self, extra=None):
        catalog = build_catalog(
            [REVENUE_GROSS, REVENUE_NET],
            extra or {"revenue_gross": ["doanh thu", "doanh số", "revenue"], "revenue_net": ["doanh thu thuần"]},
        )
        return MetricResolver(catalog)

    def test_synonyms_converge_on_one_metric(self):
        r = self._resolver()

        for phrasing in ("doanh thu tháng 6", "DOANH SỐ tháng 6", "revenue in June"):
            assert r.resolve(phrasing).definition.key == "revenue_gross"

    def test_a_metric_is_reachable_by_its_own_key_and_name(self):
        """Registering a metric without synonyms must still make it findable."""
        r = MetricResolver(build_catalog([REVENUE_GROSS], {}))

        assert r.resolve("revenue gross please").definition.key == "revenue_gross"
        assert r.resolve("cho tôi Doanh thu gộp").definition.key == "revenue_gross"

    def test_unrelated_question_resolves_to_nothing(self):
        """The normal case — and it must be None, not a wrong guess."""
        assert self._resolver().resolve("thời tiết hôm nay thế nào") is None

    def test_partial_word_does_not_match(self):
        """Substring matching would fire 'revenue' inside 'revenues_archive'."""
        r = MetricResolver(build_catalog([REVENUE_GROSS], {"revenue_gross": ["cost"]}))

        assert r.resolve("costume budget") is None

    def test_deprecated_metric_does_not_answer(self):
        deprecated = _metric(key="old_revenue", status=MetricStatus.DEPRECATED)
        r = MetricResolver(build_catalog([deprecated], {"old_revenue": ["doanh thu cũ"]}))

        assert r.resolve("doanh thu cũ") is None

    def test_conflicting_synonym_resolves_deterministically(self):
        """Two metrics claiming one phrase must not depend on dict order."""
        a = build_catalog([REVENUE_GROSS, REVENUE_NET],
                          {"revenue_gross": ["doanh thu"], "revenue_net": ["doanh thu"]})
        b = build_catalog([REVENUE_NET, REVENUE_GROSS],
                          {"revenue_net": ["doanh thu"], "revenue_gross": ["doanh thu"]})

        assert a.synonyms["doanh thu"] == "revenue_gross"
        assert b.synonyms["doanh thu"] == "revenue_gross"  # metric key order, not insertion order


# ── Compilation ────────────────────────────────────────────────────────────

class TestCompilation:
    def test_grain_buckets_by_the_business_date(self):
        compiled = compile_metric(REVENUE_GROSS, grain=TimeGrain.MONTH)

        assert "DATE_TRUNC('month', transaction_date) AS period" in compiled.sql
        assert "GROUP BY DATE_TRUNC('month', transaction_date)" in compiled.sql

    def test_clickhouse_uses_its_own_bucketing_function(self):
        compiled = compile_metric(REVENUE_GROSS, grain=TimeGrain.MONTH, dialect="clickhouse")

        assert "toStartOfMonth(transaction_date)" in compiled.sql
        assert "DATE_TRUNC" not in compiled.sql

    def test_dimension_is_looked_up_by_business_key(self):
        m = _metric(dimensions=(MetricDimension(key="miền", column="region_name"),))

        compiled = compile_metric(m, dimensions=["miền"])

        assert "region_name AS miền" in compiled.sql
        assert "GROUP BY region_name" in compiled.sql

    def test_unknown_dimension_raises_rather_than_reaching_sql(self):
        """A model proposing a breakdown that does not exist must fail loudly."""
        with pytest.raises(UnsupportedRequest, match="no dimension"):
            compile_metric(REVENUE_GROSS, dimensions=["colour"])

    def test_every_statement_is_row_capped(self):
        assert "LIMIT" in compile_metric(REVENUE_GROSS).sql

    def test_provenance_is_derived_from_the_contract(self):
        compiled = compile_metric(REVENUE_NET, grain=TimeGrain.MONTH)

        assert compiled.metric_ref == "revenue_net@1"
        assert compiled.aggregation == "sum"
        assert compiled.unit == "VND"
        assert compiled.grain == "month"
        assert compiled.time_column == "transaction_date"


class TestInjectionSurface:
    """Definitions are operator-authored config, but they are interpolated into
    SQL, so the constraint is enforced here rather than assumed."""

    @pytest.mark.parametrize("bad", [
        "orders; DROP TABLE x",
        "orders WHERE 1=1",
        'orders"',
        "orders--",
        "",
    ])
    def test_malformed_identifiers_are_rejected(self, bad):
        with pytest.raises(InvalidMetricDefinition):
            _metric(source_table=bad)

    def test_filter_column_is_validated_too(self):
        with pytest.raises(InvalidMetricDefinition):
            MetricFilter("status; DROP TABLE x", FilterOp.EQ, "ok")

    def test_string_filter_values_are_escaped_not_trusted(self):
        compiled = compile_metric(
            REVENUE_GROSS, extra_filters=[MetricFilter("region", FilterOp.EQ, "O'Brien")],
        )

        assert "'O''Brien'" in compiled.sql

    def test_time_bounds_must_parse_as_iso_dates(self):
        """The one input most likely to be model-authored."""
        with pytest.raises(UnsupportedRequest, match="ISO date"):
            compile_metric(REVENUE_GROSS, time_range=("2026-06-01", "'; DROP TABLE x --"))

    def test_unknown_dialect_is_rejected(self):
        with pytest.raises(UnsupportedRequest, match="unsupported dialect"):
            compile_metric(REVENUE_GROSS, dialect="mssql")
