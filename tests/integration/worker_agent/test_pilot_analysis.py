"""Share of total, accumulations and to-date periods (semantic/analysis.py) on the pilot's data in
Postgres, reconciled with window SQL written by hand on the tables, as the pilot's metrics are in
test_pilot_reconciliation.py."""

from __future__ import annotations

from typing import Any

import pytest

from services.worker_agent.semantic import analysis as a
from services.worker_agent.semantic import compiler as c
from tests.integration.worker_agent.test_pilot_reconciliation import (
    Q3,
    SCHEMA,
    SEPTEMBER,
    D,
    _close,
    _layer,
    _rows,
)

pytestmark = [pytest.mark.integration]
pytest_plugins = ("tests.integration.worker_agent.test_pilot_reconciliation",)

SALES = f"{SCHEMA}.pilot_sales_line"


def _same(got: dict[Any, Any], want: dict[Any, Any], sql: str) -> None:
    assert got.keys() == want.keys(), sql
    wrong = {k: (got[k], want[k]) for k in want if not _close(got[k], want[k])}
    assert not wrong, f"{wrong}\n{sql}"


def test_the_branches_shares_of_q3_revenue(pilot: Any) -> None:
    layer, metrics = _layer()
    compiled = a.compile_share(
        c.Request(metrics["DOANH_THU"], group_by=["chi_nhanh"], period=Q3),
        layer,
        dialect="postgres",
    )
    reference = f"""
        SELECT branch, sum(net_revenue), 100 * sum(net_revenue) / sum(sum(net_revenue)) OVER ()
        FROM {SALES} WHERE sold_on >= date '2026-07-01' AND sold_on < date '2026-10-01'
        GROUP BY branch"""
    _same(_rows(pilot, compiled.sql), _rows(pilot, reference), compiled.sql)
    shares = _rows(pilot, compiled.sql)
    assert sum(float(v) for v in shares.values()) == pytest.approx(100)


def test_revenue_accumulated_by_month_from_april(pilot: Any) -> None:
    layer, metrics = _layer()
    period = c.Period(D(2026, 4, 1), D(2026, 10, 1))
    compiled = a.compile_running(
        c.Request(metrics["DOANH_THU"], group_by=["ngay_ban"], period=period, grain="month"),
        layer,
        dialect="postgres",
    )
    reference = f"""
        SELECT to_char(sold_on, 'YYYY-MM') AS m, sum(net_revenue),
               sum(sum(net_revenue)) OVER (ORDER BY to_char(sold_on, 'YYYY-MM'))
        FROM {SALES} WHERE sold_on >= date '2026-04-01' AND sold_on < date '2026-10-01'
        GROUP BY m"""
    _same(_rows(pilot, compiled.sql), _rows(pilot, reference), compiled.sql)


def test_september_year_to_date_by_month_reads_the_months_before(pilot: Any) -> None:
    layer, metrics = _layer()
    compiled = a.compile_running(
        c.Request(metrics["DOANH_THU"], group_by=["ngay_ban"], period=SEPTEMBER, grain="month"),
        layer,
        dialect="postgres",
        restart="year",
    )
    reference = f"""
        SELECT '2026-09', sum(net_revenue) FILTER (WHERE sold_on >= date '2026-09-01'),
               sum(net_revenue)
        FROM {SALES} WHERE sold_on >= date '2026-01-01' AND sold_on < date '2026-10-01'"""
    _same(_rows(pilot, compiled.sql), _rows(pilot, reference), compiled.sql)


def test_the_year_to_september_is_a_period_the_compiler_answers(pilot: Any) -> None:
    layer, metrics = _layer()
    ytd = a.to_date(SEPTEMBER, "year", metrics["DOANH_THU"])
    compiled = c.compile_metric(
        c.Request(metrics["DOANH_THU"], period=ytd), layer, dialect="postgres"
    )
    reference = f"""
        SELECT sum(net_revenue) FROM {SALES}
        WHERE sold_on >= date '2026-01-01' AND sold_on < date '2026-10-01'"""
    _same(_rows(pilot, compiled.sql), _rows(pilot, reference), compiled.sql)


def test_what_does_not_add_up_is_refused_on_the_pilot() -> None:
    layer, metrics = _layer()
    with pytest.raises(c.CompileError, match="không cộng được"):
        a.compile_share(
            c.Request(metrics["SO_DON"], group_by=["chi_nhanh"], period=Q3),
            layer,
            dialect="postgres",
        )
    stock = next(m for m in metrics.values() if m.period_semantics == "end_of_period")
    with pytest.raises(c.CompileError, match="số dư tại một ngày"):
        a.to_date(SEPTEMBER, "year", stock)
