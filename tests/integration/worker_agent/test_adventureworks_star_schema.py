"""Compiler 3 (GEN-22) on AdventureWorksDW: a sale read along the date table it is keyed to, and a
ratio of a flow and a stock, reconciled with SQL written by hand on the tables."""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any

import pytest

from services.worker_agent.semantic import compiler as c
from tests.integration.worker_agent.test_adventureworks_reconciliation import (
    DEC2013,
    Y2013,
    _close,
    _layer,
    _rows,
)

pytestmark = [pytest.mark.integration]


def _date_column(conn: Any) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'dim_date' AND data_type = 'date' ORDER BY ordinal_position"
        )
        return str(cur.fetchone()[0])


def _star(conn: Any) -> tuple[c.Layer, dict[str, c.MetricVersion]]:
    """DS-AW with the internet sale's own order_date taken away: its time is dim_date's day,
    reached through order_date_key, as a star schema keeps it."""
    layer, metrics = _layer()
    calendar = layer.dimensions["nam_tai_chinh"].entity_id
    dims = {k: d for k, d in layer.dimensions.items() if k != "ngay_dat_internet"}
    dims["ngay_lich"] = c.Dimension(
        uuid.uuid4(), "ngay_lich", "Ngày", _date_column(conn), calendar, is_time=True
    )
    return dataclasses.replace(layer, dimensions=dims), metrics


def test_a_sale_is_read_along_the_date_table_it_is_keyed_to(adventureworks: Any) -> None:
    layer, metrics = _star(adventureworks)
    revenue = metrics["DOANH_THU_INTERNET"]
    assert c.time_dimension_of(revenue, layer) == layer.dimensions["ngay_lich"]
    compiled = c.compile_metric(
        c.Request(revenue, group_by=["ngay_lich"], period=Y2013, grain="quarter"),
        layer,
        dialect="postgres",
    )
    assert "dim_date" in compiled.sql
    by_quarter = _rows(adventureworks, compiled.sql)
    want = _rows(
        adventureworks,
        "SELECT SUM(sales_amount) FROM fact_internet_sales"
        " WHERE order_date >= DATE '2013-04-01' AND order_date < DATE '2013-07-01'",
    )
    assert _close(by_quarter[("2013-Q2",)], want[()]), compiled.sql


def test_revenue_over_closing_stock(adventureworks: Any) -> None:
    # A flow and a stock in one ratio: December's internet revenue over the units on hand on its
    # last day, not over the sum of its 31 daily counts.
    layer, metrics = _layer()
    turnover = dataclasses.replace(
        metrics["DOANH_THU_INTERNET"], expression="doanh_thu_internet / so_ton"
    )
    compiled = c.compile_metric(c.Request(turnover, period=DEC2013), layer, dialect="postgres")
    [(got,)] = [(v,) for v in _rows(adventureworks, compiled.sql).values()]
    want = _rows(
        adventureworks,
        "SELECT (SELECT SUM(sales_amount) FROM fact_internet_sales"
        "        WHERE order_date >= DATE '2013-12-01' AND order_date < DATE '2014-01-01')"
        " / (SELECT SUM(units_balance) FROM fact_product_inventory"
        "    WHERE movement_date = DATE '2013-12-31')",
    )
    assert _close(got, want[()]), compiled.sql
