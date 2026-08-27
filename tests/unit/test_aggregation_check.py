"""Fan-out, units, subtotals — the checks on the numbers themselves.

The failure this covers produces no error. Given

    SELECT SUM(o.amount) FROM orders o JOIN order_items i ON i.order_id = o.id

every order row is repeated once per item before the SUM runs, so the total is
multiplied by the average item count. Right order of magnitude, formatted the
same way a correct figure would be, nothing in the answer to distinguish it.

What the tests hold to:

  * **Risk, never wrongness.** Whether a join fans out depends on the
    cardinality of its key, and nothing here knows it — the Schema Catalog
    stores names and types, not primary keys. Claiming a figure is wrong when
    it cannot be shown wrong trains readers to dismiss the warning.
  * **The safe shapes are never flagged.** COUNT(DISTINCT), MIN/MAX, and a
    statement with no join. Noise on every query is the same as no check.
  * **A compiled metric is structurally immune**, and the answer says so — a
    single source_table with no joins has nothing to duplicate.
  * **Units and subtotals are exact**, unlike fan-out, so they are stated as
    errors rather than risks.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from libs.shared.aggregation_check import (
    FAN_OUT,
    SUBTOTAL_MISMATCH,
    UNIT_MISMATCH,
    analyse,
    check_subtotals,
    check_units,
    describe,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
RESPONDER = REPO_ROOT / "services" / "workflow-runtime" / "nodes" / "model_harness" / "responder.py"
DATA_HANDLER = (
    REPO_ROOT / "services" / "workflow-runtime" / "nodes" / "sandbox"
    / "tool_executor" / "handlers" / "data.py"
)

pytestmark = pytest.mark.unit


class TestTheFanOutItself:
    @pytest.mark.parametrize("sql", [
        # The canonical case, with and without aliases.
        "SELECT SUM(o.amount) FROM orders o JOIN order_items i ON i.order_id = o.id",
        "SELECT SUM(orders.amount) FROM orders JOIN order_items ON order_items.order_id = orders.id",
        # COUNT(*) after a join counts join pairs, not orders.
        "SELECT COUNT(*) FROM orders o JOIN order_items i ON i.order_id = o.id",
        # Unqualified column — cannot be shown safe, so it is not assumed safe.
        "SELECT SUM(amount) FROM orders o JOIN order_items i ON i.order_id = o.id",
        "SELECT AVG(o.amount) FROM orders o LEFT JOIN items i ON i.oid = o.id",
        # Grouped output is no protection: each group is multiplied.
        "SELECT r.name, SUM(o.amount) FROM orders o JOIN regions r ON r.id = o.region_id "
        "JOIN order_items i ON i.order_id = o.id GROUP BY r.name",
    ])
    def test_a_multiplied_aggregate_is_flagged(self, sql):
        report = analyse(sql)
        assert report.has_risk
        assert report.findings[0].kind == FAN_OUT
        assert not report.structurally_safe

    def test_a_column_from_the_joined_side_is_safe_with_one_join(self):
        """`SUM(i.qty)` over the many-side: each item row appears once."""
        report = analyse(
            "SELECT SUM(i.qty) FROM orders o JOIN order_items i ON i.order_id = o.id"
        )
        assert not report.has_risk
        assert report.structurally_safe

    def test_but_not_with_two_joins(self):
        """A second join can still duplicate the many-side rows. Exempting a
        joined alias outright would miss this entirely."""
        report = analyse(
            "SELECT SUM(i.qty) FROM orders o JOIN items i ON i.oid = o.id "
            "JOIN shipments s ON s.oid = o.id"
        )
        assert report.has_risk

    @pytest.mark.parametrize("sql", [
        "SELECT COUNT(DISTINCT o.id) FROM orders o JOIN order_items i ON i.order_id = o.id",
        "SELECT SUM(DISTINCT o.amount) FROM orders o JOIN items i ON i.oid = o.id",
    ])
    def test_distinct_is_the_fix_and_is_not_flagged(self, sql):
        assert not analyse(sql).has_risk

    def test_min_and_max_are_immune_to_duplication(self):
        """Repeating a row cannot change either, so flagging them would be
        noise — and noise on every query is the same as no check at all."""
        assert not analyse(
            "SELECT MAX(o.amount) FROM orders o JOIN order_items i ON i.order_id = o.id"
        ).has_risk

    def test_no_join_means_nothing_can_multiply(self):
        report = analyse("SELECT SUM(amount) FROM orders WHERE status <> 'refunded'")
        assert not report.has_risk
        assert report.structurally_safe
        assert report.joins == 0

    def test_a_query_with_no_aggregate_is_not_called_safe(self):
        """"Nothing to be wrong about" and "checked and sound" are different
        claims, and only the second should read as reassurance."""
        report = analyse("SELECT o.id, i.sku FROM orders o JOIN order_items i ON i.order_id = o.id")
        assert not report.has_risk
        assert not report.structurally_safe

    def test_the_finding_names_the_expression_it_objects_to(self):
        """So a reader can check the claim rather than take it on faith."""
        report = analyse("SELECT SUM(o.amount) FROM orders o JOIN order_items i ON i.order_id = o.id")
        assert report.findings[0].expression == "SUM(o.amount)"
        assert "SUM(DISTINCT ...)" in report.findings[0].detail

    def test_it_speaks_of_risk_not_of_error(self):
        """Cardinality is unknown here, so a claim of wrongness would be
        unfounded — and one unfounded warning teaches a reader to skip the
        next one."""
        detail = analyse(
            "SELECT SUM(o.amount) FROM orders o JOIN order_items i ON i.order_id = o.id"
        ).findings[0].detail.lower()
        assert "if one row" in detail
        assert "verify" in detail
        for overclaim in ("is wrong", "is incorrect", "is double"):
            assert overclaim not in detail


class TestItReadsSqlWithoutBeingFooledByIt:
    def test_a_join_inside_a_string_literal_is_not_a_join(self):
        assert not analyse(
            "SELECT SUM(amount) FROM orders WHERE note = 'join order_items on x'"
        ).has_risk

    @pytest.mark.parametrize("sql", ["", "   ", None, "NOT SQL AT ALL ((("])
    def test_unparseable_input_reports_nothing_rather_than_something_wrong(self, sql):
        """A bad finding costs more trust than a missed one."""
        report = analyse(sql)
        assert not report.has_risk

    def test_lowercase_sql_is_analysed_the_same(self):
        assert analyse(
            "select sum(o.amount) from orders o join order_items i on i.order_id = o.id"
        ).has_risk

    def test_the_report_round_trips_through_json_shaped_dicts(self):
        """It is computed where the query runs and read again in the responder,
        crossing agent state as plain JSON in between."""
        report = analyse("SELECT SUM(o.amount) FROM orders o JOIN items i ON i.oid = o.id")
        as_dict = report.to_dict()
        assert as_dict["findings"][0]["kind"] == FAN_OUT
        assert describe(as_dict) == describe(report)


class TestUnitsAreExact:
    def test_two_declared_units_in_one_answer_is_an_error(self):
        finding = check_units(["VND", "USD"])
        assert finding is not None
        assert finding.kind == UNIT_MISMATCH
        assert "VND" in finding.detail and "USD" in finding.detail

    def test_one_unit_is_fine(self):
        assert check_units(["VND", "VND", "VND"]) is None

    def test_undeclared_is_not_a_conflict(self):
        """Blank means nobody said, which is not the same as disagreeing."""
        assert check_units(["", "VND", ""]) is None
        assert check_units([]) is None


class TestSubtotalsAreExact:
    def test_parts_that_do_not_add_up_are_reported(self):
        finding = check_subtotals([10.0, 20.0, 30.0], total=100.0)
        assert finding is not None
        assert finding.kind == SUBTOTAL_MISMATCH
        assert "60.00" in finding.detail and "100.00" in finding.detail

    def test_parts_that_add_up_are_not(self):
        assert check_subtotals([10.0, 20.0, 30.0], total=60.0) is None

    def test_float_noise_is_tolerated(self):
        """A sum of many floats does not land exactly, and flagging that would
        be a false alarm on every correct answer."""
        assert check_subtotals([0.1] * 10, total=1.0) is None

    def test_no_parts_means_nothing_to_reconcile(self):
        assert check_subtotals([], total=100.0) is None


class TestItReachesTheAnswer:
    def _tree(self, path):
        return ast.parse(path.read_text(encoding="utf-8"))

    def test_both_sql_paths_attach_the_analysis(self):
        """The model-written path is where fan-out happens; the compiled path
        attaches it too, because 'checked and structurally safe' is a claim
        worth making explicitly."""
        calls = [
            n for n in ast.walk(self._tree(DATA_HANDLER))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "analyse"
        ]
        assert len(calls) == 2

    def test_the_responder_renders_it_and_appends_the_rule(self):
        source = RESPONDER.read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {
            n.func.attr for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        assert "describe" in names
        assert "check_units" in names
        assert "_AGGREGATION_RULE" in source

    def test_the_rule_forbids_deriving_further_numbers_from_it(self):
        """A share or a growth rate computed from a multiplied total is wrong
        too, and looks even more authoritative for having been calculated."""
        source = RESPONDER.read_text(encoding="utf-8")
        tree = ast.parse(source)
        rule = next(
            n.value for n in ast.walk(tree)
            if isinstance(n, ast.Assign)
            and any(getattr(t, "id", "") == "_AGGREGATION_RULE" for t in n.targets)
        )
        text = " ".join(
            c.value for c in ast.walk(rule) if isinstance(c, ast.Constant) and isinstance(c.value, str)
        ).lower()
        assert "do not compute anything" in text
        assert "risk, not a proven error" in text

    def test_nothing_is_rendered_when_there_is_no_risk(self):
        assert describe(analyse("SELECT SUM(amount) FROM orders")) == ""
        assert describe(None) == ""
        assert describe({}) == ""
        assert describe("not a report") == ""


class TestTheCompiledMetricPathIsTheWayOut:
    def test_semantic_layer_sql_is_structurally_safe(self):
        """A metric contract names one source_table and the compiler emits no
        joins, so there is nothing to duplicate. This is the concrete advantage
        of a contract over model-written SQL."""
        compiled = (
            "SELECT SUM(amount) AS value FROM orders "
            "WHERE status NOT IN ('refunded') "
            "AND transaction_date >= '2026-06-01' AND transaction_date < '2026-07-01' "
            "LIMIT 1000"
        )
        report = analyse(compiled)
        assert report.structurally_safe
        assert not report.has_risk
        assert report.joins == 0
