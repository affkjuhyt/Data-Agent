"""Share of total, accumulations and to-date periods (semantic/analysis.py) on AdventureWorksDW,
reconciled with window SQL written by hand on the tables, as DS-AW's metrics are in
test_adventureworks_reconciliation.py."""

from __future__ import annotations

import datetime
from typing import Any

import pytest

from services.worker_agent.semantic import analysis as a
from services.worker_agent.semantic import compiler as c
from tests.integration.worker_agent.test_adventureworks_reconciliation import (
    DEC2013,
    Y2013,
    _close,
    _layer,
    _rows,
)

pytestmark = [pytest.mark.integration]

D = datetime.date
JUNE2013 = c.Period(D(2013, 6, 1), D(2013, 7, 1))
Q3_2013 = c.Period(D(2013, 7, 1), D(2013, 10, 1))
TERRITORY = (
    "FROM fact_internet_sales f"
    " JOIN dim_sales_territory t ON t.sales_territory_key = f.sales_territory_key"
)


def _same(got: dict[Any, Any], want: dict[Any, Any], sql: str) -> None:
    assert got.keys() == want.keys(), sql
    wrong = {k: (got[k], want[k]) for k in want if not _close(got[k], want[k])}
    assert not wrong, f"{wrong}\n{sql}"


def test_the_territory_groups_shares_of_internet_revenue(adventureworks: Any) -> None:
    layer, metrics = _layer()
    compiled = a.compile_share(
        c.Request(metrics["DOANH_THU_INTERNET"], group_by=["khu_vuc"], period=Y2013),
        layer,
        dialect="postgres",
    )
    reference = f"""
        SELECT t.sales_territory_group, sum(f.sales_amount),
               100 * sum(f.sales_amount) / sum(sum(f.sales_amount)) OVER ()
        {TERRITORY}
        WHERE f.order_date >= date '2013-01-01' AND f.order_date < date '2014-01-01'
        GROUP BY 1"""
    _same(_rows(adventureworks, compiled.sql), _rows(adventureworks, reference), compiled.sql)
    shares = _rows(adventureworks, compiled.sql)
    assert sum(float(v) for v in shares.values()) == pytest.approx(100)


def test_reseller_revenue_accumulated_by_month(adventureworks: Any) -> None:
    # Resellers order in some months only (none in February 2011, for one): the running total
    # moves over the months that have sales, as the window over the same rows does.
    layer, metrics = _layer()
    period = c.Period(D(2011, 1, 1), D(2012, 1, 1))
    compiled = a.compile_running(
        c.Request(
            metrics["DOANH_THU_DAI_LY"], group_by=["ngay_dat_dai_ly"], period=period, grain="month"
        ),
        layer,
        dialect="postgres",
    )
    reference = """
        SELECT to_char(order_date, 'YYYY-MM') AS m, sum(sales_amount),
               sum(sum(sales_amount)) OVER (ORDER BY to_char(order_date, 'YYYY-MM'))
        FROM fact_reseller_sales
        WHERE order_date >= date '2011-01-01' AND order_date < date '2012-01-01'
        GROUP BY m"""
    _same(_rows(adventureworks, compiled.sql), _rows(adventureworks, reference), compiled.sql)


def test_june_year_to_date_by_month_reads_the_months_before(adventureworks: Any) -> None:
    layer, metrics = _layer()
    compiled = a.compile_running(
        c.Request(
            metrics["DOANH_THU_INTERNET"],
            group_by=["ngay_dat_internet"],
            period=JUNE2013,
            grain="month",
        ),
        layer,
        dialect="postgres",
        restart="year",
    )
    reference = """
        SELECT '2013-06', sum(sales_amount) FILTER (WHERE order_date >= date '2013-06-01'),
               sum(sales_amount)
        FROM fact_internet_sales
        WHERE order_date >= date '2013-01-01' AND order_date < date '2013-07-01'"""
    _same(_rows(adventureworks, compiled.sql), _rows(adventureworks, reference), compiled.sql)


def test_the_year_to_june_is_a_period_the_compiler_answers(adventureworks: Any) -> None:
    layer, metrics = _layer()
    ytd = a.to_date(JUNE2013, "year", metrics["DOANH_THU_INTERNET"])
    compiled = c.compile_metric(
        c.Request(metrics["DOANH_THU_INTERNET"], period=ytd), layer, dialect="postgres"
    )
    reference = """
        SELECT sum(sales_amount) FROM fact_internet_sales
        WHERE order_date >= date '2013-01-01' AND order_date < date '2013-07-01'"""
    _same(_rows(adventureworks, compiled.sql), _rows(adventureworks, reference), compiled.sql)


def test_what_does_not_add_up_is_refused_on_adventureworks() -> None:
    layer, metrics = _layer()
    # Distinct orders: a member's orders do not add up to the total's.
    with pytest.raises(c.CompileError, match="không cộng được"):
        a.compile_share(
            c.Request(metrics["SO_DON_INTERNET"], group_by=["khu_vuc"], period=Y2013),
            layer,
            dialect="postgres",
        )
    # A daily balance has no year to date.
    with pytest.raises(c.CompileError, match="số dư tại một ngày"):
        a.to_date(DEC2013, "year", metrics["TON_KHO"])
    # Cut by month, the accumulation restarts each year only: a quarter's is said, not guessed.
    with pytest.raises(c.CompileError, match="đầu quý hoặc đầu tháng"):
        a.compile_running(
            c.Request(
                metrics["DOANH_THU_INTERNET"],
                group_by=["ngay_dat_internet"],
                period=Q3_2013,
                grain="month",
            ),
            layer,
            dialect="postgres",
            restart="quarter",
        )
