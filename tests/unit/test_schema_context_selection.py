"""Which schema reaches the SQL-generation prompt
(services/workflow-runtime/nodes/sandbox/tool_executor/handlers/data.py).

Two defects are pinned here:

  * the curated Schema Catalog (schema_catalog.py — embedded, tenant-filtered,
    carrying an LLM-written business description and join hints per table) was
    built and then never read, so
  * every SQL generation re-introspected information_schema and rendered EVERY
    table of EVERY column into the prompt, unbounded.

The catalog is now tried first, and the introspection fallback is ranked
against the question and capped. The properties worth regressing on are the
ones an eyeball test would miss: that the cap actually binds on a large schema,
that omission is disclosed to the model, and that catalog entries can never be
served for a database they do not describe.
"""
from __future__ import annotations

import json
import pathlib
import sys
import types
from unittest.mock import AsyncMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "services" / "workflow-runtime")

pytestmark = pytest.mark.unit

TENANT = "a0000000-0000-0000-0000-000000000001"


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


def _stub_redis(monkeypatch, get_return=None):
    mod = types.ModuleType("redis_cache")
    mod.cache_get_schema = AsyncMock(return_value=get_return)
    mod.cache_set_schema = AsyncMock(return_value=None)
    monkeypatch.setitem(sys.modules, "redis_cache", mod)
    return mod


def _stub_catalog(monkeypatch, hits):
    mod = types.ModuleType("schema_catalog")
    mod.retrieve_schema_catalog = AsyncMock(return_value=hits)
    mod.format_catalog_context = lambda tables: "\n".join(
        f"-- {t.get('text_description', '')}\nTABLE {t.get('table_name')}" for t in tables
    )
    monkeypatch.setitem(sys.modules, "schema_catalog", mod)
    return mod


def _rows(*specs):
    """(table, column, type) triples as information_schema would return them."""
    return [{"table_name": t, "column_name": c, "data_type": d} for t, c, d in specs]


def _table_rows(*names):
    """Phase-1 result: one row per table name."""
    return [{"table_name": n} for n in names]


def _two_phase(monkeypatch, data, table_names, column_specs):
    """Stub both introspection phases and return the mock, so a test can assert
    on which tables phase 2 was actually asked for."""
    sandbox = AsyncMock(side_effect=[
        (_table_rows(*table_names), None, False, None),
        (_rows(*column_specs), None, False, None),
    ])
    monkeypatch.setattr(data, "_sandbox_sql", sandbox)
    return sandbox


class TestRanking:
    def test_table_named_in_the_question_comes_first(self):
        data = _data()
        tables = {"audit_log": ["id int"], "orders": ["id int"], "employees": ["id int"]}

        assert data._rank_tables(tables, "tổng doanh thu orders tháng 6")[0] == "orders"

    def test_a_column_match_promotes_a_table_whose_name_does_not_match(self):
        """"revenue" names no table, but `orders.revenue_amount` is the answer."""
        data = _data()
        tables = {
            "employees": ["id int", "name text"],
            "orders": ["id int", "revenue_amount numeric"],
        }

        assert data._rank_tables(tables, "total revenue")[0] == "orders"

    def test_table_name_beats_column_name(self):
        data = _data()
        tables = {"orders": ["id int"], "shipments": ["orders_ref int"]}

        assert data._rank_tables(tables, "orders")[0] == "orders"

    def test_unrelated_question_keeps_the_original_order(self):
        """No signal must mean stable output, not an arbitrary reshuffle."""
        data = _data()
        tables = {"alpha": ["id int"], "beta": ["id int"], "gamma": ["id int"]}

        assert data._rank_tables(tables, "zzzz qqqq") == ["alpha", "beta", "gamma"]

    def test_empty_query_keeps_the_original_order(self):
        data = _data()
        tables = {"alpha": ["id int"], "beta": ["id int"]}

        assert data._rank_tables(tables, "") == ["alpha", "beta"]

    def test_snake_case_is_split_into_tokens(self):
        data = _data()
        tables = {"misc": ["id int"], "don_hang": ["id int"]}

        assert data._rank_tables(tables, "bao nhiêu don hang")[0] == "don_hang"


class TestBounding:
    def test_large_schema_is_capped_at_the_table_limit(self):
        """The actual defect: a warehouse-sized schema went into the prompt whole."""
        data = _data()
        tables = {f"table_{i:03d}": ["id int", "value numeric"] for i in range(200)}

        rendered = data._render_tables(tables, "value")

        table_lines = [ln for ln in rendered.splitlines() if not ln.startswith("--")]
        assert len(table_lines) == data._MAX_SCHEMA_TABLES

    def test_omission_is_disclosed_to_the_model(self):
        """Without this the model cannot tell "no such table" from "not shown",
        and will state the data does not exist."""
        data = _data()
        tables = {f"t{i}": ["id int"] for i in range(50)}

        rendered = data._render_tables(tables, "anything")

        assert "not shown" in rendered
        assert "38 more table(s)" in rendered  # 50 - 12

    def test_char_budget_binds_before_the_table_count_on_wide_tables(self):
        data = _data()
        wide = [f"column_{i} character varying" for i in range(400)]
        tables = {f"t{i}": list(wide) for i in range(10)}

        rendered = data._render_tables(tables, "column")

        assert len(rendered) < data._MAX_SCHEMA_CHARS * 1.5
        assert "not shown" in rendered

    def test_one_very_wide_table_cannot_blow_the_budget_on_its_own(self):
        """The first rendered line is always emitted (dropping it would leave an
        empty schema block), so without a per-table column cap a single
        400-column fact table lands in the prompt whole."""
        data = _data()
        tables = {"fct_sales": [f"column_{i} character varying" for i in range(400)]}

        rendered = data._render_tables(tables, "sales")

        assert len(rendered) < data._MAX_SCHEMA_CHARS
        assert "more column(s)" in rendered

    def test_truncated_columns_keep_the_ones_the_question_names(self):
        """Cutting to the first 40 alphabetically would drop exactly the column
        the query is about."""
        data = _data()
        columns = [f"column_{i} int" for i in range(100)] + ["revenue_amount numeric"]
        tables = {"orders": columns}

        rendered = data._render_tables(tables, "revenue")

        assert "revenue_amount numeric" in rendered

    def test_column_omission_count_is_disclosed(self):
        data = _data()
        tables = {"t": [f"c{i} int" for i in range(60)]}

        rendered = data._render_tables(tables, "")

        assert f"+{60 - data._MAX_COLUMNS_PER_TABLE} more column(s)" in rendered

    def test_small_schema_is_rendered_whole_with_no_disclosure(self):
        data = _data()
        tables = {"orders": ["id int", "total numeric"], "customers": ["id int"]}

        rendered = data._render_tables(tables, "orders")

        assert "not shown" not in rendered
        assert "orders(id int, total numeric)" in rendered
        assert "customers(id int)" in rendered


class TestCatalogIsPreferred:
    async def test_catalog_is_used_when_the_connection_names_a_warehouse(self, monkeypatch):
        data = _data()
        catalog = _stub_catalog(monkeypatch, [
            {"table_name": "FCT_REVENUE", "text_description": "Revenue fact table"},
        ])
        sandbox = AsyncMock()
        monkeypatch.setattr(data, "_sandbox_sql", sandbox)

        text = await data._get_schema_text(
            TENANT,
            {"host": "h", "database": "d", "catalog_warehouse_id": "wh-1"},
            "doanh thu",
        )

        assert "Revenue fact table" in text
        sandbox.assert_not_awaited(), "catalog hit must not also introspect"
        _, kwargs = catalog.retrieve_schema_catalog.call_args
        assert kwargs["warehouse_id"] == "wh-1"

    async def test_catalog_is_scoped_to_one_warehouse_not_the_whole_tenant(self, monkeypatch):
        """A tenant with several crawled warehouses must not be handed tables
        from a database this connection cannot query."""
        data = _data()
        catalog = _stub_catalog(monkeypatch, [{"table_name": "T", "text_description": "d"}])
        monkeypatch.setattr(data, "_sandbox_sql", AsyncMock())

        await data._get_schema_text(
            TENANT, {"host": "h", "database": "d", "catalog_warehouse_id": "wh-2"}, "q",
        )

        _, kwargs = catalog.retrieve_schema_catalog.call_args
        assert kwargs["warehouse_id"] == "wh-2"

    async def test_no_warehouse_id_means_the_catalog_is_not_consulted(self, monkeypatch):
        """Guessing which warehouse an unlinked connection maps to would feed the
        model another database's tables."""
        data = _data()
        catalog = _stub_catalog(monkeypatch, [{"table_name": "T", "text_description": "d"}])
        _stub_redis(monkeypatch, get_return=None)
        monkeypatch.setattr(data, "_sandbox_sql", AsyncMock(return_value=(_rows(("orders", "id", "int")), None, False, None)))

        text = await data._get_schema_text(TENANT, {"host": "h", "database": "d"}, "q")

        catalog.retrieve_schema_catalog.assert_not_called()
        assert "orders(id int)" in text

    async def test_empty_catalog_falls_back_to_introspection(self, monkeypatch):
        data = _data()
        _stub_catalog(monkeypatch, [])
        _stub_redis(monkeypatch, get_return=None)
        monkeypatch.setattr(data, "_sandbox_sql", AsyncMock(return_value=(_rows(("orders", "id", "int")), None, False, None)))

        text = await data._get_schema_text(
            TENANT, {"host": "h", "database": "d", "catalog_warehouse_id": "wh-1"}, "q",
        )

        assert "orders(id int)" in text

    async def test_catalog_failure_falls_back_instead_of_failing_the_turn(self, monkeypatch):
        data = _data()
        mod = types.ModuleType("schema_catalog")
        mod.retrieve_schema_catalog = AsyncMock(side_effect=RuntimeError("qdrant down"))
        mod.format_catalog_context = lambda t: ""
        monkeypatch.setitem(sys.modules, "schema_catalog", mod)
        _stub_redis(monkeypatch, get_return=None)
        monkeypatch.setattr(data, "_sandbox_sql", AsyncMock(return_value=(_rows(("orders", "id", "int")), None, False, None)))

        text = await data._get_schema_text(
            TENANT, {"host": "h", "database": "d", "catalog_warehouse_id": "wh-1"}, "q",
        )

        assert "orders(id int)" in text


class TestTwoPhaseIntrospection:
    """Columns used to be fetched for EVERY table in one statement, which hits
    sandbox-executor's MAX_ROWS=10_000 cap at roughly 500 tables. The result came
    back as an alphabetical prefix with `is_truncated` set, and `_sandbox_sql`
    logs that flag without returning it — so on a large warehouse the tail simply
    vanished and the agent concluded those tables did not exist.

    Ranking on names first and fetching columns only for the survivors makes the
    fetch bounded by construction instead."""

    async def test_only_the_table_list_is_cached(self, monkeypatch):
        """The list changes at DDL speed. Columns are not cached: which tables
        are asked about varies per question, so a per-subset cache either
        fragments or drifts back toward holding the whole schema."""
        data = _data()
        redis = _stub_redis(monkeypatch, get_return=None)
        _two_phase(monkeypatch, data, ["orders", "employees"], [("orders", "id", "int")])

        await data._get_schema_text(TENANT, {"host": "h", "port": 5432, "database": "d"}, "orders")

        cached = redis.cache_set_schema.await_args.args[4]
        assert json.loads(cached) == {"tables": ["orders", "employees"]}

    async def test_column_fetch_is_capped_regardless_of_warehouse_size(self, monkeypatch):
        """The actual defect: the wire payload used to scale with the warehouse."""
        data = _data()
        _stub_redis(monkeypatch, get_return=None)
        sandbox = _two_phase(
            monkeypatch, data,
            [f"table_{i:04d}" for i in range(900)],
            [("table_0001", "id", "int")],
        )

        await data._get_schema_text(TENANT, {"host": "h", "database": "d"}, "table_0001")

        columns_sql = sandbox.await_args_list[1].args[0]
        # Count the table literals specifically — the statement also carries the
        # quoted schema name, which is not a table.
        assert columns_sql.count("'table_") == data._COLUMN_FETCH_TABLES

    async def test_phase_two_asks_only_for_relevant_tables(self, monkeypatch):
        data = _data()
        _stub_redis(monkeypatch, get_return=None)
        sandbox = _two_phase(
            monkeypatch, data,
            ["orders", "employees", "audit_log"],
            [("orders", "id", "int")],
        )

        await data._get_schema_text(TENANT, {"host": "h", "database": "d"}, "orders revenue")

        columns_sql = sandbox.await_args_list[1].args[0]
        assert "'orders'" in columns_sql

    async def test_phase_one_lists_tables_not_columns(self, monkeypatch):
        """A names-only query is one row per table, which puts the row cap out of
        reach for any realistic warehouse."""
        data = _data()
        _stub_redis(monkeypatch, get_return=None)
        sandbox = _two_phase(monkeypatch, data, ["orders"], [("orders", "id", "int")])

        await data._get_schema_text(TENANT, {"host": "h", "database": "d"}, "orders")

        tables_sql = sandbox.await_args_list[0].args[0]
        assert "information_schema.tables" in tables_sql
        assert "information_schema.columns" not in tables_sql

    async def test_disclosure_quotes_the_real_warehouse_size(self, monkeypatch):
        """Saying "3 more not shown" on a 900-table warehouse understates the gap
        badly enough to change how the model answers."""
        data = _data()
        _stub_redis(monkeypatch, get_return=None)
        _two_phase(
            monkeypatch, data,
            [f"t{i}" for i in range(900)],
            [("t1", "id", "int")],
        )

        rendered = await data._get_schema_text(TENANT, {"host": "h", "database": "d"}, "t1")

        assert "899 more table(s)" in rendered

    async def test_a_cached_list_still_fetches_columns(self, monkeypatch):
        """The list is cached; the columns for this question are not."""
        data = _data()
        _stub_redis(monkeypatch, get_return=json.dumps({"tables": ["orders", "employees"]}))
        sandbox = AsyncMock(return_value=(_rows(("orders", "id", "int")), None, False, None))
        monkeypatch.setattr(data, "_sandbox_sql", sandbox)

        rendered = await data._get_schema_text(TENANT, {"host": "h", "database": "d"}, "orders")

        assert sandbox.await_count == 1
        assert "information_schema.columns" in sandbox.await_args.args[0]
        assert "orders(id int)" in rendered

    async def test_a_stale_cache_shape_is_discarded_not_fatal(self, monkeypatch):
        """Entries written by the previous whole-schema shape must be re-fetched,
        not misread as a table list."""
        data = _data()
        _stub_redis(monkeypatch, get_return=json.dumps({"orders": ["id int"]}))
        _two_phase(monkeypatch, data, ["orders"], [("orders", "id", "int")])

        rendered = await data._get_schema_text(TENANT, {"host": "h", "database": "d"}, "orders")

        assert "orders(id int)" in rendered

    async def test_unparseable_cache_is_discarded_not_fatal(self, monkeypatch):
        data = _data()
        _stub_redis(monkeypatch, get_return="orders(id int)")
        _two_phase(monkeypatch, data, ["orders"], [("orders", "id", "int")])

        rendered = await data._get_schema_text(TENANT, {"host": "h", "database": "d"}, "orders")

        assert "orders(id int)" in rendered

    async def test_table_listing_failure_degrades_to_empty(self, monkeypatch):
        data = _data()
        _stub_redis(monkeypatch, get_return=None)
        monkeypatch.setattr(data, "_sandbox_sql", AsyncMock(
            return_value=(None, "sandbox-executor unreachable", False, None),
        ))

        assert await data._get_schema_text(TENANT, {"host": "h", "database": "d"}, "q") == ""

    async def test_column_fetch_failure_degrades_to_empty(self, monkeypatch):
        """Phase 1 succeeding and phase 2 failing must not render a table list
        with no columns — that reads as "these tables have no columns"."""
        data = _data()
        _stub_redis(monkeypatch, get_return=None)
        monkeypatch.setattr(data, "_sandbox_sql", AsyncMock(side_effect=[
            (_table_rows("orders"), None, False, None),
            (None, "timeout", False, None),
        ]))

        assert await data._get_schema_text(TENANT, {"host": "h", "database": "d"}, "orders") == ""


class TestTableNameQuoting:
    """Phase 2 interpolates names into an IN list. They come from the database's
    own catalog rather than the question, but this is the one place in the module
    where a value reaches a statement without passing through the metric
    compiler, so it does not get an exception."""

    def test_plain_identifiers_are_quoted(self):
        data = _data()

        assert data._quote_table_names(["orders", "line_items"]) == "'orders', 'line_items'"

    def test_names_that_are_not_identifiers_are_dropped(self):
        data = _data()

        out = data._quote_table_names(["orders", "weird-name", "has space", "drop';--"])

        assert out == "'orders'"

    def test_an_all_dropped_list_yields_nothing_rather_than_empty_in(self):
        """`IN ()` is a syntax error; returning "" lets the caller skip the
        query entirely."""
        data = _data()

        assert data._quote_table_names(["weird-name"]) == ""


class TestNameOnlyRanking:
    def test_a_named_table_comes_first(self):
        data = _data()

        ranked = data._rank_table_names(["audit_log", "orders", "employees"], "doanh thu orders")

        assert ranked[0] == "orders"

    def test_an_unrelated_question_preserves_order(self):
        """No signal must mean stable output, not an arbitrary reshuffle — the
        candidate set feeding phase 2 would otherwise vary between identical
        turns."""
        data = _data()
        names = ["alpha", "beta", "gamma"]

        assert data._rank_table_names(names, "zzz qqq") == names

    def test_empty_query_preserves_order(self):
        data = _data()

        assert data._rank_table_names(["a", "b"], "") == ["a", "b"]
