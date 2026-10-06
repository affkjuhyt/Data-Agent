"""Lineage / owner / freshness / classification, end to end.

The defect this covers: an answer built from a warehouse could not say when the
data behind it was last loaded. `SchemaCatalog` tracked when the DDL changed and
when the crawler last looked — neither is the age of the data — so a question
answered off a pipeline that broke on Tuesday read exactly like one answered off
this morning's load.

What the tests hold to, in order of how badly each would hurt if it broke:

  * **Absence is never fresh.** No watermark, no SLA, no catalog entry, a failed
    lookup — every one of them reports UNKNOWN. The single worst outcome here is
    a confident "as of now" that nobody measured.
  * **The oldest input wins.** An answer joining a table loaded an hour ago to
    one loaded last week is a statement about last week.
  * **Freshness is written on every crawl, not only when the schema changed.**
    The unchanged-schema path is the normal one; writing the watermark only on
    the changed path would freeze `data_as_of` at whenever the DDL last moved.
  * **Declared facts are never inferred.** An unrecognised classification label
    is dropped, not stored — a label no comparison understands reads like a
    control while behaving like nothing.

The crawler's half (declared policy, watermark query, the write path) runs in
the platform_workers image: tests/unit/platform_workers/test_data_governance_crawler.py.
"""
from __future__ import annotations

import pathlib
import sys
import types
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from libs.shared import freshness
from libs.shared.freshness import (
    FRESH,
    STALE,
    UNKNOWN,
    Provenance,
    TableGovernance,
    describe,
    normalize_classification,
    summarize,
)

from .._loader import stub_if_missing
from ._paths import RUNTIME_LAYER_DIRS

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_RUNTIME_DIR = str(REPO_ROOT / "backend" / "services" / "workflow_runtime")

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
TENANT = "a0000000-0000-0000-0000-000000000001"
WAREHOUSE = "b0000000-0000-0000-0000-000000000002"

HOUR = 3600
DAY = 86400


def _table(name="orders", **kw) -> TableGovernance:
    return TableGovernance(table=name, schema_name=kw.pop("schema_name", "public"), **kw)


# Bare module names that several services each define at their own top level.
# Whichever service's directory reached sys.path first wins and stays cached, so
# a full-suite run can leave `observability` pointing at another service's copy
# while this file wants workflow_runtime's. Dropped from the cache (monkeypatch
# restores them afterwards) so the import below resolves against the path this
# fixture just prepended.
_AMBIGUOUS_MODULES = ("observability", "state", "schema_catalog", "data_provenance", "redis_cache")


@pytest.fixture
def runtime_path(monkeypatch):
    stub_if_missing(
        monkeypatch,
        ("qdrant_client", {"AsyncQdrantClient": MagicMock, "QdrantClient": MagicMock}),
        ("qdrant_client.models", {
            "Distance": MagicMock(), "VectorParams": MagicMock, "Filter": MagicMock,
            "FieldCondition": MagicMock, "MatchValue": MagicMock, "PointStruct": MagicMock,
        }),
    )
    for _d in RUNTIME_LAYER_DIRS:
        monkeypatch.syspath_prepend(_d)
    for name in _AMBIGUOUS_MODULES:
        module = sys.modules.get(name)
        if module is not None and getattr(module, "__file__", None) is None:
            # A namespace package — i.e. a directory of that name shadowing the
            # real module. Never a legitimate import here.
            monkeypatch.delitem(sys.modules, name, raising=False)
    yield


# ── the vocabulary ─────────────────────────────────────────────────────────

class TestClassification:
    @pytest.mark.parametrize("raw,expected", [
        ("Confidential", "confidential"),
        ("  RESTRICTED  ", "restricted"),
        ("internal_only", "internal"),
        ("pii", "restricted"),
        ("open", "public"),
    ])
    def test_known_labels_normalise(self, raw, expected):
        assert normalize_classification(raw) == expected

    @pytest.mark.parametrize("raw", ["", None, "  ", "classifcation", "top-secret", 7])
    def test_unknown_or_missing_becomes_undeclared(self, raw):
        """Not stored verbatim. A label no downstream comparison understands
        would read as a declared control while behaving like an absent one."""
        assert normalize_classification(raw) == ""

    def test_an_answer_carries_the_highest_label_of_its_inputs(self):
        prov = summarize([
            _table("regions", classification="public"),
            _table("salaries", classification="restricted"),
            _table("orders", classification="internal"),
        ], now=NOW)
        assert prov.classification == "restricted"


# ── per-table status ───────────────────────────────────────────────────────

class TestTableStatus:
    def test_no_watermark_is_unknown_not_fresh(self):
        assert _table(freshness_sla_seconds=DAY).status(NOW) == UNKNOWN

    def test_watermark_without_an_sla_is_unknown(self):
        """Nobody has said how old is too old, so the answer states the age and
        claims nothing. Defaulting to fresh would invent the policy."""
        t = _table(data_as_of=NOW - timedelta(days=400))
        assert t.status(NOW) == UNKNOWN
        assert t.age_seconds(NOW) == pytest.approx(400 * DAY)

    def test_within_the_window_is_fresh(self):
        assert _table(data_as_of=NOW - timedelta(hours=2), freshness_sla_seconds=DAY).status(NOW) == FRESH

    def test_past_the_window_is_stale(self):
        assert _table(data_as_of=NOW - timedelta(days=3), freshness_sla_seconds=DAY).status(NOW) == STALE

    def test_exactly_at_the_window_is_still_fresh(self):
        """The boundary belongs to the compliant side — an SLA of 24h means data
        24h old has met it, not missed it."""
        assert _table(data_as_of=NOW - timedelta(seconds=DAY), freshness_sla_seconds=DAY).status(NOW) == FRESH

    def test_naive_timestamps_are_read_as_utc(self):
        """Postgres hands back naive datetimes for a column declared without a
        timezone; comparing one to an aware `now` raises rather than answering
        wrong, so it must be coerced, not passed through."""
        t = _table(data_as_of=datetime(2026, 8, 28, 6, 0), freshness_sla_seconds=DAY)
        assert t.status(NOW) == FRESH
        assert t.age_seconds(NOW) == pytest.approx(6 * HOUR)

    def test_from_row_accepts_an_orm_object_and_a_mapping(self):
        fields = {
            "schema_name": "public", "table_name": "orders", "owner": "finance",
            "classification": "Confidential", "upstream": ["kafka:orders.cdc"],
            "data_as_of": NOW, "freshness_column": "created_at",
            "freshness_checked_at": NOW, "freshness_sla_seconds": DAY,
            "last_crawled_at": NOW,
        }
        from_mapping = TableGovernance.from_row(dict(fields))
        from_orm = TableGovernance.from_row(types.SimpleNamespace(**fields))
        assert from_mapping == from_orm
        assert from_mapping.qualified_name == "public.orders"
        # Normalised on the way in, so a stored label the vocabulary does not
        # know cannot reach a comparison.
        assert from_mapping.classification == "confidential"

    def test_from_row_reads_a_sqlalchemy_row_mapping(self):
        """`RowMapping` is a Mapping but NOT a dict. Testing for dict sends it
        down the getattr branch, which finds nothing on a Mapping and yields a
        row with every governance field empty — a silent, total loss of
        freshness that still returns a plausible-looking object."""
        from collections.abc import Mapping

        class RowMappingLike(Mapping):
            def __init__(self, data): self._d = data
            def __getitem__(self, k): return self._d[k]
            def __iter__(self): return iter(self._d)
            def __len__(self): return len(self._d)

        assert not isinstance(RowMappingLike({}), dict)
        row = RowMappingLike({
            "schema_name": "public", "table_name": "orders", "owner": "finance",
            "classification": "confidential", "upstream": ["kafka:orders.cdc"],
            "data_as_of": NOW, "freshness_column": "created_at",
            "freshness_checked_at": NOW, "freshness_sla_seconds": DAY,
            "last_crawled_at": NOW,
        })
        got = TableGovernance.from_row(row)
        assert got.owner == "finance"
        assert got.data_as_of == NOW
        assert got.status(NOW) == FRESH

    def test_from_row_tolerates_a_row_missing_every_governance_column(self):
        """A catalog row written before the migration. Must degrade to UNKNOWN,
        not raise on the query path."""
        t = TableGovernance.from_row({"schema_name": "public", "table_name": "orders"})
        assert t.status(NOW) == UNKNOWN
        assert t.owner == "" and t.upstream == ()


# ── rolling up to one answer ───────────────────────────────────────────────

class TestSummarize:
    def test_as_of_is_the_oldest_input_not_the_newest(self):
        """An answer is only as current as its least fresh table. Quoting the
        newest would be the most flattering reading of the evidence."""
        prov = summarize([
            _table("orders", data_as_of=NOW - timedelta(hours=1), freshness_sla_seconds=DAY),
            _table("regions", data_as_of=NOW - timedelta(days=7), freshness_sla_seconds=30 * DAY),
        ], now=NOW)
        assert prov.as_of == NOW - timedelta(days=7)
        assert prov.age_seconds == pytest.approx(7 * DAY)

    def test_one_stale_table_makes_the_answer_stale(self):
        prov = summarize([
            _table("orders", data_as_of=NOW, freshness_sla_seconds=DAY),
            _table("shipments", data_as_of=NOW - timedelta(days=5), freshness_sla_seconds=DAY),
        ], now=NOW)
        assert prov.status == STALE
        assert prov.stale_tables == ("public.shipments",)

    def test_one_unmeasured_table_withdraws_the_fresh_claim(self):
        """FRESH requires every input to be individually provable."""
        prov = summarize([
            _table("orders", data_as_of=NOW, freshness_sla_seconds=DAY),
            _table("regions"),
        ], now=NOW)
        assert prov.status == UNKNOWN
        assert prov.unknown_tables == ("public.regions",)

    def test_stale_outranks_unknown(self):
        prov = summarize([
            _table("regions"),
            _table("orders", data_as_of=NOW - timedelta(days=5), freshness_sla_seconds=DAY),
        ], now=NOW)
        assert prov.status == STALE

    def test_all_measured_and_inside_their_windows_is_fresh(self):
        prov = summarize([
            _table("orders", data_as_of=NOW - timedelta(minutes=5), freshness_sla_seconds=HOUR),
            _table("regions", data_as_of=NOW - timedelta(days=2), freshness_sla_seconds=30 * DAY),
        ], now=NOW)
        assert prov.status == FRESH
        assert prov.stale_tables == () and prov.unknown_tables == ()

    def test_no_tables_yields_an_unknown_provenance_with_nothing_to_say(self):
        prov = summarize([], now=NOW)
        assert prov.status == UNKNOWN
        assert prov.as_of is None
        assert describe(prov) == ""

    def test_owners_and_lineage_are_collected_and_deduplicated(self):
        prov = summarize([
            _table("orders", owner="finance", upstream=("kafka:orders.cdc",)),
            _table("returns", owner="finance"),
            _table("regions", owner="ops", upstream=("erp:GEO",)),
        ], now=NOW)
        assert prov.owners == ("finance", "ops")
        assert prov.lineage == {"public.orders": ["kafka:orders.cdc"], "public.regions": ["erp:GEO"]}


# ── the line the model actually reads ──────────────────────────────────────

class TestDescribe:
    def test_states_the_as_of_and_the_age(self):
        line = describe(summarize([
            _table(data_as_of=NOW - timedelta(hours=3), freshness_sla_seconds=DAY),
        ], now=NOW))
        assert "data as of 2026-08-28T09:00:00+00:00" in line
        assert "3.0h old" in line

    def test_stale_is_named_as_stale_with_the_offending_tables(self):
        line = describe(summarize([
            _table("orders", data_as_of=NOW - timedelta(days=4), freshness_sla_seconds=DAY),
        ], now=NOW))
        assert "STALE" in line
        assert "public.orders" in line

    def test_unmeasured_says_so_rather_than_going_quiet(self):
        line = describe(summarize([_table("orders")], now=NOW))
        assert "UNKNOWN" in line
        assert "never measured" in line

    def test_renders_from_the_dict_form_that_actually_travels(self):
        """Provenance is computed where the SQL runs and read again in the
        responder, crossing the agent state as plain JSON in between."""
        prov = summarize([
            _table("orders", owner="finance", classification="confidential",
                   upstream=("kafka:orders.cdc",),
                   data_as_of=NOW - timedelta(days=4), freshness_sla_seconds=DAY),
        ], now=NOW)
        assert describe(prov.to_dict()) == describe(prov)
        line = describe(prov.to_dict())
        assert "classification=confidential" in line
        assert "owner=finance" in line
        assert "lineage: public.orders <- kafka:orders.cdc" in line

    @pytest.mark.parametrize("value", [None, {}, {"tables": []}, "stale", 42, []])
    def test_nothing_to_say_renders_nothing(self, value):
        """Including shapes this version does not recognise. This runs inside
        the responder, on an answer that has already been computed — raising
        here would fail the turn over a disclosure."""
        assert describe(value) == ""

    def test_a_dict_from_an_older_schema_does_not_raise(self):
        """Provenance rides in agent state, which can be replayed from a run
        written by a previous deploy."""
        assert describe({"tables": [{"table": "orders"}]}) != ""


# ── the runtime read path ──────────────────────────────────────────────────

class TestProvenanceLookup:
    def test_qualified_references_match_on_the_bare_table_name(self, runtime_path):
        import data_provenance
        assert set(data_provenance._bare_names(["public.orders", "db.public.regions", "items"])) == {
            "orders", "regions", "items",
        }

    async def test_a_connection_not_linked_to_a_crawled_warehouse_yields_nothing(self, runtime_path):
        """Freshness read off the wrong warehouse is worse than none."""
        import data_provenance
        assert await data_provenance.provenance_for_sql(TENANT, {"host": "h"}, "SELECT * FROM orders") is None

    async def test_sql_naming_no_tables_yields_nothing(self, runtime_path):
        import data_provenance
        assert await data_provenance.provenance_for_sql(
            TENANT, {"catalog_warehouse_id": WAREHOUSE}, "SELECT 1",
        ) is None

    async def test_a_catalog_outage_degrades_the_disclosure_not_the_answer(self, runtime_path, monkeypatch):
        import data_provenance
        monkeypatch.setattr(
            data_provenance, "governance_for_tables",
            AsyncMock(side_effect=RuntimeError("postgres down")),
        )
        with pytest.raises(RuntimeError):
            # governance_for_tables swallows its own errors in production; this
            # asserts the caller does not add a second, silent swallow that
            # would hide a genuine bug in the lookup itself.
            await data_provenance.provenance_for_sql(
                TENANT, {"catalog_warehouse_id": WAREHOUSE}, "SELECT * FROM orders",
            )

    async def test_missing_arguments_short_circuit_before_any_database_call(self, runtime_path):
        import data_provenance
        assert await data_provenance.governance_for_tables("", WAREHOUSE, ["orders"]) == []
        assert await data_provenance.governance_for_tables(TENANT, "", ["orders"]) == []
        assert await data_provenance.governance_for_tables(TENANT, WAREHOUSE, []) == []

    async def test_provenance_is_summarised_from_the_catalog_rows(self, runtime_path, monkeypatch):
        import data_provenance
        monkeypatch.setattr(data_provenance, "governance_for_tables", AsyncMock(return_value=[
            _table("orders", data_as_of=NOW - timedelta(days=3), freshness_sla_seconds=DAY),
        ]))

        prov = await data_provenance.provenance_for_sql(
            TENANT, {"catalog_warehouse_id": WAREHOUSE}, "SELECT * FROM public.orders",
        )

        assert prov["status"] == STALE
        assert prov["stale_tables"] == ["public.orders"]


# ── attaching it to the result ─────────────────────────────────────────────

class TestHandlerAttachesProvenance:
    def _data(self, runtime_path):
        import nodes.sandbox.tool_executor.handlers.data as data
        return data

    async def test_a_successful_query_carries_the_provenance_of_its_tables(self, runtime_path, monkeypatch):
        data = self._data(runtime_path)
        stub = types.ModuleType("data_provenance")
        stub.provenance_for_sql = AsyncMock(return_value={"status": STALE, "tables": [{"table": "public.orders"}]})
        monkeypatch.setitem(sys.modules, "services.workflow_runtime.domain.data_provenance", stub)
        monkeypatch.setattr(data, "_sandbox_sql", AsyncMock(return_value=([{"n": 1}], None, False, None)))

        result, error = await data._run_one_query(
            "Sales", "SELECT * FROM orders", {"catalog_warehouse_id": WAREHOUSE},
            "q", "schema", "model", audit_ctx={"tenant_id": TENANT},
        )

        assert error is None
        assert result["metadata"]["provenance"]["status"] == STALE

    async def test_provenance_describes_the_statement_that_actually_ran(self, runtime_path, monkeypatch):
        """A self-corrected retry can end up querying different tables than the
        first attempt did."""
        data = self._data(runtime_path)
        stub = types.ModuleType("data_provenance")
        stub.provenance_for_sql = AsyncMock(return_value=None)
        monkeypatch.setitem(sys.modules, "services.workflow_runtime.domain.data_provenance", stub)
        monkeypatch.setattr(data, "_sandbox_sql", AsyncMock(side_effect=[
            (None, "column does not exist", False, None),
            ([{"n": 1}], None, False, None),
        ]))
        completion = AsyncMock()
        completion.return_value = types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="SELECT * FROM orders_v2"))]
        )
        monkeypatch.setattr(data.litellm, "acompletion", completion)

        await data._run_one_query(
            "Sales", "SELECT * FROM orders", {"catalog_warehouse_id": WAREHOUSE},
            "q", "schema", "model", audit_ctx={"tenant_id": TENANT},
        )

        assert stub.provenance_for_sql.await_args.args[2] == "SELECT * FROM orders_v2"

    async def test_a_provenance_failure_leaves_the_answer_intact(self, runtime_path, monkeypatch):
        """The query has already succeeded by the time this runs."""
        data = self._data(runtime_path)
        stub = types.ModuleType("data_provenance")
        stub.provenance_for_sql = AsyncMock(side_effect=RuntimeError("catalog down"))
        monkeypatch.setitem(sys.modules, "services.workflow_runtime.domain.data_provenance", stub)
        monkeypatch.setattr(data, "_sandbox_sql", AsyncMock(return_value=([{"n": 1}], None, False, None)))

        result, error = await data._run_one_query(
            "Sales", "SELECT 1 FROM orders", {"catalog_warehouse_id": WAREHOUSE},
            "q", "schema", "model", audit_ctx={"tenant_id": TENANT},
        )

        assert error is None
        assert result["metadata"]["provenance"] is None

    async def test_no_tenant_means_no_lookup(self, runtime_path, monkeypatch):
        data = self._data(runtime_path)
        stub = types.ModuleType("data_provenance")
        stub.provenance_for_sql = AsyncMock()
        monkeypatch.setitem(sys.modules, "services.workflow_runtime.domain.data_provenance", stub)

        assert await data._provenance({"catalog_warehouse_id": WAREHOUSE}, "SELECT 1", audit_ctx={}) is None
        stub.provenance_for_sql.assert_not_awaited()


# ── the catalog block the SQL model sees ───────────────────────────────────

class TestCatalogContext:
    def test_an_as_of_reaches_the_sql_generation_prompt(self, runtime_path):
        """A table whose data stops on the 20th changes which date range a
        sensible query asks for, and the model cannot infer that from DDL."""
        import schema_catalog
        block = schema_catalog.format_catalog_context([{
            "schema_name": "public", "table_name": "orders", "columns": ["id int"],
            "text_description": "Sales orders", "data_as_of": "2026-08-20T00:00:00+00:00",
            "freshness_column": "created_at", "owner": "finance",
            "classification": "confidential", "upstream": ["kafka:orders.cdc"],
        }])
        assert "Data as of 2026-08-20T00:00:00+00:00" in block
        assert "business date column: created_at" in block
        assert "classification=confidential" in block
        assert "upstream: kafka:orders.cdc" in block

    def test_a_table_with_no_governance_renders_exactly_as_before(self, runtime_path):
        """Governance is additive — an uncrawled warehouse's prompt must not
        gain a stray empty comment line."""
        import schema_catalog
        payload = {"schema_name": "public", "table_name": "orders",
                   "columns": ["id int"], "text_description": "Sales orders"}
        assert schema_catalog.format_catalog_context([payload]) == (
            "-- Sales orders\nTABLE public.orders (\n  id int\n)"
        )


# ── the answer ─────────────────────────────────────────────────────────────

class TestResponderDisclosure:
    def _state(self, items):
        # A query result is a ResultSet (result_sets channel, R0-1); anything
        # else is a ranked text chunk.
        return {
            "config": {"system_prompt": "You are helpful.", "context_max_chunks": 5,
                       "context_max_chars": 4000, "responder_model": "m"},
            "query": "doanh thu tháng 6",
            "messages": [],
            "result_sets": [i for i in items if "result_id" in i],
            "reranked_chunks": [i for i in items if "result_id" not in i],
        }

    def _chunk(self, provenance=None, rows=({"total": 42},)):
        from services.workflow_runtime.application import result_sets
        return result_sets.build(list(rows), connection_id="dw", engine="postgres", generated_by="llm",
                                 sql="SELECT SUM(amount) AS total FROM orders", governance=provenance)

    async def _run(self, monkeypatch, runtime_path, chunks):
        import nodes.model_harness.responder as responder

        captured = {}

        async def fake_completion(**kwargs):
            captured["messages"] = kwargs["messages"]
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="Doanh thu là 42."))],
                usage=None,
            )

        monkeypatch.setattr(responder.litellm, "acompletion", fake_completion)
        from services.workflow_runtime.application.runtime_services import llm_call
        monkeypatch.setattr(llm_call, "get_langfuse_client", lambda: None)
        out = await responder.responder_node(self._state(chunks))
        return captured["messages"][0]["content"], out

    async def test_a_stale_result_puts_the_warning_in_front_of_the_model(self, runtime_path, monkeypatch):
        prov = summarize([
            _table("orders", data_as_of=NOW - timedelta(days=4), freshness_sla_seconds=DAY),
        ], now=NOW).to_dict()

        system_prompt, _ = await self._run(monkeypatch, runtime_path, [self._chunk(prov)])

        assert "governance: " in system_prompt
        assert "STALE" in system_prompt
        assert "past its agreed refresh window" in system_prompt

    async def test_the_disclosure_rule_is_absent_when_nothing_carries_governance(self, runtime_path, monkeypatch):
        """Instructing the model to state an 'as of' it has not been given
        produces an invented date — worse than the silence it replaces."""
        system_prompt, _ = await self._run(monkeypatch, runtime_path, [self._chunk(None)])

        assert "governance: " not in system_prompt
        assert "Data freshness" not in system_prompt

    async def test_a_kb_chunk_without_metadata_does_not_break_rendering(self, runtime_path, monkeypatch):
        chunk = {"id": "kb_1", "source": "kb/doc", "content": "text", "rrf_sources": ["kb"]}
        system_prompt, _ = await self._run(monkeypatch, runtime_path, [chunk])
        assert "governance: " not in system_prompt

    async def test_the_citation_carries_provenance_for_the_ui(self, runtime_path, monkeypatch):
        prov = summarize([_table("orders", data_as_of=NOW, freshness_sla_seconds=DAY)], now=NOW).to_dict()
        import nodes.model_harness.responder as responder

        async def fake_completion(**kwargs):
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="Kết quả [1]."))],
                usage=None,
            )

        monkeypatch.setattr(responder.litellm, "acompletion", fake_completion)
        from services.workflow_runtime.application.runtime_services import llm_call
        monkeypatch.setattr(llm_call, "get_langfuse_client", lambda: None)

        out = await responder.responder_node(self._state([self._chunk(prov)]))

        assert out["citations"][0]["provenance"]["status"] == FRESH

    async def test_a_long_result_cannot_truncate_the_as_of_away(self, runtime_path, monkeypatch):
        """The governance line is not counted against the context budget — an
        accurate 'as of' being the thing trimmed by a large result set defeats
        the point of having one."""
        prov = summarize([
            _table("orders", data_as_of=NOW - timedelta(days=9), freshness_sla_seconds=DAY),
        ], now=NOW).to_dict()
        chunk = self._chunk(prov, rows=[{"note": "x" * 1_000} for _ in range(50)])

        system_prompt, _ = await self._run(monkeypatch, runtime_path, [chunk])

        assert "STALE" in system_prompt


class TestProvenanceIsNotTheSemanticProvenance:
    """Two different questions, deliberately two different keys: `semantic` says
    how the number was DEFINED, `provenance` says how current the data behind it
    is. An approved contract compiled against a table that stopped loading last
    week is still exactly wrong."""

    def test_the_dataclass_round_trips_through_json_shaped_dicts(self):
        prov = summarize([
            _table("orders", owner="finance", data_as_of=NOW, freshness_sla_seconds=DAY),
        ], now=NOW)
        as_dict = prov.to_dict()
        assert as_dict["as_of"] == NOW.isoformat()
        assert as_dict["tables"][0]["table"] == "public.orders"
        assert as_dict["tables"][0]["freshness_sla_seconds"] == DAY

    def test_an_empty_provenance_is_falsy_enough_to_skip(self):
        assert describe(Provenance()) == ""
        assert freshness.summarize([]).to_dict()["tables"] == []
