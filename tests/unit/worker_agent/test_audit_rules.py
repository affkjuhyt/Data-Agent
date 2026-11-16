"""Deterministic audit of exploratory SQL against the catalogue (sqlgen/audit_rules.py, AE-07).

The statements below are the misses the DS-AW agent eval recorded (03/11 and 12/11/2026): a sales
quota read as revenue, one channel read as both, gross profit read as gross margin, order lines
counted as orders, a filter dropped. Each must be refused before it runs; the right statement for
the same question must pass, or the rules only trade a wrong answer for no answer.
"""

from __future__ import annotations

import datetime
import uuid

import pytest

from services.worker_agent.semantic import compiler as c
from services.worker_agent.semantic.loader import SpaceLayer
from services.worker_agent.sqlgen import audit_rules
from services.worker_agent.sqlgen.check import problems
from services.worker_agent.sqlgen.schema import Col, Linked, Tbl
from services.worker_agent.understand.resolver import (
    DimensionEntry,
    MetricEntry,
    ValueEntry,
    Vocabulary,
)

pytestmark = [pytest.mark.unit]

TODAY = datetime.date(2014, 6, 30)

INTERNET = Tbl(
    uuid.uuid4(),
    "adventureworks",
    "fact_internet_sales",
    "Bán hàng Internet",
    (
        Col("sales_amount", "Doanh thu", "decimal"),
        Col("total_product_cost", "Giá vốn", "decimal"),
        Col("order_quantity", "Số lượng", "integer"),
        Col("sales_order_number", "Số đơn", "string"),
        Col("order_date", "Ngày đặt", "date"),
        Col("product_category", "Nhóm hàng", "string", samples=("Bikes", "Clothing")),
    ),
)
RESELLER = Tbl(
    uuid.uuid4(),
    "adventureworks",
    "fact_reseller_sales",
    "Bán hàng đại lý",
    (
        Col("sales_amount", "Doanh thu", "decimal"),
        Col("total_product_cost", "Giá vốn", "decimal"),
        Col("order_date", "Ngày đặt", "date"),
    ),
)
QUOTA = Tbl(
    uuid.uuid4(),
    "adventureworks",
    "fact_sales_quota",
    "Chỉ tiêu doanh số",
    (Col("sales_amount_quota", "Chỉ tiêu", "decimal"), Col("calendar_year", "Năm", "integer")),
)
LINKED = Linked([INTERNET, RESELLER, QUOTA], [], [])

ONLINE, SHOP = uuid.uuid4(), uuid.uuid4()
CATEGORY = c.Dimension(uuid.uuid4(), "nhom_hang", "Nhóm hàng", "product_category", ONLINE)
DAY = c.Dimension(uuid.uuid4(), "ngay", "Ngày đặt", "order_date", ONLINE, is_time=True)
MEASURES = {
    "doanh_thu_internet": c.Measure("doanh_thu_internet", "SUM(sales_amount)", ONLINE),
    "lai_gop_internet": c.Measure(
        "lai_gop_internet", "SUM(sales_amount - total_product_cost)", ONLINE
    ),
    "so_don_internet": c.Measure(
        "so_don_internet", "COUNT(DISTINCT sales_order_number)", ONLINE, "none"
    ),
    "doanh_thu_dai_ly": c.Measure("doanh_thu_dai_ly", "SUM(sales_amount)", SHOP),
}
REVENUE, ONLINE_REVENUE, MARGIN, ORDERS = (uuid.uuid4() for _ in range(4))


def _version(code: str, name: str, expression: str, unit: str) -> c.MetricVersion:
    return c.MetricVersion(
        id=uuid.uuid4(),
        metric_code=code,
        display_name=name,
        expression=expression,
        filters=(),
        unit=unit,
        decimals=0,
        period_semantics="sum_over_period",
        allowed_dimension_ids=frozenset({CATEGORY.id, DAY.id}),
        certified=True,
    )


SPACE = SpaceLayer(
    vocabulary=Vocabulary(
        metrics=[
            MetricEntry(REVENUE, "DOANH_THU", "Doanh thu", certified=True),
            MetricEntry(ONLINE_REVENUE, "DOANH_THU_INTERNET", "Doanh thu Internet", True),
            MetricEntry(MARGIN, "BIEN_GOP_INTERNET", "Biên lợi nhuận gộp Internet", True),
            MetricEntry(ORDERS, "SO_DON_INTERNET", "Số đơn Internet", certified=True),
        ],
        dimensions=[DimensionEntry(d.id, d.code, d.name, d.is_time) for d in (CATEGORY, DAY)],
        values=[ValueEntry("nhom_hang", "Bikes", "Bikes")],
    ),
    layer=c.Layer(
        measures=MEASURES,
        dimensions={d.code: d for d in (CATEGORY, DAY)},
        entity_tables={
            ONLINE: c.Table(INTERNET.dataset_id, "adventureworks", "fact_internet_sales"),
            SHOP: c.Table(RESELLER.dataset_id, "adventureworks", "fact_reseller_sales"),
        },
    ),
    versions={
        REVENUE: _version("DOANH_THU", "Doanh thu", "doanh_thu_internet + doanh_thu_dai_ly", "USD"),
        ONLINE_REVENUE: _version(
            "DOANH_THU_INTERNET", "Doanh thu Internet", "doanh_thu_internet", "USD"
        ),
        MARGIN: _version(
            "BIEN_GOP_INTERNET",
            "Biên lợi nhuận gộp Internet",
            "lai_gop_internet / doanh_thu_internet * 100",
            "%",
        ),
        ORDERS: _version("SO_DON_INTERNET", "Số đơn Internet", "so_don_internet", "đơn"),
    },
    version_rows={},
    datasets={},
    blocked_metric_ids=frozenset(),
)

F = "adventureworks.fact_internet_sales"
R = "adventureworks.fact_reseller_sales"
YEAR = "order_date >= DATE '2013-01-01' AND order_date < DATE '2014-01-01'"
BOTH = (
    f"SELECT (SELECT SUM(sales_amount) FROM {F} WHERE {YEAR}) "
    f"+ (SELECT SUM(sales_amount) FROM {R} WHERE {YEAR}) AS doanh_thu"
)


def _found(question: str, sql: str) -> list[str]:
    ctx = audit_rules.context_for(question, SPACE, today=TODAY)
    return problems(sql, LINKED, "postgres", ctx)


def test_the_metric_the_question_names_is_read_from_the_catalogue() -> None:
    ctx = audit_rules.context_for("Doanh thu năm 2013", SPACE, today=TODAY)
    assert ctx.metric == "Doanh thu" and ctx.period and not ctx.ratio
    assert {(r.table, tuple(r.columns)) for r in ctx.reads} == {
        ("fact_internet_sales", ("sales_amount",)),
        ("fact_reseller_sales", ("sales_amount",)),
    }
    margin = audit_rules.context_for("Biên lợi nhuận gộp Internet năm 2013", SPACE, today=TODAY)
    assert margin.ratio
    orders = audit_rules.context_for("Số đơn Internet năm 2013", SPACE, today=TODAY)
    assert [r.distinct for r in orders.reads] == [True]


@pytest.mark.parametrize(
    ("question", "sql", "rule"),
    [
        # R1: the sales quota for revenue (eval 03/11: 29.507.000).
        (
            "Doanh thu năm 2013",
            "SELECT SUM(sales_amount_quota) FROM adventureworks.fact_sales_quota "
            "WHERE calendar_year = 2013",
            "không đọc bảng nào",
        ),
        # R1b: one channel for both (eval 12/11: 4.057.065, one cut of the total).
        ("Doanh thu năm 2013", f"SELECT SUM(sales_amount) FROM {R} WHERE {YEAR}", "bỏ sót"),
        # R1c: right table, another quantity summed.
        (
            "Doanh thu năm 2013",
            BOTH.replace("SUM(sales_amount)", "SUM(order_quantity)", 1).replace(
                f"SELECT SUM(sales_amount) FROM {R}", f"SELECT SUM(total_product_cost) FROM {R}"
            ),
            "đại lượng khác",
        ),
        # R4: gross profit for gross margin.
        (
            "Biên lợi nhuận gộp Internet năm 2013",
            f"SELECT SUM(sales_amount - total_product_cost) FROM {F} WHERE {YEAR}",
            "phép chia",
        ),
        # R5: order lines for orders (eval 12/11: 21.289).
        ("Số đơn Internet năm 2013", f"SELECT COUNT(*) FROM {F} WHERE {YEAR}", "COUNT(DISTINCT"),
        (
            "Số đơn Internet năm 2013",
            f"SELECT COUNT(sales_order_number) FROM {F} WHERE {YEAR}",
            "COUNT(DISTINCT",
        ),
        # R2: the category the question names, dropped (eval 03/11).
        (
            "Doanh thu Internet nhóm hàng Bikes năm 2013",
            f"SELECT SUM(sales_amount) FROM {F} WHERE {YEAR}",
            "Bikes",
        ),
        # R3: the period, dropped.
        ("Số đơn Internet năm 2013", f"SELECT COUNT(DISTINCT sales_order_number) FROM {F}", "kỳ"),
    ],
)
def test_a_statement_that_measures_something_else_is_refused(
    question: str, sql: str, rule: str
) -> None:
    found = _found(question, sql)
    assert any(rule in p for p in found), (sql, found)


@pytest.mark.parametrize(
    ("question", "sql"),
    [
        ("Doanh thu năm 2013", BOTH),
        (
            "Biên lợi nhuận gộp Internet năm 2013",
            f"SELECT SUM(sales_amount - total_product_cost) / SUM(sales_amount) * 100 "
            f"FROM {F} WHERE {YEAR}",
        ),
        (
            "Số đơn Internet năm 2013",
            f"SELECT COUNT(DISTINCT sales_order_number) FROM {F} WHERE {YEAR}",
        ),
        (
            "Doanh thu Internet nhóm hàng Bikes năm 2013",
            f"SELECT SUM(sales_amount) FROM {F} WHERE {YEAR} AND product_category = 'Bikes'",
        ),
        # A cut along the value the question names is not a dropped filter.
        (
            "Doanh thu Internet nhóm hàng Bikes năm 2013",
            f"SELECT product_category, SUM(sales_amount) FROM {F} WHERE {YEAR} "
            "GROUP BY product_category",
        ),
    ],
)
def test_the_right_statement_passes(question: str, sql: str) -> None:
    assert _found(question, sql) == []


def test_without_a_metric_only_the_question_s_own_filters_and_period_hold() -> None:
    # "Doanh thu theo giờ" names no catalogued metric here: nothing to hold the measure to.
    ctx = audit_rules.context_for("Tổng chi phí vận chuyển năm 2013", SPACE, today=TODAY)
    assert ctx.metric is None and ctx.reads == () and ctx.period
    sql = f"SELECT SUM(total_product_cost) FROM {F} WHERE {YEAR}"
    assert problems(sql, LINKED, "postgres", ctx) == []


def test_no_context_means_no_audit() -> None:
    sql = "SELECT SUM(sales_amount_quota) FROM adventureworks.fact_sales_quota"
    assert problems(sql, LINKED, "postgres") == []


def test_the_rules_can_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SQL_AUDIT_RULES", "off")
    assert not audit_rules.enabled()
    monkeypatch.setenv("SQL_AUDIT_RULES", "on")
    assert audit_rules.enabled()
