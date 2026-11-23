"""DF-15: the column lineage of a read-only query (libs/sql_guard/lineage.py)."""

from __future__ import annotations

import time
from typing import Any

import pytest

from libs.sql_guard import SqlRejected, lineage
from libs.sql_guard.lineage import Edge, column_lineage

pytestmark = [pytest.mark.unit, pytest.mark.req("DF-15")]

SCHEMA = {
    "sales": {
        "f": {"amount": "numeric", "cust": "int", "d": "date"},
        "c": {"id": "int", "name": "text", "region": "text"},
    }
}


def _pairs(sql: str, schema: Any = SCHEMA) -> set[tuple[str, str, str]]:
    return {
        (e.output_column, e.source_table, e.source_column)
        for e in column_lineage(sql, schema).edges
    }


def test_a_column_is_traced_through_a_cte_a_join_and_an_expression() -> None:
    got = column_lineage(
        "WITH t AS (SELECT cust, sum(amount) AS total FROM sales.f GROUP BY cust)"
        " SELECT c.region, t.total * 2 AS dbl, 'x' AS lit"
        " FROM t JOIN sales.c c ON c.id = t.cust",
        SCHEMA,
    )
    assert set(got.edges) == {
        Edge("sales", "c", "region", "region"),
        Edge("sales", "f", "amount", "dbl"),
    }
    # A constant is no source, and not unresolved either.
    assert got.unresolved == () and got.complete
    assert got.tables == {("sales", "f"), ("sales", "c")}


def test_every_branch_of_a_union_feeds_the_column() -> None:
    assert _pairs("SELECT amount AS v FROM sales.f UNION ALL SELECT id FROM sales.c") == {
        ("v", "f", "amount"),
        ("v", "c", "id"),
    }


def test_a_subquery_and_a_star_expand_with_the_schema() -> None:
    assert _pairs("SELECT * FROM (SELECT region, id FROM sales.c) s") == {
        ("region", "c", "region"),
        ("id", "c", "id"),
    }
    assert {o for o, _, _ in _pairs("SELECT * FROM sales.c")} == {"id", "name", "region"}


def test_a_star_without_a_schema_is_unresolved_but_its_table_is_known() -> None:
    got = column_lineage("SELECT * FROM sales.c", None)
    assert got.edges == () and got.unresolved == ("*",)
    assert got.tables == {("sales", "c")}


def test_an_unqualified_column_finds_its_table_through_the_schema() -> None:
    assert _pairs("SELECT region, amount FROM sales.f JOIN sales.c ON id = cust") == {
        ("region", "c", "region"),
        ("amount", "f", "amount"),
    }


def test_postgres_folds_an_unquoted_name_and_keeps_a_quoted_one() -> None:
    schema = {"s": {"T": {"Amount": "numeric"}, "t": {"amount": "numeric"}}}
    assert _pairs('SELECT "Amount" FROM s."T"', schema) == {("Amount", "T", "Amount")}
    assert _pairs("SELECT AMOUNT FROM S.T", schema) == {("amount", "t", "amount")}


def test_a_write_is_refused_before_anything_is_traced() -> None:
    with pytest.raises(SqlRejected):
        column_lineage("DELETE FROM sales.f", SCHEMA)
    with pytest.raises(SqlRejected):
        column_lineage("WITH x AS (DELETE FROM sales.f RETURNING *) SELECT * FROM x", SCHEMA)


def test_a_trace_past_its_time_falls_back_to_the_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow(*_: object) -> object:
        time.sleep(0.5)
        raise AssertionError("not reached in time")

    monkeypatch.setattr(lineage, "_trace", slow)
    got = column_lineage("SELECT amount FROM sales.f", SCHEMA, timeout_s=0.05)
    assert not got.complete and got.edges == ()
    assert got.tables == {("sales", "f")}


def test_a_query_sqlglot_cannot_follow_still_names_its_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*_: object) -> object:
        raise KeyError("cte column")

    monkeypatch.setattr(lineage, "_trace", broken)
    got = column_lineage("SELECT amount FROM sales.f", SCHEMA)
    assert not got.complete and got.tables == {("sales", "f")}


def test_an_unqualified_table_resolves_to_the_default_schema() -> None:
    got = column_lineage("SELECT region FROM c", SCHEMA, default_schema="sales")
    assert got.edges == (Edge("sales", "c", "region", "region"),)
