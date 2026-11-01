"""A fiscal year from July and weeks from Sunday (GEN-21) on AdventureWorksDW, reconciled with SQL
written by hand on the tables, as test_adventureworks_reconciliation.py does for DS-AW's metrics.

The calendar is the compiler's input, not DS-AW's dim_date.fiscal_year: AdventureWorksDW labels
its fiscal years one year off (the xfail there), and a customer's books are set on its calendar."""

from __future__ import annotations

import dataclasses
import datetime
from typing import Any

import pytest

from libs import clock
from services.worker_agent.semantic import analysis as a
from services.worker_agent.semantic import compiler as c
from tests.integration.worker_agent.test_adventureworks_reconciliation import (
    _close,
    _layer,
    _rows,
)

pytestmark = [pytest.mark.integration]

D = datetime.date
JULY = clock.Calendar(week_start=7, fiscal_year_start_month=7, fiscal_year_named_by="end")


def _fiscal_layer() -> tuple[c.Layer, dict[str, c.MetricVersion]]:
    layer, metrics = _layer()
    return dataclasses.replace(layer, calendar=JULY), metrics


def test_fy2012_is_july_2011_to_june_2012(adventureworks: Any) -> None:
    layer, metrics = _fiscal_layer()
    compiled = c.compile_metric(
        c.Request(
            metrics["DOANH_THU_INTERNET"], group_by=["ngay_dat_internet"], grain="fiscal_year"
        ),
        layer,
        dialect="postgres",
    )
    by_year = _rows(adventureworks, compiled.sql)
    want = _rows(
        adventureworks,
        "SELECT SUM(sales_amount) FROM fact_internet_sales"
        " WHERE order_date >= DATE '2011-07-01' AND order_date < DATE '2012-07-01'",
    )
    assert _close(by_year[("FY2012",)], want[()]), compiled.sql


def test_a_fiscal_quarter_by_its_label(adventureworks: Any) -> None:
    layer, metrics = _fiscal_layer()
    compiled = c.compile_metric(
        c.Request(
            metrics["DOANH_THU_INTERNET"],
            group_by=["ngay_dat_internet"],
            grain="fiscal_quarter",
        ),
        layer,
        dialect="postgres",
    )
    # Q2 of FY2013 is October to December 2012.
    want = _rows(
        adventureworks,
        "SELECT SUM(sales_amount) FROM fact_internet_sales"
        " WHERE order_date >= DATE '2012-10-01' AND order_date < DATE '2013-01-01'",
    )
    assert _close(_rows(adventureworks, compiled.sql)[("FY2013-Q2",)], want[()]), compiled.sql


def test_weeks_from_sunday(adventureworks: Any) -> None:
    layer, metrics = _fiscal_layer()
    period = c.Period(D(2013, 6, 1), D(2013, 7, 1))
    compiled = c.compile_metric(
        c.Request(
            metrics["DOANH_THU_INTERNET"],
            group_by=["ngay_dat_internet"],
            grain="week",
            period=period,
        ),
        layer,
        dialect="postgres",
    )
    got = _rows(adventureworks, compiled.sql)
    # 2013-06-02 is a Sunday: its week runs to Saturday the 8th.
    want = _rows(
        adventureworks,
        "SELECT SUM(sales_amount) FROM fact_internet_sales"
        " WHERE order_date >= DATE '2013-06-02' AND order_date < DATE '2013-06-09'",
    )
    assert _close(got[("2013-06-02",)], want[()]), compiled.sql
    assert all(D.fromisoformat(k[0]).isoweekday() == 7 for k in got), got.keys()


def test_year_to_date_runs_from_the_fiscal_year_start(adventureworks: Any) -> None:
    layer, metrics = _fiscal_layer()
    ytd = a.to_date(
        c.Period(D(2013, 3, 1), D(2013, 4, 1)), "year", metrics["DOANH_THU_INTERNET"], JULY
    )
    assert ytd.start == D(2012, 7, 1)
    compiled = a.compile_running(
        c.Request(
            metrics["DOANH_THU_INTERNET"],
            group_by=["ngay_dat_internet"],
            period=c.Period(D(2013, 3, 1), D(2013, 4, 1)),
            grain="month",
        ),
        layer,
        dialect="postgres",
        restart="year",
    )
    reference = """
        SELECT '2013-03', sum(sales_amount) FILTER (WHERE order_date >= date '2013-03-01'),
               sum(sales_amount)
        FROM fact_internet_sales
        WHERE order_date >= date '2012-07-01' AND order_date < date '2013-04-01'"""
    got, want = _rows(adventureworks, compiled.sql), _rows(adventureworks, reference)
    assert got.keys() == want.keys(), compiled.sql
    assert all(_close(got[k], want[k]) for k in want), (got, want, compiled.sql)
