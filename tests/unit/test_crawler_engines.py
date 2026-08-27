"""Multi-engine schema discovery (services/schema-crawler-worker).

The crawler was hardcoded to Snowflake — `WHERE w.service = 'Snowflake'`, one
metadata module, one credential env var — while the DB Connector node, which
runs the agent's SQL, points at Postgres. So the curated, embedded,
tenant-filtered Schema Catalog described one set of databases and the agent
queried a different one, and every SQL generation fell back to dumping
information_schema into the prompt.

What matters most here is that all three engines return the SAME shape: the
crawl loop's change detection, the LLM description step and the Qdrant payload
are generic, and a per-engine deviation would corrupt the catalog rather than
fail loudly.
"""
from __future__ import annotations

import pathlib
import sys
import types
from unittest.mock import MagicMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "services" / "schema-crawler-worker")

pytestmark = pytest.mark.unit

# The expected keys of one table's metadata, shared by every engine.
_TABLE_KEYS = {"schema_name", "table_name", "columns", "row_count", "last_altered"}
_COLUMN_KEYS = {"name", "type", "nullable", "comment"}


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


def _metadata():
    import metadata
    return metadata


class TestEngineNormalisation:
    @pytest.mark.parametrize("service,expected", [
        ("Snowflake", "snowflake"),
        ("snowflake", "snowflake"),
        ("Postgres", "postgres"),
        ("PostgreSQL", "postgres"),
        ("postgresql", "postgres"),
        ("  PG  ", "postgres"),
        ("ClickHouse", "clickhouse"),
        ("click_house", "clickhouse"),
    ])
    def test_known_spellings_map_to_an_engine(self, service, expected):
        """`service` is a free string from the connect UI
        (`service=body.warehouse_type`), so it arrives spelled however a form
        chose."""
        assert _metadata().normalize_engine(service) == expected

    @pytest.mark.parametrize("service", ["MySQL", "bigquery", "", None, "  "])
    def test_unknown_services_are_skipped_not_guessed(self, service):
        """Crawling with the wrong driver puts confident nonsense in the
        catalog, which is worse than an uncrawled warehouse."""
        assert _metadata().normalize_engine(service) is None


class TestCredentialResolution:
    def test_per_warehouse_override_wins(self, monkeypatch):
        monkeypatch.setenv("WAREHOUSE_PASSWORD__abc_123", "specific")
        monkeypatch.setenv("POSTGRES_CRAWLER_PASSWORD", "shared")

        params = _metadata().connection_params(
            "postgres", {"connection": {"host": "h", "database": "d"}}, "abc-123",
        )

        assert params["password"] == "specific"

    def test_uuid_hyphens_become_underscores(self, monkeypatch):
        """Env names cannot contain "-", and warehouse_id is a UUID."""
        monkeypatch.setenv("WAREHOUSE_PASSWORD__a_b_c", "ok")

        params = _metadata().connection_params(
            "postgres", {"connection": {"host": "h", "database": "d"}}, "a-b-c",
        )

        assert params["password"] == "ok"

    def test_per_engine_shared_account_is_the_fallback(self, monkeypatch):
        monkeypatch.delenv("WAREHOUSE_PASSWORD__w1", raising=False)
        monkeypatch.setenv("CLICKHOUSE_CRAWLER_PASSWORD", "shared")

        params = _metadata().connection_params(
            "clickhouse", {"connection": {"host": "h"}}, "w1",
        )

        assert params["password"] == "shared"

    def test_the_legacy_snowflake_variables_still_work(self, monkeypatch):
        """A stack configured before other engines existed must keep crawling."""
        monkeypatch.delenv("WAREHOUSE_PASSWORD__w1", raising=False)
        monkeypatch.delenv("SNOWFLAKE_PASSWORD__w1", raising=False)
        monkeypatch.setenv("SNOWFLAKE_CRAWLER_PASSWORD", "legacy")

        params = _metadata().connection_params(
            "snowflake", {"connection": {"account": "a"}}, "w1",
        )

        assert params["password"] == "legacy"

    def test_no_password_skips_the_warehouse(self, monkeypatch):
        """One misconfigured warehouse must not stop every other tenant's
        catalog from refreshing."""
        for var in ("WAREHOUSE_PASSWORD__w1", "SNOWFLAKE_PASSWORD__w1",
                    "POSTGRES_CRAWLER_PASSWORD", "SNOWFLAKE_CRAWLER_PASSWORD"):
            monkeypatch.delenv(var, raising=False)

        assert _metadata().connection_params(
            "postgres", {"connection": {"host": "h", "database": "d"}}, "w1",
        ) is None

    def test_a_snowflake_shared_password_does_not_leak_to_other_engines(self, monkeypatch):
        """Different engines mean different accounts; falling back across them
        would send Snowflake credentials to a Postgres host."""
        for var in ("WAREHOUSE_PASSWORD__w1", "SNOWFLAKE_PASSWORD__w1", "POSTGRES_CRAWLER_PASSWORD"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("SNOWFLAKE_CRAWLER_PASSWORD", "snowflake-only")

        assert _metadata().connection_params(
            "postgres", {"connection": {"host": "h", "database": "d"}}, "w1",
        ) is None


class TestConnectionParams:
    @pytest.fixture(autouse=True)
    def _password(self, monkeypatch):
        monkeypatch.setenv("WAREHOUSE_PASSWORD__w1", "pw")

    def test_postgres_defaults_port_and_schema(self):
        params = _metadata().connection_params(
            "postgres", {"connection": {"host": "h", "database": "d", "username": "u"}}, "w1",
        )

        assert params["port"] == 5432
        assert params["schema"] == "public"
        assert params["user"] == "u"

    def test_clickhouse_defaults_port_and_database(self):
        params = _metadata().connection_params("clickhouse", {"connection": {"host": "h"}}, "w1")

        assert params["port"] == 8123
        assert params["database"] == "default"

    def test_snowflake_shape_is_unchanged(self):
        params = _metadata().connection_params(
            "snowflake",
            {"connection": {"account": "a", "username": "u", "warehouse": "wh",
                            "database": "db", "schema": "s", "role": "r"}},
            "w1",
        )

        assert params["account"] == "a"
        assert params["schema"] == "s"
        assert params["role"] == "r"

    @pytest.mark.parametrize("engine,conn", [
        ("postgres", {"host": "h"}),                 # no database
        ("postgres", {"database": "d"}),             # no host
        ("clickhouse", {}),                          # no host
        ("snowflake", {"user": "u"}),                # no account
    ])
    def test_an_incomplete_stored_connection_is_skipped(self, engine, conn):
        assert _metadata().connection_params(engine, {"connection": conn}, "w1") is None

    def test_an_unsupported_engine_yields_nothing(self):
        assert _metadata().connection_params("mysql", {"connection": {"host": "h"}}, "w1") is None


class TestPostgresDiscovery:
    def _run(self, monkeypatch, table_rows, column_rows):
        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)
        cursor.fetchall.side_effect = [table_rows, column_rows]
        conn = MagicMock()
        conn.cursor.return_value = cursor

        import postgres_metadata
        monkeypatch.setattr(postgres_metadata, "_connect", lambda params: conn)
        return postgres_metadata.discover_and_describe(
            {"host": "h", "database": "d", "user": "u", "password": "p"},
        )

    def test_output_matches_the_shared_shape(self, monkeypatch):
        """All three engines feed the same generic pipeline; a deviation would
        corrupt the catalog rather than fail loudly."""
        result = self._run(
            monkeypatch,
            [{"table_name": "orders", "row_count": 1234}],
            [{"table_name": "orders", "column_name": "id", "data_type": "integer",
              "is_nullable": "NO", "comment": "primary key"}],
        )

        assert set(result["orders"]) == _TABLE_KEYS
        assert set(result["orders"]["columns"][0]) == _COLUMN_KEYS
        assert result["orders"]["schema_name"] == "public"
        assert result["orders"]["row_count"] == 1234

    def test_nullable_is_derived_from_the_information_schema_flag(self, monkeypatch):
        result = self._run(
            monkeypatch,
            [{"table_name": "t", "row_count": 0}],
            [
                {"table_name": "t", "column_name": "a", "data_type": "text",
                 "is_nullable": "YES", "comment": None},
                {"table_name": "t", "column_name": "b", "data_type": "text",
                 "is_nullable": "NO", "comment": None},
            ],
        )

        assert [c["nullable"] for c in result["t"]["columns"]] == [True, False]

    def test_last_altered_is_none_because_postgres_has_no_such_column(self, monkeypatch):
        """Change detection hashes the column set instead — only the cheap
        timestamp shortcut is unavailable."""
        result = self._run(
            monkeypatch,
            [{"table_name": "t", "row_count": 1}],
            [{"table_name": "t", "column_name": "a", "data_type": "text",
              "is_nullable": "YES", "comment": None}],
        )

        assert result["t"]["last_altered"] is None

    def test_a_table_with_no_readable_columns_is_skipped_not_fatal(self, monkeypatch):
        """Keeps a partial crawl useful when one table is unreadable under the
        crawler's role."""
        result = self._run(
            monkeypatch,
            [{"table_name": "visible", "row_count": 1}, {"table_name": "hidden", "row_count": 1}],
            [{"table_name": "visible", "column_name": "a", "data_type": "text",
              "is_nullable": "YES", "comment": None}],
        )

        assert set(result) == {"visible"}


class TestClickHouseDiscovery:
    def _run(self, monkeypatch, table_rows, column_rows):
        client = MagicMock()
        client.query.side_effect = [
            MagicMock(result_rows=table_rows),
            MagicMock(result_rows=column_rows),
        ]
        import clickhouse_metadata
        monkeypatch.setattr(clickhouse_metadata, "_client", lambda params: client)
        return clickhouse_metadata.discover_and_describe({"host": "h", "database": "analytics"})

    def test_output_matches_the_shared_shape(self, monkeypatch):
        result = self._run(
            monkeypatch,
            [("events", 99, "2026-01-01")],
            [("events", "id", "UInt64", "", 1)],
        )

        assert set(result["events"]) == _TABLE_KEYS
        assert set(result["events"]["columns"][0]) == _COLUMN_KEYS
        assert result["events"]["schema_name"] == "analytics"
        assert result["events"]["row_count"] == 99

    def test_nullable_is_derived_from_the_type_not_assumed_false(self, monkeypatch):
        """ClickHouse encodes nullability in the type as Nullable(T); reporting
        every column as non-nullable would be silently wrong."""
        result = self._run(
            monkeypatch,
            [("events", 1, None)],
            [("events", "a", "Nullable(String)", "", 1), ("events", "b", "UInt64", "", 2)],
        )

        assert [c["nullable"] for c in result["events"]["columns"]] == [True, False]

    def test_missing_driver_raises_a_clear_error(self, monkeypatch):
        """Lazy import, so a build without it still crawls the other engines."""
        import clickhouse_metadata

        monkeypatch.setitem(sys.modules, "clickhouse_connect", None)
        with pytest.raises((RuntimeError, TypeError, AttributeError)):
            clickhouse_metadata._client({"host": "h"})


class TestDispatch:
    def test_each_engine_routes_to_its_own_module(self, monkeypatch):
        metadata = _metadata()
        calls = []

        for name in ("snowflake_metadata", "postgres_metadata", "clickhouse_metadata"):
            mod = types.ModuleType(name)
            mod.discover_and_describe = (
                lambda params, tables, _n=name: calls.append(_n) or {}
            )
            monkeypatch.setitem(sys.modules, name, mod)

        for engine in metadata.SUPPORTED_ENGINES:
            metadata.discover_and_describe(engine, {}, None)

        assert calls == ["snowflake_metadata", "postgres_metadata", "clickhouse_metadata"]

    def test_an_unknown_engine_raises_rather_than_defaulting(self):
        """Defaulting to Snowflake is how the original bug would come back."""
        with pytest.raises(ValueError, match="unsupported engine"):
            _metadata().discover_and_describe("mysql", {}, None)


class TestWarehouseDiscovery:
    """`get_active_warehouses` replaced `WHERE w.service = 'Snowflake'` — the
    single line that kept the catalog and the agent looking at different
    databases.

    Asserted against main.py's source rather than by importing it: the module
    pulls Kafka, Qdrant and psycopg2 at import time, and stubbing all three to
    check a WHERE clause makes the test about the stubs. The filtering semantics
    on top of the query are covered by TestEngineNormalisation.
    """

    def _source(self) -> str:
        return (pathlib.Path(_SVC_DIR) / "main.py").read_text(encoding="utf-8")

    def _sql_literals(self) -> list[str]:
        """Executable string constants only, docstrings excluded.

        main.py's own docstring quotes the old `w.service = 'Snowflake'` filter
        while explaining why it is gone, so a raw text scan would trip on the
        explanation instead of on real SQL.
        """
        import ast

        tree = ast.parse(self._source())
        docstrings = set()
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))                     and body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
        return [
            n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
        ]

    def test_the_query_no_longer_pins_snowflake(self):
        """A regression here silently returns the catalog to covering one engine
        while every other test still passes."""
        offenders = [lit for lit in self._sql_literals() if "service = 'Snowflake'" in lit]

        assert not offenders, f"warehouse discovery filters on Snowflake again: {offenders}"
        assert "def get_active_snowflake_warehouses" not in self._source()

    def test_the_query_selects_the_service_column_for_dispatch(self):
        """Without it there is nothing to normalise, and every warehouse would
        have to be assumed Snowflake again."""
        source = self._source()

        assert "w.service" in source
        assert "normalize_engine" in source

    def test_discovery_tags_each_row_with_its_engine(self):
        source = self._source()

        assert '"engine": engine' in source

    def test_both_crawl_paths_forward_the_engine(self):
        """The cron path and the DDL-event path each build their own connection;
        one of them silently defaulting would crawl a Postgres warehouse with
        the Snowflake driver."""
        source = self._source()

        assert source.count("metadata_dispatch.connection_params(") == 2
        assert "engine=engine" in source
