"""R1-02: query execution is wired for the two next SQL source kinds."""

from __future__ import annotations

import pytest

from libs.sql_guard import SqlRejected, check_read_only, dialect_of
from services.worker_agent.query.executor import driver_for
from services.worker_agent.questions import dialect_for_source

pytestmark = [pytest.mark.unit]


@pytest.mark.parametrize(
    ("kind", "dialect"),
    [
        ("postgresql", "postgres"),
        ("clickhouse", "clickhouse"),
        ("mysql", "mysql"),
        ("sqlserver", "tsql"),
    ],
)
def test_query_sources_have_a_guard_dialect(kind: str, dialect: str) -> None:
    assert dialect_of(kind) == dialect
    assert dialect_for_source(kind) == dialect


@pytest.mark.parametrize("kind", ["mysql", "sqlserver"])
def test_r1_query_sources_have_a_driver(kind: str) -> None:
    assert driver_for(kind).__name__ == f"_run_{kind}"


@pytest.mark.parametrize(
    ("sql", "dialect"),
    [
        ("SELECT `branch`, SUM(`balance`) FROM `loans` GROUP BY `branch`", "mysql"),
        ("SELECT TOP 10 [branch], SUM([balance]) FROM [loans] GROUP BY [branch]", "tsql"),
    ],
)
def test_mysql_and_sqlserver_read_queries_pass_the_guard(sql: str, dialect: str) -> None:
    check_read_only(sql, dialect)


@pytest.mark.parametrize(
    ("sql", "dialect"),
    [
        ("UPDATE `loans` SET `balance` = 0", "mysql"),
        ("DELETE FROM [loans]", "tsql"),
        ("SELECT TOP 1 * FROM [loans] WITH (UPDLOCK)", "tsql"),
    ],
)
def test_mysql_and_sqlserver_writes_are_rejected(sql: str, dialect: str) -> None:
    with pytest.raises(SqlRejected):
        check_read_only(sql, dialect)
