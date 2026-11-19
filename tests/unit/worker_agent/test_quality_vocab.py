"""DF-07: quality rules speak GX's vocabulary; `mostly` and `row_condition` apply to every rule
that counts rows; a row_condition is columns compared with constants, nothing else."""

from __future__ import annotations

import pytest

from libs import quality_vocab
from libs.dataagent_db.models import ingest
from services.worker_agent.quality import violation_sql

pytestmark = [pytest.mark.unit]


def _rule(kind: str, column: str | None, params: dict[str, object]) -> ingest.QualityRule:
    return ingest.QualityRule(kind=kind, column_name=column, params=params)


def test_every_kind_is_one_gx_expectation() -> None:
    names = {k: e.name for k, e in quality_vocab.EXPECTATIONS.items()}
    assert names["not_null"] == "expect_column_values_to_not_be_null"
    assert names["range"] == "expect_column_values_to_be_between"
    assert names["unique"] == "expect_column_values_to_be_unique"
    assert names["in_list"] == "expect_column_values_to_be_in_set"
    assert names["compound_unique"] == "expect_compound_columns_to_be_unique"
    assert names["match_regex"] == "expect_column_values_to_match_regex"
    assert names["custom_sql"] == "expect_query_to_return_no_rows"
    # The CHECK on ingest.quality_rule.kind lists the same nine.
    assert len(quality_vocab.KINDS) == 9


@pytest.mark.parametrize(
    ("violations", "total", "mostly", "passed"),
    [
        (0, 10, None, True),
        (1, 10, None, False),
        (1, 10, 0.9, True),
        (2, 10, 0.9, False),
        (1, 0, 0.9, False),
    ],
)
def test_mostly_lets_a_share_of_rows_break_the_rule(
    violations: int, total: int, mostly: float | None, passed: bool
) -> None:
    assert quality_vocab.passes(violations, total, mostly) is passed


@pytest.mark.parametrize("value", [0, -0.1, 1.5, "nhiều"])
def test_mostly_is_above_0_and_at_most_1(value: object) -> None:
    with pytest.raises(quality_vocab.RuleInvalid):
        quality_vocab.mostly({"mostly": value})


@pytest.mark.parametrize(
    "condition",
    [
        "segment = 'Cá nhân'",
        "amount > 0 AND segment IN ('A', 'B')",
        "(region <> 'Bắc') AND closed_at IS NULL",
        "closed_at IS NOT NULL AND amount >= -5",
        "0 < amount",
    ],
)
def test_a_row_condition_compares_columns_with_constants(condition: str) -> None:
    quality_vocab.row_condition(condition)


@pytest.mark.parametrize(
    "condition",
    [
        "amount > 0 OR 1 = 1",
        "amount > (SELECT max(x) FROM other)",
        "segment IN (SELECT segment FROM other)",
        "lower(segment) = 'a'",
        "t.segment = 'a'",
        "amount > other_column",
        "pg_sleep(10) IS NULL",
        "amount = 1; DROP TABLE x",
    ],
)
def test_anything_else_is_refused(condition: str) -> None:
    with pytest.raises(quality_vocab.RuleInvalid):
        quality_vocab.row_condition(condition)


def test_an_older_rule_reads_exactly_as_before() -> None:
    # 7 kinds unchanged: no mostly, no row_condition, the same SQL as test_quality_sql pins.
    assert violation_sql(_rule("unique", "x", {}), "t") == (
        'SELECT count(*) - count(DISTINCT "x") AS n FROM t WHERE "x" IS NOT NULL'
    )


def test_a_row_condition_narrows_the_rows_checked() -> None:
    sql = violation_sql(
        _rule("not_null", "gioi_tinh", {"row_condition": "segment = 'Cá nhân'"}), "t"
    )
    assert (
        sql == "SELECT count(*) AS n FROM t WHERE \"gioi_tinh\" IS NULL AND (segment = 'Cá nhân')"
    )


def test_mostly_counts_the_rows_looked_at() -> None:
    sql = violation_sql(_rule("in_list", "g", {"values": [1, 3], "mostly": 0.9}), "t")
    assert sql == (
        "SELECT coalesce(sum(CASE WHEN (\"g\" IS NOT NULL AND CAST(\"g\" AS text) NOT IN ('1', '3'))"
        ' THEN 1 ELSE 0 END), 0) AS n, count("g") AS total FROM t'
    )


def test_a_compound_key_counts_rows_beyond_the_first_of_each_key() -> None:
    sql = violation_sql(_rule("compound_unique", None, {"columns": ["order_id", "line_no"]}), "t")
    assert 'GROUP BY "order_id", "line_no" HAVING count(*) > 1' in sql
    with pytest.raises(ValueError):
        violation_sql(_rule("compound_unique", None, {"columns": ["only_one"]}), "t")


def test_a_regex_is_a_literal_in_every_dialect() -> None:
    rule = _rule("match_regex", "ma", {"regex": "^[A-Z]{2}'; DROP TABLE t; --"})
    sql = violation_sql(rule, "t")
    assert "~ '^[A-Z]{2}''; DROP TABLE t; --'" in sql
    assert "REGEXP_LIKE" in violation_sql(rule, "t", "mysql")


@pytest.mark.parametrize(
    ("kind", "column", "params"),
    [
        ("compound_unique", None, {"columns": ["a"]}),
        ("match_regex", "c", {"regex": "("}),
        ("match_regex", "c", {}),
        ("sum_matches", "c", {"expected": 1, "mostly": 0.9}),
        ("freshness", "c", {"row_condition": "a = 1"}),
        ("match_regex", None, {"regex": "^a"}),
        ("not_null", "c", {"row_condition": "a = 1 OR b = 2"}),
        ("nope", "c", {}),
    ],
)
def test_the_api_refuses_a_rule_that_is_no_check(
    kind: str, column: str | None, params: dict[str, object]
) -> None:
    with pytest.raises(quality_vocab.RuleInvalid):
        quality_vocab.validate(kind, column, params)


def test_an_older_kind_written_incomplete_is_still_accepted() -> None:
    # Before DF-07 the check, not the api, said "rule incomplete"; that stays.
    quality_vocab.validate("range", "c", {})
    quality_vocab.validate("range", None, {"min": 0})
