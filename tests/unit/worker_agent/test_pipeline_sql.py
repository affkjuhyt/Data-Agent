"""The SQL a pipeline runs on its source (GEN-12): the steps' CTEs lead the statement, so SQL
Server, which refuses a WITH inside a subquery, runs it like every other engine."""

from __future__ import annotations

import pytest
import sqlglot
from sqlglot import exp

from services.worker_agent.pipelines import chain, over, sample_of, total

pytestmark = [pytest.mark.unit]

STEPS = [
    {"code": "SELECT region, amount FROM sales WHERE amount > 0"},
    {"code": "SELECT region, SUM(amount) AS amount FROM t GROUP BY region"},
]


@pytest.mark.parametrize("dialect", ["tsql", "postgres", "mysql", "clickhouse"])
def test_the_steps_ctes_lead_the_statement(dialect: str) -> None:
    sql = over(chain(STEPS, 1), dialect, exp.Count(this=exp.Star()), total("amount", dialect))
    tree = sqlglot.parse_one(sql, read=dialect)
    # One WITH, at the top; no subquery holds one.
    assert sql.startswith("WITH s0 AS (")
    assert not any(
        isinstance(sub.this, exp.Select) and sub.this.args.get("with")
        for sub in tree.find_all(exp.Subquery)
    )
    assert [cte.alias for cte in tree.args["with"].expressions] == ["s0", "s1", "da_inner_query"]


def test_a_preview_on_sql_server_is_top_not_limit() -> None:
    sql = over(chain(STEPS, 0, "sales|" + sample_of("dbo", "sales", 5, "tsql")), "tsql", limit=0)
    assert "TOP 5" in sql and "TOP 0" in sql and "LIMIT" not in sql
    sqlglot.parse_one(sql, read="tsql")


def test_sql_that_is_no_chain_is_wrapped_as_before() -> None:
    assert (
        over("SELECT 1 AS a", "postgres", limit=1) == "SELECT * FROM (SELECT 1 AS a) AS q LIMIT 1"
    )
