"""Declared governance policy and the freshness watermark, on the crawler side.

The schema crawler (platform_workers/workers/schema_crawler) is where owner,
classification, upstream and the freshness watermark are written; the runtime
side that reads them back into an answer is in
tests/unit/workflow_runtime/test_data_governance.py, which states the
invariants both halves hold to. Split out so each half runs in the image of the
service it tests.
"""
from __future__ import annotations

import pathlib
import sys
import types
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from .._loader import stub_if_missing

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_CRAWLER_DIR = str(REPO_ROOT / "backend" / "services" / "platform_workers")

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
TENANT = "a0000000-0000-0000-0000-000000000001"
WAREHOUSE = "b0000000-0000-0000-0000-000000000002"

HOUR = 3600
DAY = 86400


@pytest.fixture
def crawler_path(monkeypatch):
    stub_if_missing(
        monkeypatch,
        ("kafka", {"KafkaConsumer": MagicMock}),
        ("kafka.errors", {"NoBrokersAvailable": type("NoBrokersAvailable", (Exception,), {})}),
        ("qdrant_client", {"QdrantClient": MagicMock, "AsyncQdrantClient": MagicMock}),
        ("qdrant_client.models", {
            "Distance": MagicMock(), "VectorParams": MagicMock, "PointStruct": MagicMock,
        }),
        ("psycopg2", {"OperationalError": type("OperationalError", (Exception,), {}),
                      "InterfaceError": type("InterfaceError", (Exception,), {}),
                      "connect": MagicMock()}),
        ("psycopg2.extras", {"RealDictCursor": MagicMock}),
    )
    monkeypatch.syspath_prepend(_CRAWLER_DIR)
    yield


# ── declared policy (crawler) ──────────────────────────────────────────────

class TestTablePolicy:
    def _policy(self, config, schema="public", table="orders"):
        from workers.schema_crawler import governance
        return governance.table_policy(config, schema, table)

    def test_defaults_apply_and_the_table_entry_overrides_them(self, crawler_path):
        policy = self._policy({"governance": {
            "defaults": {"owner": "data-platform", "classification": "internal"},
            "freshness_sla_seconds": DAY,
            "tables": {"public.orders": {"owner": "finance", "freshness_sla_seconds": HOUR}},
        }})
        assert policy["owner"] == "finance"
        assert policy["classification"] == "internal"
        assert policy["freshness_sla_seconds"] == HOUR

    def test_lookup_is_case_insensitive_and_prefers_the_qualified_key(self, crawler_path):
        config = {"governance": {"tables": {
            "ORDERS": {"owner": "wrong"},
            "Public.Orders": {"owner": "right"},
        }}}
        assert self._policy(config)["owner"] == "right"

    def test_falls_back_to_the_bare_table_name(self, crawler_path):
        config = {"governance": {"tables": {"orders": {"owner": "finance"}}}}
        assert self._policy(config)["owner"] == "finance"

    def test_freshness_column_is_never_inherited_from_defaults(self, crawler_path):
        """It names a column that must exist on THIS table. A default would aim
        the watermark query at every table in the warehouse looking for a column
        most of them do not have."""
        policy = self._policy({"governance": {
            "defaults": {"freshness_column": "created_at"},
            "tables": {},
        }})
        assert policy["freshness_column"] == ""

    @pytest.mark.parametrize("sla", [0, -1, "soon", None, True])
    def test_an_unusable_sla_is_dropped_rather_than_clamped(self, crawler_path, sla):
        """An SLA of 0 marks every table stale forever, which trains readers to
        ignore the warning — the one failure mode that makes it worthless."""
        config = {"governance": {"tables": {"orders": {"freshness_sla_seconds": sla}}}}
        assert self._policy(config)["freshness_sla_seconds"] is None

    def test_an_unknown_classification_is_dropped(self, crawler_path):
        config = {"governance": {"tables": {"orders": {"classification": "very-secret"}}}}
        assert self._policy(config)["classification"] == ""

    def test_unrecognised_keys_are_ignored(self, crawler_path):
        config = {"governance": {"tables": {"orders": {"classifcation": "restricted", "owner": "finance"}}}}
        policy = self._policy(config)
        assert policy["owner"] == "finance"
        assert policy["classification"] == ""

    def test_upstream_must_be_a_list(self, crawler_path):
        config = {"governance": {"tables": {"orders": {"upstream": "kafka:orders"}}}}
        assert self._policy(config)["upstream"] == []

    @pytest.mark.parametrize("config", [{}, {"governance": None}, {"governance": "yes"}, {"governance": {"tables": []}}])
    def test_a_warehouse_with_no_declared_policy_yields_empty_not_an_error(self, crawler_path, config):
        policy = self._policy(config)
        assert policy["owner"] == "" and policy["freshness_sla_seconds"] is None


# ── the crawler's write path ───────────────────────────────────────────────

class TestCrawlerWrites:
    def test_freshness_column_resolves_to_the_warehouses_own_spelling(self, crawler_path):
        """Snowflake upper-cases what an operator wrote as `created_at`."""
        from workers.schema_crawler import worker as main
        columns = [{"name": "ID"}, {"name": "CREATED_AT"}]
        assert main.resolve_freshness_column("created_at", columns, "ORDERS") == "CREATED_AT"

    def test_a_declared_column_that_does_not_exist_is_caught_before_the_query(self, crawler_path):
        """Otherwise it is a failing MAX() against that table on every crawl,
        forever, with the table reported freshness-unknown either way."""
        from workers.schema_crawler import worker as main
        assert main.resolve_freshness_column("updated_at", [{"name": "id"}], "orders") == ""

    def test_no_declared_column_means_no_watermark_query(self, crawler_path):
        from workers.schema_crawler import worker as main
        assert main.resolve_freshness_column("", [{"name": "created_at"}], "orders") == ""

    def test_checked_at_is_only_stamped_when_a_watermark_was_obtained(self, crawler_path):
        """A checked_at with no value would read as 'measured, and it is null'
        rather than 'the measurement failed'."""
        from workers.schema_crawler import worker as main
        payload = main._governance_payload({"owner": "finance"}, None, NOW)
        assert payload["data_as_of"] is None
        assert payload["freshness_checked_at"] is None

        payload = main._governance_payload({"owner": "finance"}, NOW, NOW)
        assert payload["data_as_of"] == NOW.isoformat()
        assert payload["freshness_checked_at"] == NOW.isoformat()

    def test_unchanged_schema_still_writes_the_freshness_columns(self, crawler_path):
        """The regression that matters most. A table's DDL is stable for months
        while its data reloads nightly, so the schema-unchanged path is the
        normal one — writing the watermark only on the changed path would freeze
        `data_as_of` at whenever the table was last altered."""
        from workers.schema_crawler import worker as main

        meta = {"schema_name": "public", "table_name": "orders",
                "columns": [{"name": "id", "type": "int"}], "row_count": 10}
        unchanged_hash = main.schema_hash(meta["columns"])

        cur = MagicMock()
        cur.fetchone.return_value = ("row-id", unchanged_hash, "point-id")
        qdrant = MagicMock()
        policy = {"owner": "finance", "classification": "confidential",
                  "upstream": ["kafka:orders.cdc"], "freshness_column": "created_at",
                  "freshness_sla_seconds": DAY}

        changed = main.process_table(cur, qdrant, TENANT, WAREHOUSE, meta, policy, NOW, NOW)

        assert changed is False
        sql, params = cur.execute.call_args.args
        assert "data_as_of" in sql and "freshness_sla_seconds" in sql
        assert NOW in params and DAY in params
        # No re-describe, no re-embed: nothing the vector is built from moved.
        qdrant.upsert.assert_not_called()
        qdrant.set_payload.assert_called_once()
        assert qdrant.set_payload.call_args.kwargs["payload"]["data_as_of"] == NOW.isoformat()

    def test_a_qdrant_payload_refresh_failure_does_not_fail_the_crawl(self, crawler_path):
        from workers.schema_crawler import worker as main
        meta = {"schema_name": "public", "table_name": "orders",
                "columns": [{"name": "id", "type": "int"}], "row_count": 10}
        cur = MagicMock()
        cur.fetchone.return_value = ("row-id", main.schema_hash(meta["columns"]), "point-id")
        qdrant = MagicMock()
        qdrant.set_payload.side_effect = RuntimeError("qdrant down")

        assert main.process_table(cur, qdrant, TENANT, WAREHOUSE, meta, {}, NOW, NOW) is False


# ── the watermark query ────────────────────────────────────────────────────

class TestWatermark:
    def test_an_identifier_that_is_not_a_bare_name_is_refused(self, crawler_path):
        from workers.schema_crawler.identifiers import UnsafeIdentifier, safe_identifier
        for bad in ["orders; DROP TABLE t", "public.orders", "1_orders", "", "or ders", 'o"rders']:
            with pytest.raises(UnsafeIdentifier):
                safe_identifier(bad)
        assert safe_identifier("orders_2024") == "orders_2024"

    def test_a_rejected_identifier_returns_none_and_runs_no_query(self, crawler_path):
        """Freshness stays unknown; the crawl of everything else continues."""
        from workers.schema_crawler import metadata
        assert metadata.table_watermark("postgres", {}, "public", "orders; DROP", "created_at") is None
        assert metadata.table_watermark("postgres", {}, "public", "orders", "created_at; --") is None

    def test_a_driver_failure_returns_none_rather_than_raising(self, crawler_path, monkeypatch):
        """A watermark the crawler could not read must leave the table UNKNOWN.
        Returning now() would produce an 'as of' describing the crawl instead of
        the data."""
        from workers.schema_crawler import metadata

        stub = types.ModuleType("workers.schema_crawler.postgres_metadata")
        stub.max_timestamp = MagicMock(side_effect=RuntimeError("permission denied"))
        monkeypatch.setitem(sys.modules, "workers.schema_crawler.postgres_metadata", stub)

        assert metadata.table_watermark("postgres", {}, "public", "orders", "created_at") is None

    def test_an_unsupported_engine_returns_none(self, crawler_path):
        from workers.schema_crawler import metadata
        assert metadata.table_watermark("db2", {}, "public", "orders", "created_at") is None

    def test_quoting_is_engine_appropriate(self, crawler_path):
        from workers.schema_crawler.identifiers import backtick_identifier, quote_identifier
        assert quote_identifier("orders") == '"orders"'
        assert backtick_identifier("orders") == "`orders`"

    def test_a_warehouse_that_reports_no_schema_queries_the_bare_table(self, crawler_path, monkeypatch):
        """Qualifying with an empty schema produces `""."orders"` — a syntax
        error that would turn every watermark on such a warehouse into UNKNOWN
        while looking like a permissions problem in the log."""
        from workers.schema_crawler import postgres_metadata

        cursor = MagicMock()
        cursor.fetchone.return_value = (NOW,)
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        monkeypatch.setattr(postgres_metadata, "_connect", lambda params: conn)

        assert postgres_metadata.max_timestamp({}, "", "orders", "created_at") == NOW
        assert cursor.execute.call_args.args[0] == 'SELECT MAX("created_at") FROM "orders"'

        postgres_metadata.max_timestamp({}, "public", "orders", "created_at")
        assert cursor.execute.call_args.args[0] == 'SELECT MAX("created_at") FROM "public"."orders"'
