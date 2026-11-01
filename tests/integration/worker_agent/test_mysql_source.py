"""A MySQL source is scanned, profiled and quality-checked on a real MySQL (GEN-12).

Through the executor's own driver, as the worker reaches a customer's MySQL: the scan reads
INFORMATION_SCHEMA, the profile and the quality checks run in MySQL's dialect. Before GEN-12 the
scan and profile went through asyncpg, and a not_null rule sent `"col" IS NULL`, which MySQL
reads as a string literal: it counted nothing and the rule passed.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from libs.dataagent_db.models import catalog, ingest
from services.worker_agent import source_meta
from services.worker_agent.quality import violation_sql
from services.worker_agent.schema_scans import _normalized_type

pytestmark = [pytest.mark.integration]

SETUP = """
CREATE TABLE customers (id INT PRIMARY KEY, `Họ tên` VARCHAR(50), region VARCHAR(10));
CREATE TABLE orders (
  id BIGINT PRIMARY KEY, customer_id INT NOT NULL, amount DECIMAL(12, 2),
  FOREIGN KEY (customer_id) REFERENCES customers(id)
);
INSERT INTO customers VALUES (1, 'An', 'HN'), (2, 'Bình', 'HN'), (3, NULL, NULL);
INSERT INTO orders VALUES (10, 1, 5.5), (11, 1, 7), (12, 2, NULL);
"""


@pytest.fixture(scope="module")
def mysql() -> Iterator[dict[str, Any]]:
    try:
        from testcontainers.mysql import MySqlContainer
    except ImportError:
        pytest.skip("testcontainers mysql not installed")
    with MySqlContainer("mysql:8.4", dbname="shop", username="reader", password="pw") as box:
        import pymysql

        conn = pymysql.connect(
            host=box.get_container_host_ip(),
            port=int(box.get_exposed_port(3306)),
            user="root",
            password=box.root_password,
            database="shop",
            autocommit=True,
        )
        with conn.cursor() as cursor:
            for statement in filter(str.strip, SETUP.split(";")):
                cursor.execute(statement)
        conn.close()
        yield {
            "host": box.get_container_host_ip(),
            "port": int(box.get_exposed_port(3306)),
        }


def _source(box: dict[str, Any]) -> catalog.Source:
    return catalog.Source(
        kind="mysql",
        host=box["host"],
        port=box["port"],
        database_name="shop",
        conn_options={"sslmode": "disable"},
        query_timeout_s=30,
        scan_schemas=None,
    )


async def test_a_mysql_source_is_scanned_and_profiled(mysql: dict[str, Any]) -> None:
    source = _source(mysql)
    run = source_meta.driver_run(source, "reader", "pw")
    tables = {
        t["table_name"]: t for t in await source_meta.scan(source, None, run, _normalized_type)
    }
    assert set(tables) == {"customers", "orders"}
    columns = {c["name"]: c for c in tables["customers"]["columns"]}
    assert columns["id"]["is_key"] and columns["Họ tên"]["normalized_type"] == "string"
    [fk] = tables["orders"]["foreign_keys"]
    assert (fk["child_column"], fk["parent_table"], fk["parent_column"]) == (
        "customer_id",
        "customers",
        "id",
    )

    profiles = await source_meta.profile(
        source, [tables["customers"]], run, lambda name: False, lambda name, v: str(v)
    )
    region = profiles[("shop", "customers", "region")]
    assert region["sample_rows"] == 3 and region["distinct_est"] == 1
    assert region["top_values"] == [{"value": "HN", "count": 2}]


async def test_a_failing_not_null_rule_fails_on_mysql(mysql: dict[str, Any]) -> None:
    source = _source(mysql)
    run = source_meta.driver_run(source, "reader", "pw")
    rule = ingest.QualityRule(kind="not_null", column_name="amount", params={})
    _, rows = await run(violation_sql(rule, '"shop"."orders"', "mysql"))
    assert int(rows[0][0]) == 1
    fresh = ingest.QualityRule(kind="freshness", column_name="amount", params={"max_age_hours": 1})
    # Not a date column, but the statement itself must be one MySQL runs.
    await run(violation_sql(fresh, '"shop"."orders"', "mysql"))
