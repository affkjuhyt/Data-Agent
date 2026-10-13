"""The read-only guard admits one query and nothing that could write or reach outside it."""

from __future__ import annotations

import pytest

from services.worker_agent.query.guard import SqlRejected, capped, check_read_only, tables_of

pytestmark = [pytest.mark.unit]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT branch, SUM(balance) FROM loans GROUP BY branch",
        "WITH t AS (SELECT 1 AS a) SELECT a FROM t",
        "SELECT a FROM x UNION ALL SELECT a FROM y",
        "SELECT ';DROP TABLE x' AS s",
    ],
)
def test_a_read_only_query_passes(sql: str) -> None:
    check_read_only(sql, "postgres")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; DROP TABLE loans",
        "DELETE FROM loans",
        "UPDATE loans SET balance = 0",
        "WITH d AS (DELETE FROM loans RETURNING *) SELECT * FROM d",
        "SELECT * INTO copy_of_loans FROM loans",
        "SELECT pg_sleep(30)",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT * FROM loans FOR UPDATE",
        "CREATE TABLE x AS SELECT 1",
        "SELECT set_config('role', 'postgres', false)",
        "COPY loans TO '/tmp/x'",
        "SELEC broken",
        "SELECT * FROM loans WHERE name = 'unclosed",
        "SELECT pg_catalog.pg_ls_waldir()",
        "SELECT * FROM dblink('host=x', 'select 1') AS t(a int)",
        "SELECT my_schema.custom_writer(1)",
    ],
)
def test_anything_else_is_rejected(sql: str) -> None:
    with pytest.raises(SqlRejected):
        check_read_only(sql, "postgres")


def test_the_cap_wraps_the_query() -> None:
    sql = capped(check_read_only("SELECT a FROM t ORDER BY a", "postgres"), "postgres", 10001)
    assert sql.endswith("LIMIT 10001") and "_q" in sql


def test_what_runs_is_checked_again() -> None:
    sql = capped(check_read_only("SELECT 'it''s' AS a", "postgres"), "postgres", 5)
    assert check_read_only(sql, "postgres").sql(dialect="postgres") == sql


@pytest.mark.parametrize(
    "sql", ["SELECT 1 -- note", "SELECT /* x */ 1", "SELECT 1 /*; DROP TABLE t */"]
)
def test_a_comment_is_refused(sql: str) -> None:
    with pytest.raises(SqlRejected):
        check_read_only(sql, "postgres")


def test_the_tables_a_query_reads_leave_out_ctes_and_table_functions() -> None:
    tree = check_read_only(
        'WITH recent AS (SELECT * FROM bank.loan) SELECT * FROM recent JOIN "Branch" b ON true'
        " JOIN generate_series(1, 3) g ON true JOIN LOAN x ON true",
        "postgres",
    )
    # PostgreSQL folds an unquoted name and keeps a quoted one.
    assert tables_of(tree, "postgres") == {("bank", "loan"), (None, "Branch"), (None, "loan")}


def test_a_table_in_another_database_is_refused() -> None:
    with pytest.raises(SqlRejected):
        tables_of(check_read_only("SELECT * FROM other.public.t", "postgres"), "postgres")
