"""Semantic Layer wiring — backend/services/workflow_runtime.

The pure logic is covered in test_semantic_layer.py. What matters here is that
it is actually REACHED, and that it yields correctly when it cannot answer:

  * a resolved metric short-circuits the LLM path entirely — no prompt, no
    model-authored SQL;
  * an unresolved question, an unreachable registry, or a metric that cannot
    serve this request all fall through to the existing path rather than
    failing the turn;
  * a stored contract that is malformed is dropped at load, not at query time.

Falling back is the common case and must stay cheap and silent — the registry
answers the questions it can guarantee, not all of them.
"""
from __future__ import annotations

import json
import pathlib
import sys
import types
from unittest.mock import AsyncMock

import pytest

from libs.semantic import (
    Aggregation,
    FilterOp,
    InvalidMetricDefinition,
    MetricDefinition,
    MetricFilter,
    MetricResolver,
    TimeGrain,
    build_catalog,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "workflow_runtime")

pytestmark = pytest.mark.unit

TENANT = "a0000000-0000-0000-0000-000000000001"

REVENUE_NET = MetricDefinition(
    key="revenue_net",
    version=3,
    name="Doanh thu thuần",
    aggregation=Aggregation.SUM,
    source_table="orders",
    expression="amount",
    time_column="transaction_date",
    default_grain=TimeGrain.NONE,
    unit="VND",
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


def _data():
    import nodes.sandbox.tool_executor.handlers.data as data
    return data


def _stub_semantic(monkeypatch, match):
    """Replace the semantic_layer module the handler imports lazily."""
    mod = types.ModuleType("semantic_layer")
    mod.match_question = AsyncMock(return_value=match)
    monkeypatch.setitem(sys.modules, "semantic_layer", mod)
    return mod


def _real_match():
    """A genuine SemanticMatch, built through the real resolver + compiler so the
    test exercises the production objects rather than a hand-made stand-in."""
    import semantic_layer

    resolver = MetricResolver(build_catalog([REVENUE_NET], {"revenue_net": ["doanh thu thuần"]}))
    resolution = resolver.resolve("doanh thu thuần")
    from libs.semantic import compile_metric

    compiled = compile_metric(resolution.definition)
    return semantic_layer.SemanticMatch(resolution.definition, compiled, resolution.matched_term)


def _cfg(**tool_cfg):
    conns = tool_cfg.pop("db_connections", {"Sales": {"host": "h", "database": "d"}})
    return {"tenant_id": TENANT, "tool_config": {"db_connections": conns, **tool_cfg}}


class TestResolvedQuestionSkipsTheModel:
    async def test_no_llm_call_and_no_schema_lookup_when_a_metric_matches(self, monkeypatch):
        """The whole point: an approved contract means there is nothing for a
        model to decide, so neither the schema prompt nor the SQL prompt runs."""
        data = _data()
        _stub_semantic(monkeypatch, _real_match())
        monkeypatch.setattr(data, "_sandbox_sql", AsyncMock(return_value=([{"value": 42}], None, False, None)))
        completion = AsyncMock()
        monkeypatch.setattr(data.litellm, "acompletion", completion)
        schema = AsyncMock()
        monkeypatch.setattr(data, "_get_schema_text", schema)

        results, errors = await data._text_to_sql_handler({"query": "doanh thu thuần"}, _cfg())

        assert errors == []
        assert json.loads(results[0]["content"]) == [{"value": 42}]
        completion.assert_not_awaited()
        schema.assert_not_awaited()

    async def test_the_executed_sql_is_the_compiled_contract(self, monkeypatch):
        data = _data()
        _stub_semantic(monkeypatch, _real_match())
        sandbox = AsyncMock(return_value=([{"value": 1}], None, False, None))
        monkeypatch.setattr(data, "_sandbox_sql", sandbox)

        await data._text_to_sql_handler({"query": "doanh thu thuần"}, _cfg())

        sql = sandbox.await_args.args[0]
        assert "SUM(amount)" in sql
        # The contract's default filter is present without anyone asking for it.
        assert "status NOT IN ('refunded')" in sql

    async def test_result_carries_contract_provenance(self, monkeypatch):
        """Provenance is derived from the contract, so it cannot drift from the
        SQL that actually ran the way a model-written explanation can."""
        data = _data()
        _stub_semantic(monkeypatch, _real_match())
        monkeypatch.setattr(data, "_sandbox_sql", AsyncMock(return_value=([{"value": 1}], None, False, None)))

        results, _ = await data._text_to_sql_handler({"query": "doanh thu thuần"}, _cfg())

        meta = results[0]["metadata"]
        assert meta["generated_by"] == "semantic_layer"
        assert meta["semantic"]["metric"] == "revenue_net@3"
        assert meta["semantic"]["unit"] == "VND"
        assert meta["semantic"]["business_date_column"] == "transaction_date"
        assert meta["semantic"]["owner"] == "finance@example.com"

    async def test_metric_that_fails_to_execute_is_reported_not_re_guessed(self, monkeypatch):
        """A compiled metric failing means the contract disagrees with the real
        database — an operator problem. Silently re-asking a model to guess the
        same query would hide it."""
        data = _data()
        _stub_semantic(monkeypatch, _real_match())
        monkeypatch.setattr(data, "_sandbox_sql", AsyncMock(return_value=(None, 'column "amount" does not exist', False, None)))
        completion = AsyncMock()
        monkeypatch.setattr(data.litellm, "acompletion", completion)

        results, errors = await data._text_to_sql_handler({"query": "doanh thu thuần"}, _cfg())

        assert results == []
        assert "revenue_net@3" in errors[0]["error"]
        completion.assert_not_awaited()


class TestFallback:
    async def test_unresolved_question_uses_the_existing_llm_path(self, monkeypatch):
        data = _data()
        _stub_semantic(monkeypatch, None)
        monkeypatch.setattr(data, "_get_schema_text", AsyncMock(return_value="orders(id int)"))
        monkeypatch.setattr(data.litellm, "acompletion", AsyncMock(
            return_value=type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": "SELECT 1"})()})()]})(),
        ))
        run_query = AsyncMock(return_value=({"id": "sql_1", "metadata": {"db_name": "Sales"}}, None))
        monkeypatch.setattr(data, "_run_one_query", run_query)

        results, errors = await data._text_to_sql_handler({"query": "thời tiết hôm nay"}, _cfg())

        assert errors == []
        assert results[0]["id"] == "sql_1"
        run_query.assert_awaited_once()

    async def test_registry_failure_falls_back_rather_than_failing_the_turn(self, monkeypatch):
        data = _data()
        mod = types.ModuleType("semantic_layer")
        mod.match_question = AsyncMock(side_effect=RuntimeError("postgres down"))
        monkeypatch.setitem(sys.modules, "semantic_layer", mod)
        monkeypatch.setattr(data, "_get_schema_text", AsyncMock(return_value="orders(id int)"))
        monkeypatch.setattr(data.litellm, "acompletion", AsyncMock(
            return_value=type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": "SELECT 1"})()})()]})(),
        ))
        run_query = AsyncMock(return_value=({"id": "sql_1", "metadata": {"db_name": "Sales"}}, None))
        monkeypatch.setattr(data, "_run_one_query", run_query)

        results, errors = await data._text_to_sql_handler({"query": "doanh thu thuần"}, _cfg())

        assert errors == []
        assert results[0]["id"] == "sql_1"

    async def test_multi_connection_does_not_use_the_semantic_path(self, monkeypatch):
        """A contract does not yet name which connection it lives on, and
        inferring it would reintroduce the guesswork the layer removes."""
        data = _data()
        semantic = _stub_semantic(monkeypatch, _real_match())
        monkeypatch.setattr(data, "_get_schema_text", AsyncMock(return_value="schema"))
        monkeypatch.setattr(data.litellm, "acompletion", AsyncMock(
            return_value=type("R", (), {"choices": [type("C", (), {"message": type("M", (), {
                "content": json.dumps({"queries": [{"db_name": "Sales", "sql": "SELECT 1"}]})})()})()]})(),
        ))
        monkeypatch.setattr(data, "_run_one_query", AsyncMock(return_value=({"id": "x", "metadata": {}}, None)))

        await data._text_to_sql_handler(
            {"query": "doanh thu thuần"},
            _cfg(db_connections={"Sales": {"host": "h", "database": "d"},
                                 "HR": {"host": "h2", "database": "d2"}}),
        )

        semantic.match_question.assert_not_awaited()

    async def test_no_tenant_skips_the_semantic_path(self, monkeypatch):
        """Contracts are tenant-scoped by RLS; without a tenant there is nothing
        safe to load."""
        data = _data()
        semantic = _stub_semantic(monkeypatch, _real_match())
        monkeypatch.setattr(data, "_get_schema_text", AsyncMock(return_value="schema"))
        monkeypatch.setattr(data.litellm, "acompletion", AsyncMock(
            return_value=type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": "SELECT 1"})()})()]})(),
        ))
        monkeypatch.setattr(data, "_run_one_query", AsyncMock(return_value=({"id": "x", "metadata": {}}, None)))

        cfg = _cfg()
        cfg["tenant_id"] = ""
        await data._text_to_sql_handler({"query": "doanh thu thuần"}, cfg)

        semantic.match_question.assert_not_awaited()


class TestContractLoading:
    def test_malformed_contract_is_dropped_at_load_not_at_query_time(self):
        """One bad row must not take the whole registry offline, and the
        operator who stored it should see it in the logs."""
        import semantic_layer

        good = types.SimpleNamespace(
            metric_key="ok", name="OK", description="", owner="o",
            version=1, status="approved", source_table="orders",
            aggregation="sum", expression="amount", time_column=None,
            default_grain="none", unit="", dimensions=[], default_filters=[],
        )
        bad = types.SimpleNamespace(
            metric_key="broken", name="Broken", description="", owner="o",
            version=1, status="approved", source_table="orders; DROP TABLE x",
            aggregation="sum", expression="amount", time_column=None,
            default_grain="none", unit="", dimensions=[], default_filters=[],
        )

        assert semantic_layer._row_to_definition(good).key == "ok"
        with pytest.raises(InvalidMetricDefinition):
            semantic_layer._row_to_definition(bad)

    def test_unknown_aggregation_is_rejected(self):
        import semantic_layer

        row = types.SimpleNamespace(
            metric_key="x", name="X", description="", owner="o",
            version=1, status="approved", source_table="orders",
            aggregation="median", expression="amount", time_column=None,
            default_grain="none", unit="", dimensions=[], default_filters=[],
        )

        with pytest.raises(InvalidMetricDefinition):
            semantic_layer._row_to_definition(row)

    async def test_cache_is_per_tenant(self, monkeypatch):
        """One tenant's contracts must never serve another's question."""
        import semantic_layer

        semantic_layer.invalidate()
        loads: list[str] = []

        async def fake_load(tenant_id):
            loads.append(tenant_id)
            return MetricResolver(build_catalog([], {}))

        monkeypatch.setattr(semantic_layer, "_load_resolver", fake_load)

        await semantic_layer.get_resolver("tenant-a")
        await semantic_layer.get_resolver("tenant-a")
        await semantic_layer.get_resolver("tenant-b")

        assert loads == ["tenant-a", "tenant-b"]
        semantic_layer.invalidate()


class TestTimeWordsBecomeTheGrain:
    """"doanh thu thuần tháng này theo vùng" — the planner files "tháng" under
    dimensions. It is a time bucket, not a dimension the contract must define;
    treating it as unknown pushed a contract question onto the LLM SQL path."""

    def _args(self, slots_dict):
        import semantic_layer

        from libs.semantic import AnalyticalSlots
        return semantic_layer.slots_to_compiler_args(REVENUE_NET, AnalyticalSlots.from_dict(slots_dict))

    def test_thang_becomes_month_grain_and_leaves_dimensions(self):
        dims, grain, _, _ = self._args({"metric": "doanh thu thuần", "dimensions": ["tháng"]})
        assert dims == []
        assert grain is TimeGrain.MONTH

    def test_mixed_dimensions_keep_the_business_one(self):
        dims, grain, _, _ = self._args({"dimensions": ["vùng", "Quý"]})
        assert dims == ["vùng"]
        assert grain is TimeGrain.QUARTER

    def test_generic_time_word_falls_back_to_the_default_grain(self):
        dims, grain, _, _ = self._args({"dimensions": ["thời gian"]})
        assert dims == [] and grain is None  # None → compile_metric uses definition.default_grain

    def test_explicit_grain_wins_over_an_inferred_one(self):
        _, grain, _, _ = self._args({"dimensions": ["tháng"], "grain": "year"})
        assert grain is TimeGrain.YEAR

    def test_a_contract_dimension_named_like_a_time_word_is_kept(self):
        import dataclasses

        import semantic_layer

        from libs.semantic import AnalyticalSlots, MetricDimension
        contract = dataclasses.replace(REVENUE_NET, dimensions=(MetricDimension(key="tháng", column="fiscal_month"),))
        dims, grain, _, _ = semantic_layer.slots_to_compiler_args(contract, AnalyticalSlots.from_dict({"dimensions": ["tháng"]}))
        assert dims == ["tháng"] and grain is None
