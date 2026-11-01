"""Scanning and profiling a non-PostgreSQL source from INFORMATION_SCHEMA (source_meta, GEN-12)."""

from __future__ import annotations

import decimal
from typing import Any

import pytest

from libs.dataagent_db.models import catalog
from services.worker_agent import source_meta
from services.worker_agent.query.executor import SourceDriverError
from services.worker_agent.schema_scans import _normalized_type

pytestmark = [pytest.mark.unit]


def _source(kind: str = "mysql") -> catalog.Source:
    return catalog.Source(kind=kind, database_name="shop", scan_schemas=None, conn_options={})


def _fake(answers: dict[str, list[list[Any]]]) -> source_meta.Run:
    """`run` answering by the first table the SQL reads from."""

    async def run(sql: str) -> tuple[list[dict[str, str]], list[list[Any]]]:
        for marker, rows in answers.items():
            if marker in sql:
                return [], rows
        raise AssertionError(sql)

    return run


async def test_a_mysql_source_scans_tables_keys_and_one_to_one_links() -> None:
    run = _fake(
        {
            "REFERENCED_TABLE_NAME": [
                ["shop", "orders", "fk_cust", "customer_id", "shop", "customers", "id"],
                ["shop", "profiles", "fk_prof", "customer_id", "shop", "customers", "id"],
            ],
            "TABLE_CONSTRAINTS": [
                ["shop", "customers", "PRIMARY", "PRIMARY KEY", "id"],
                ["shop", "orders", "PRIMARY", "PRIMARY KEY", "id"],
                ["shop", "profiles", "PRIMARY", "PRIMARY KEY", "customer_id"],
            ],
            "INFORMATION_SCHEMA.COLUMNS": [
                ["shop", "customers", "BASE TABLE", 3, "id", "int", "NO", 1],
                ["shop", "customers", "BASE TABLE", 3, "Họ tên", "varchar", "YES", 2],
                ["shop", "orders", "BASE TABLE", 9, "id", "bigint", "NO", 1],
                ["shop", "orders", "BASE TABLE", 9, "customer_id", "int", "NO", 2],
                ["shop", "profiles", "BASE TABLE", 2, "customer_id", "int", "NO", 1],
                ["shop", "v_sales", "VIEW", None, "amount", "decimal", "YES", 1],
            ],
        }
    )
    tables = {
        t["table_name"]: t for t in await source_meta.scan(_source(), None, run, _normalized_type)
    }
    assert tables["customers"]["columns"][0]["is_key"] is True
    assert tables["customers"]["columns"][1]["name"] == "Họ tên"
    assert tables["v_sales"]["object_kind"] == "view"
    assert tables["orders"]["row_estimate"] == 9
    [order_fk] = tables["orders"]["foreign_keys"]
    assert (order_fk["parent_table"], order_fk["child_unique"]) == ("customers", False)
    # profiles.customer_id is both its primary key and its foreign key: one profile per customer.
    assert tables["profiles"]["foreign_keys"][0]["child_unique"] is True


async def test_a_requested_scan_keeps_only_the_named_tables() -> None:
    run = _fake(
        {
            "REFERENCED_TABLE_NAME": [],
            "TABLE_CONSTRAINTS": [],
            "INFORMATION_SCHEMA.COLUMNS": [
                ["shop", "a", "BASE TABLE", 0, "x", "int", "YES", 1],
                ["shop", "b", "BASE TABLE", 0, "y", "int", "YES", 1],
            ],
        }
    )
    found = await source_meta.scan(_source(), ["shop.b"], run, _normalized_type)
    assert [t["table_name"] for t in found] == ["b"]


async def test_a_column_the_engine_cannot_aggregate_is_left_unprofiled() -> None:
    async def run(sql: str) -> tuple[list[dict[str, str]], list[list[Any]]]:
        if "notes" in sql:
            raise SourceDriverError("sqlserver query failed (OperationalError)")
        if "value_count" in sql:
            return [], [["HN", 2], ["HCM", 1]]
        return [], [[4, 1, 2, "HCM", "HN"]]

    table = {
        "schema_name": "dbo",
        "table_name": "t",
        "columns": [{"name": "region"}, {"name": "notes"}],
    }
    profiles = await source_meta.profile(
        _source("sqlserver"), [table], run, lambda name: False, lambda name, v: str(v)
    )
    assert set(profiles) == {("dbo", "t", "region")}
    region = profiles[("dbo", "t", "region")]
    assert region["null_ratio"] == decimal.Decimal("0.25")
    assert region["top_values"] == [{"value": "HN", "count": 2}, {"value": "HCM", "count": 1}]


@pytest.mark.parametrize(
    ("data_type", "normalized"),
    [
        ("bit", "boolean"),
        ("float", "decimal"),
        ("Float64", "decimal"),
        ("UInt32", "integer"),
        ("NUMBER", "decimal"),
        ("money", "decimal"),
        ("datetime2", "timestamp"),
        ("nvarchar", "string"),
    ],
)
def test_engine_type_names_normalize(data_type: str, normalized: str) -> None:
    assert _normalized_type(data_type) == normalized


def test_profile_sql_is_written_in_the_sources_dialect() -> None:
    assert source_meta.top_values_sql("dbo", "t", "Số tiền", "tsql").startswith("SELECT TOP 10")
    assert "`Số tiền`" in source_meta.stats_sql("shop", "t", "Số tiền", "mysql")


def test_a_schema_name_cannot_close_its_string_on_mysql() -> None:
    # MySQL reads a backslash as an escape: doubling quotes alone let `a\' OR 1=1 --` out.
    sql = source_meta._columns_sql("mysql", ["a\\' OR 1=1 -- "])
    assert "IN ('a\\\\'' OR 1=1 -- ')" in sql


async def test_a_clickhouse_column_that_is_not_a_plain_name_is_not_profiled() -> None:
    sent: list[str] = []

    async def run(sql: str) -> tuple[list[dict[str, str]], list[list[Any]]]:
        sent.append(sql)
        return [], [[1, 0, 1, "a", "a"]] if "sample_rows" in sql else [["a", 1]]

    table = {"schema_name": "db", "table_name": "t", "columns": [{"name": 'x\\" y'}, {"name": "ok"}]}
    profiles = await source_meta.profile(
        _source("clickhouse"), [table], run, lambda name: False, lambda name, v: str(v)
    )
    assert set(profiles) == {("db", "t", "ok")}
    assert all("x\\" not in sql for sql in sent)
