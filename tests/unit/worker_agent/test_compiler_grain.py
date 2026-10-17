"""Compiler 2 (R1-06): a cut along time at a grain, the average over a period, and a metric whose
measures sit on several tables."""

from __future__ import annotations

import datetime
import uuid

import pytest
import sqlglot

from services.worker_agent.semantic.compiler import (
    Dimension,
    Layer,
    Measure,
    Period,
    Request,
    Table,
    compile_metric,
)

from .test_compiler import BRANCHES, DAY, LAYER, LOAN_ENTITY, LOANS, REGION, metric

pytestmark = [pytest.mark.unit]

D = datetime.date
YEAR_2026 = Period(D(2026, 1, 1), D(2027, 1, 1))
DIALECTS = ["postgres", "mysql", "tsql", "clickhouse", "oracle", "snowflake", "trino"]


def test_a_balance_by_month_is_read_on_each_months_last_day() -> None:
    out = compile_metric(
        Request(metric(), group_by=["ngay"], period=YEAR_2026, grain="month"),
        LAYER,
        dialect="postgres",
    )
    assert out.columns == ("ngay", "value")
    assert "TO_CHAR(t0.as_of_date, 'YYYY-MM') AS \"ngay\"" in out.sql
    # The last day of each month with data, within the year; not the sum of the month's days.
    assert "t0.as_of_date IN (SELECT MAX(l.as_of_date)" in out.sql
    assert "GROUP BY TO_CHAR(l.as_of_date, 'YYYY-MM')" in out.sql
    assert "t0.as_of_date >= CAST('2026-01-01' AS DATE)" in out.sql


def test_the_metrics_own_grain_applies_when_the_question_names_none() -> None:
    out = compile_metric(
        Request(metric(period_semantics="sum_over_period", time_grain="year"), group_by=["ngay"]),
        LAYER,
        dialect="postgres",
    )
    assert "TO_CHAR(t0.as_of_date, 'YYYY') AS \"ngay\"" in out.sql and "MAX(" not in out.sql


def test_a_day_cut_is_unchanged() -> None:
    out = compile_metric(
        Request(metric(), group_by=["ngay"], period=YEAR_2026), LAYER, dialect="postgres"
    )
    assert 't0.as_of_date AS "ngay"' in out.sql and "IN (SELECT MAX" not in out.sql


def test_a_quarter_is_worked_out_from_the_month() -> None:
    out = compile_metric(
        Request(metric(period_semantics="sum_over_period"), group_by=["ngay"], grain="quarter"),
        LAYER,
        dialect="postgres",
    )
    assert "QUARTER(" not in out.sql
    assert "CONCAT(TO_CHAR(t0.as_of_date, 'YYYY'), CASE WHEN" in out.sql
    assert "IN ('07', '08', '09') THEN '-Q3'" in out.sql


TEXT_MONTH = Dimension(
    uuid.uuid4(), "thang", "Tháng", "month", LOAN_ENTITY, is_time=True, value_type="string"
)


def test_a_time_stored_as_text_is_cut_not_formatted() -> None:
    layer = Layer(
        LAYER.measures,
        {d.code: d for d in (REGION, TEXT_MONTH)},
        LAYER.entity_tables,
        LAYER.joins,
    )
    out = compile_metric(
        Request(
            metric(period_semantics="sum_over_period", filters=()), group_by=["thang"], grain="year"
        ),
        layer,
        dialect="postgres",
    )
    assert "SUBSTRING(t0.month FROM 1 FOR 4)" in out.sql and "TO_CHAR" not in out.sql


def test_an_average_over_the_period_is_the_mean_of_its_daily_values() -> None:
    out = compile_metric(
        Request(
            metric(period_semantics="average_over_period"),
            group_by=["vung"],
            period=Period(D(2026, 9, 1), D(2026, 10, 1)),
        ),
        LAYER,
        dialect="postgres",
    )
    tree = sqlglot.parse_one(out.sql, read="postgres")
    assert out.columns == ("vung", "value")
    assert tree.selects[-1].sql(dialect="postgres") == 'AVG("value") AS "value"'
    # The daily values: grouped by region and day inside, by region outside.
    inner = tree.find(sqlglot.exp.Subquery)
    assert inner is not None and '"_day"' in inner.sql(dialect="postgres")
    assert 'GROUP BY "vung" ORDER BY "vung"' in out.sql


# A second entity: customers, on their own table, joined to the branch like loans.
CUSTOMERS, CUSTOMER_ENTITY = uuid.uuid4(), uuid.uuid4()
CUSTOMER_DAY = Dimension(
    uuid.uuid4(), "ngay_kh", "Ngày", "snapshot_date", CUSTOMER_ENTITY, is_time=True
)
TWO_TABLES = Layer(
    measures={
        **LAYER.measures,
        "so_khach_hang": Measure("so_khach_hang", "COUNT(DISTINCT customer_id)", CUSTOMER_ENTITY),
    },
    dimensions={**LAYER.dimensions, CUSTOMER_DAY.code: CUSTOMER_DAY},
    entity_tables={**LAYER.entity_tables, CUSTOMER_ENTITY: Table(CUSTOMERS, "dw", "customer")},
    joins=(
        *LAYER.joins,
        LAYER.joins[0].__class__(CUSTOMERS, ("branch_code",), BRANCHES, ("code",)),
    ),
)


def test_measures_of_two_tables_are_aggregated_apart_then_combined() -> None:
    out = compile_metric(
        Request(
            metric(
                expression="du_no / so_khach_hang",
                period_semantics="sum_over_period",
                filters=(),
            ),
            group_by=["vung"],
            period=Period(D(2026, 9, 1), D(2026, 10, 1)),
        ),
        TWO_TABLES,
        dialect="postgres",
    )
    assert out.columns == ("vung", "value")
    assert set(out.dataset_ids) == {LOANS, BRANCHES, CUSTOMERS}
    # Each table aggregated on its own, never joined to the other before aggregating.
    assert '"dw"."loan_balance" AS t0' in out.sql and '"dw"."customer" AS t0' in out.sql
    assert "UNION ALL" in out.sql and 'JOIN "dw"."customer"' not in out.sql
    # The metric's expression over each side's aggregate: one row per region on each side.
    value = sqlglot.parse_one(out.sql, read="postgres").selects[-1].sql(dialect="postgres")
    assert 'MAX("m_du_no")' in value and '/ (MAX("m_so_khach_hang"))' in value
    # Each side cut to the period on its own time column.
    assert "t0.as_of_date >= CAST('2026-09-01' AS DATE)" in out.sql
    assert "t0.snapshot_date >= CAST('2026-09-01' AS DATE)" in out.sql


def test_two_tables_cut_by_month_meet_on_the_months_label() -> None:
    out = compile_metric(
        Request(
            metric(
                expression="du_no / so_khach_hang", period_semantics="sum_over_period", filters=()
            ),
            group_by=["ngay"],
            grain="month",
        ),
        TWO_TABLES,
        dialect="postgres",
    )
    assert "TO_CHAR(t0.as_of_date, 'YYYY-MM') AS \"ngay\"" in out.sql
    assert "TO_CHAR(t0.snapshot_date, 'YYYY-MM') AS \"ngay\"" in out.sql


def test_a_dimension_one_table_cannot_reach_is_no_join_path() -> None:
    layer = Layer(TWO_TABLES.measures, TWO_TABLES.dimensions, TWO_TABLES.entity_tables, LAYER.joins)
    with pytest.raises(Exception) as caught:
        compile_metric(
            Request(metric(expression="du_no / so_khach_hang", filters=()), group_by=["vung"]),
            layer,
            dialect="postgres",
        )
    assert getattr(caught.value, "code", None) == "NO_JOIN_PATH"


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize(
    "request_",
    [
        Request(metric(), group_by=["ngay"], period=YEAR_2026, grain="quarter"),
        Request(
            metric(period_semantics="average_over_period"),
            group_by=["vung"],
            period=Period(D(2026, 9, 1), D(2026, 10, 1)),
        ),
        Request(
            metric(
                expression="du_no / so_khach_hang", period_semantics="sum_over_period", filters=()
            ),
            group_by=["vung"],
        ),
    ],
)
def test_every_dialect_parses_it_back(dialect: str, request_: Request) -> None:
    out = compile_metric(request_, TWO_TABLES, dialect=dialect)
    sqlglot.parse_one(out.sql, read=dialect)


def test_day_grain_dimensions_keep_their_type() -> None:
    assert DAY.value_type is None
