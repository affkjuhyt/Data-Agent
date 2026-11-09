"""The query a period close reads its snapshot with, in the source's own dialect."""

from __future__ import annotations

import datetime

import pytest

from libs.sql_guard import DIALECTS, check_read_only, tables_of
from services.worker_agent.periods import snapshot_sql

pytestmark = pytest.mark.unit

DAY = datetime.date(2026, 9, 30)


def test_postgres_reads_the_rows_up_to_the_business_date() -> None:
    assert snapshot_sql("silver", "loan", "ngay", snapshot=False, day=DAY, dialect="postgres") == (
        'SELECT * FROM "silver"."loan" WHERE "ngay" <= CAST(\'2026-09-30\' AS DATE)'
    )


def test_a_snapshot_table_reads_the_business_date_only() -> None:
    assert "\"ngay\" = CAST('2026-09-30' AS DATE)" in snapshot_sql(
        "silver", "loan", "ngay", snapshot=True, day=DAY, dialect="postgres"
    )


def test_mysql_gets_its_own_quoting() -> None:
    # Written the PostgreSQL way, "silver"."loan" is two string literals in MySQL's default mode.
    sql = snapshot_sql("silver", "loan", "ngay", snapshot=False, day=DAY, dialect="mysql")
    assert sql.startswith("SELECT * FROM `silver`.`loan` WHERE `ngay` <=")


@pytest.mark.parametrize("dialect", sorted(set(DIALECTS.values())))
def test_every_dialect_reads_the_one_table_and_passes_the_guard(dialect: str) -> None:
    sql = snapshot_sql('odd"schema', "loan", "ngay", snapshot=False, day=DAY, dialect=dialect)
    tree = check_read_only(sql, dialect)
    assert {name for _, name in tables_of(tree, dialect)} == {"loan"}


def test_without_a_business_date_column_every_row_is_read() -> None:
    assert snapshot_sql("silver", "loan", None, snapshot=False, day=DAY, dialect="postgres") == (
        'SELECT * FROM "silver"."loan"'
    )
