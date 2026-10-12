"""The read-only guard admits one query and nothing that could write or reach outside it."""

from __future__ import annotations

import pytest

from services.worker_agent.query.guard import SqlRejected, capped, check_read_only

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
    ],
)
def test_anything_else_is_rejected(sql: str) -> None:
    with pytest.raises(SqlRejected):
        check_read_only(sql, "postgres")


def test_the_cap_wraps_the_query() -> None:
    sql = capped(check_read_only("SELECT a FROM t ORDER BY a", "postgres"), "postgres", 10001)
    assert sql.endswith("LIMIT 10001") and "_q" in sql
