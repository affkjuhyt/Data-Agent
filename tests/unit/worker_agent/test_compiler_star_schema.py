"""Compiler 3 (GEN-22): typed filters, a division that cannot fail, stock and flow in one ratio,
and a fact read along a date table, run on SQLite with rows whose answers are known."""

from __future__ import annotations

import datetime
import sqlite3
import uuid
from collections.abc import Iterator

import pytest

from services.worker_agent.semantic.compiler import (
    CompileError,
    Dimension,
    Filter,
    Join,
    Layer,
    Measure,
    MetricVersion,
    Period,
    Request,
    Table,
    compile_metric,
    filter_of,
    time_dimension_of,
)

pytestmark = [pytest.mark.unit]

SALES, DATES, STOCK = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
SALE, DATE, INVENTORY = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
DAY = Dimension(uuid.uuid4(), "ngay", "Ngày", "full_date", DATE, is_time=True)
SHIPPED = Dimension(uuid.uuid4(), "ngay_giao", "Ngày giao", "ship_date", SALE, is_time=True)
AMOUNT = Dimension(uuid.uuid4(), "gia_tri", "Giá trị đơn", "amount", SALE, value_type="number")
CHANNEL = Dimension(uuid.uuid4(), "kenh", "Kênh", "channel", SALE)
STOCK_DAY = Dimension(uuid.uuid4(), "ngay_ton", "Ngày tồn", "on_date", INVENTORY, is_time=True)

# Sales are keyed to the date table; the stock table has its own date.
STAR = Layer(
    measures={
        "doanh_thu": Measure("doanh_thu", "SUM(amount)", SALE),
        "so_don": Measure("so_don", "COUNT(*)", SALE),
        "tra_lai": Measure("tra_lai", "SUM(returned)", SALE),
        "ton_kho": Measure("ton_kho", "SUM(units)", INVENTORY, additivity="semi"),
    },
    dimensions={d.code: d for d in (DAY, AMOUNT, CHANNEL, STOCK_DAY)},
    entity_tables={
        SALE: Table(SALES, None, "sales"),
        DATE: Table(DATES, None, "dates"),
        INVENTORY: Table(STOCK, None, "stock"),
    },
    joins=(Join(SALES, ("date_key",), DATES, ("date_key",)),),
)
JAN = Period(datetime.date(2026, 1, 1), datetime.date(2026, 2, 1))


def metric(expression: str, **over: object) -> MetricVersion:
    base: dict[str, object] = {
        "id": uuid.uuid4(),
        "metric_code": "M",
        "display_name": "Chỉ số",
        "expression": expression,
        "filters": (),
        "unit": None,
        "decimals": 2,
        "period_semantics": "sum_over_period",
        "allowed_dimension_ids": frozenset({AMOUNT.id, CHANNEL.id}),
        "certified": True,
    }
    base.update(over)
    return MetricVersion(**base)  # type: ignore[arg-type]


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE dates (date_key INTEGER, full_date TEXT);
        INSERT INTO dates VALUES (1, '2026-01-05'), (2, '2026-01-20'), (3, '2026-02-03');
        CREATE TABLE sales (date_key INTEGER, ship_date TEXT, amount REAL, returned REAL,
                            channel TEXT);
        INSERT INTO sales VALUES
          (1, '2026-01-06', 2000000000, 0, 'web'),
          (1, '2026-01-07', 500000000, 0, 'store'),
          (2, '2026-02-01', 1000000000, 0, NULL),
          (3, '2026-02-04', 3000000000, 0, 'web');
        CREATE TABLE stock (on_date TEXT, units REAL);
        INSERT INTO stock VALUES ('2026-01-15', 40), ('2026-01-31', 50), ('2026-02-28', 70);
        """
    )
    yield conn
    conn.close()


def run(db: sqlite3.Connection, request: Request, layer: Layer = STAR) -> list[tuple[object, ...]]:
    return db.execute(compile_metric(request, layer, dialect="sqlite").sql).fetchall()


def test_a_fact_keyed_to_a_date_table_is_read_along_its_date(db: sqlite3.Connection) -> None:
    m = metric("doanh_thu")
    assert time_dimension_of(m, STAR) == DAY
    # January by the date table's day: the 5th and the 20th, 3.5 billion.
    assert run(db, Request(m, period=JAN)) == [(3_500_000_000,)]
    by_month = run(db, Request(m, group_by=["ngay"], grain="month"))
    assert by_month == [("2026-01", 3_500_000_000), ("2026-02", 3_000_000_000)]


def test_the_versions_time_dimension_is_the_one_read(db: sqlite3.Connection) -> None:
    m = metric("doanh_thu", time_dimension_id=SHIPPED.id)
    layer = Layer(
        STAR.measures,
        {**STAR.dimensions, "ngay_giao": SHIPPED},
        STAR.entity_tables,
        STAR.joins,
    )
    assert time_dimension_of(m, layer) == SHIPPED
    # Shipped in January: the two orders of the 5th; the one of the 20th shipped in February.
    assert run(db, Request(m, period=JAN), layer) == [(2_500_000_000,)]


@pytest.mark.parametrize(
    ("condition", "want"),
    [
        (Filter("gia_tri", ">", (1_000_000_000,)), 5_000_000_000),
        (Filter("gia_tri", ">=", (1_000_000_000,)), 6_000_000_000),
        (Filter("gia_tri", "between", (500_000_000, 1_000_000_000)), 1_500_000_000),
        (Filter("kenh", "is_null"), 1_000_000_000),
        (Filter("kenh", "is_not_null"), 5_500_000_000),
        (Filter("kenh", "not_in", ("web",)), 500_000_000),
    ],
)
def test_typed_conditions(db: sqlite3.Connection, condition: Filter, want: float) -> None:
    assert run(db, Request(metric("doanh_thu"), conditions=[condition])) == [(want,)]


def test_a_metric_filter_compares_too(db: sqlite3.Connection) -> None:
    big = metric("doanh_thu", filters=({"dimension": "gia_tri", "op": ">", "value": 1e9},))
    assert run(db, Request(big)) == [(5_000_000_000,)]
    # A list is membership, as stored filters always read.
    assert filter_of({"dimension": "kenh", "op": "!=", "values": ["web"]}).op == "not_in"


@pytest.mark.parametrize(
    "raw",
    [
        {"dimension": "kenh", "op": "like", "values": ["w%"]},
        {"dimension": "gia_tri", "op": "between", "values": [1]},
        {"dimension": "gia_tri", "op": ">"},
        {"op": "in", "values": ["web"]},
    ],
)
def test_a_filter_the_compiler_cannot_read_is_refused(raw: dict[str, object]) -> None:
    with pytest.raises(CompileError) as raised:
        compile_metric(Request(metric("doanh_thu", filters=(raw,))), STAR, dialect="sqlite")
    assert raised.value.code == "CONTRACT_MISMATCH"


def test_a_ratio_with_no_denominator_is_empty_not_an_error(db: sqlite3.Connection) -> None:
    # No sale was returned: the return rate's denominator would be 0 on every engine that errs.
    out = compile_metric(Request(metric("doanh_thu / tra_lai")), STAR, dialect="sqlite")
    assert "NULLIF(" in out.sql
    assert db.execute(out.sql).fetchall() == [(None,)]


def test_stock_and_flow_are_aggregated_apart(db: sqlite3.Connection) -> None:
    # Revenue over January divided by January's closing stock (the 31st: 50), not by the sum of
    # its two counts (90).
    turnover = metric("doanh_thu / ton_kho")
    out = compile_metric(Request(turnover, period=JAN), STAR, dialect="sqlite")
    assert "UNION ALL" in out.sql
    assert db.execute(out.sql).fetchall() == [(3_500_000_000 / 50,)]


def test_a_balance_read_along_a_date_table_takes_its_last_day(db: sqlite3.Connection) -> None:
    # The fact's last day with data, through the date table: the 20th (one order of 1 billion).
    closing = metric("doanh_thu", period_semantics="end_of_period")
    assert run(db, Request(closing, period=JAN)) == [(1_000_000_000,)]
