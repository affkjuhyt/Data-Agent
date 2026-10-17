"""compare_to (R1-06): the base period a comparison reads, and the one statement that reads both."""

from __future__ import annotations

import datetime

import pytest
import sqlglot

from services.worker_agent.semantic.compare import (
    PREVIOUS_PERIOD,
    SAME_PERIOD_LAST_YEAR,
    base_period,
    compile_comparison,
)
from services.worker_agent.semantic.compiler import CompileError, Period, Request, compile_metric

from .test_compiler import LAYER, metric

pytestmark = [pytest.mark.unit]

D = datetime.date


@pytest.mark.parametrize(
    ("period", "compare_to", "base"),
    [
        # A month, a quarter and a year: the one before, in whole months.
        (
            Period(D(2026, 3, 1), D(2026, 4, 1)),
            PREVIOUS_PERIOD,
            Period(D(2026, 2, 1), D(2026, 3, 1)),
        ),
        (
            Period(D(2026, 1, 1), D(2026, 4, 1)),
            PREVIOUS_PERIOD,
            Period(D(2025, 10, 1), D(2026, 1, 1)),
        ),
        (
            Period(D(2026, 1, 1), D(2027, 1, 1)),
            PREVIOUS_PERIOD,
            Period(D(2025, 1, 1), D(2026, 1, 1)),
        ),
        # A run of days: the same number of days before.
        (
            Period(D(2026, 10, 6), D(2026, 10, 13)),
            PREVIOUS_PERIOD,
            Period(D(2026, 9, 29), D(2026, 10, 6)),
        ),
        (
            Period(D(2026, 7, 1), D(2026, 10, 1)),
            SAME_PERIOD_LAST_YEAR,
            Period(D(2025, 7, 1), D(2025, 10, 1)),
        ),
        # 29/02 a year back is the last day of February.
        (
            Period(D(2028, 2, 29), D(2028, 3, 1)),
            SAME_PERIOD_LAST_YEAR,
            Period(D(2027, 2, 28), D(2027, 3, 1)),
        ),
    ],
)
def test_the_base_period(period: Period, compare_to: str, base: Period) -> None:
    assert base_period(period, compare_to) == base


SEPTEMBER = Period(D(2026, 9, 1), D(2026, 10, 1))


def test_a_comparison_reads_both_periods_with_the_compilers_own_statements() -> None:
    request = Request(metric(), group_by=["vung"], period=SEPTEMBER)
    out, base = compile_comparison(request, LAYER, PREVIOUS_PERIOD, dialect="postgres")
    assert base == Period(D(2026, 8, 1), D(2026, 9, 1))
    assert out.columns == ("vung", "value", "base_value")
    assert "UNION ALL" in out.sql and "FULL" not in out.sql
    assert "CAST('2026-09-01' AS DATE)" in out.sql and "CAST('2026-08-01' AS DATE)" in out.sql
    # Each side is what the same question without a comparison compiles to (ordering aside).
    alone = compile_metric(request, LAYER, dialect="postgres")
    without_order = sqlglot.parse_one(alone.sql, read="postgres")
    without_order.set("order", None)
    assert without_order.sql(dialect="postgres") in out.sql
    assert out.sql_sha256 != alone.sql_sha256 and out.metric_version_id == alone.metric_version_id
    tree = sqlglot.parse_one(out.sql, read="postgres")
    assert tree.args.get("group") is not None


def test_a_comparison_without_a_cut_is_one_row() -> None:
    out, _ = compile_comparison(
        Request(metric(period_semantics="sum_over_period"), period=SEPTEMBER),
        LAYER,
        SAME_PERIOD_LAST_YEAR,
        dialect="postgres",
    )
    tree = sqlglot.parse_one(out.sql, read="postgres")
    assert tree.args.get("group") is None and out.columns == ("value", "base_value")
    assert "CAST('2025-09-01' AS DATE)" in out.sql


@pytest.mark.parametrize("dialect", ["postgres", "mysql", "tsql", "clickhouse", "oracle"])
def test_every_dialect_parses_it_back(dialect: str) -> None:
    out, _ = compile_comparison(
        Request(metric(), group_by=["vung"], period=SEPTEMBER),
        LAYER,
        PREVIOUS_PERIOD,
        dialect=dialect,
    )
    sqlglot.parse_one(out.sql, read=dialect)


@pytest.mark.parametrize(
    ("request_", "compare_to", "code"),
    [
        (Request(metric()), PREVIOUS_PERIOD, "NO_DATA_FOR_PERIOD"),
        (Request(metric(), group_by=["ngay"], period=SEPTEMBER), PREVIOUS_PERIOD, "UNSUPPORTED"),
        (Request(metric(), period=SEPTEMBER), "next_week", "CONTRACT_MISMATCH"),
    ],
)
def test_what_cannot_be_compared_says_why(request_: Request, compare_to: str, code: str) -> None:
    with pytest.raises(CompileError) as raised:
        compile_comparison(request_, LAYER, compare_to, dialect="postgres")
    assert raised.value.code == code
