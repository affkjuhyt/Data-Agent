"""R1-06 acceptance: the pilot's 20 metrics (deploy/docker/semantic/ds-bh.yaml) compile, and every
compiled result reconciles with SQL written by hand on the pilot's data
(deploy/docker/sql/v3-pilot-seed.sql, same generators, in a schema of the test server).

The reference SQL is independent of the compiler: it reads the tables directly, as an analyst
would write the definition, so a difference is the compiler's (a fan-out, a summed balance, a
missed filter) or the definition's, never both the same way.
"""

from __future__ import annotations

import datetime
import decimal
import math
import pathlib
import re
import uuid
from typing import Any

import psycopg2
import pytest
import yaml

from services.worker_agent.semantic import compiler as c
from services.worker_agent.sqlgen.verified import bind, match
from services.worker_agent.understand.text import find_period

pytestmark = [pytest.mark.integration]

MODEL = pathlib.Path(__file__).parents[3] / "deploy" / "docker" / "semantic" / "ds-bh.yaml"
SEED = pathlib.Path(__file__).parents[3] / "deploy" / "docker" / "sql" / "v3-pilot-seed.sql"
SCHEMA = "pilot_r106"
_AGGS = {"sum": "SUM", "count": "COUNT", "avg": "AVG", "min": "MIN", "max": "MAX"}

DATA = f"""
DROP SCHEMA IF EXISTS {SCHEMA} CASCADE;
CREATE SCHEMA {SCHEMA};
CREATE TABLE {SCHEMA}.pilot_sales_line AS
SELECT s.order_id || '-1' AS line_id, s.order_id, s.sold_on, s.branch, s.category, s.qty,
       (s.qty * s.unit_price)::numeric AS net_revenue,
       round(s.qty * s.unit_price * s.cogs_rate, 0) AS cogs
FROM (
    SELECT 'O-' || b.code || '-' || c.code || '-' || to_char(m.month_start, 'YYYYMM') || '-' || n
             AS order_id,
           (m.month_start + ((n * 3) || ' day')::interval)::date AS sold_on,
           b.name AS branch, c.name AS category, (b.qty_base * n)::integer AS qty, c.unit_price,
           CASE
               WHEN c.name = 'Thời trang' AND m.month_start >= date '2026-09-01'
                    AND b.name <> 'Đà Nẵng' THEN 0.82
               WHEN c.name = 'Thời trang' AND m.month_start >= date '2026-09-01' THEN 0.50
               ELSE 0.68
           END AS cogs_rate
    FROM (VALUES ('HN', 'Hà Nội', 10), ('HCM', 'TP. Hồ Chí Minh', 5), ('DN', 'Đà Nẵng', 1))
             AS b(code, name, qty_base)
    CROSS JOIN (VALUES ('TP', 'Thực phẩm', 80000), ('DM', 'Điện máy', 4000000),
                       ('TT', 'Thời trang', 350000)) AS c(code, name, unit_price)
    CROSS JOIN generate_series(date '2026-04-01', date '2026-09-01', interval '1 month')
             AS m(month_start)
    CROSS JOIN generate_series(1, 4) AS n
) AS s;
CREATE TABLE {SCHEMA}.pilot_receivable AS
SELECT 'IV-' || b.code || '-' || to_char(m.month_start, 'YYYYMM') AS invoice_id,
       'Khách mẫu ' || b.code AS customer, b.name AS branch, m.month_start::date AS issued_on,
       (m.month_start + interval '30 day')::date AS due_on, b.amount::numeric AS amount,
       (CASE WHEN m.month_start < date '2026-08-01' THEN b.amount ELSE 0 END)::numeric AS paid,
       date '2026-09-30' AS as_of_date
FROM (VALUES ('HN', 'Hà Nội', 120000000), ('HCM', 'TP. Hồ Chí Minh', 80000000),
             ('DN', 'Đà Nẵng', 15000000)) AS b(code, name, amount)
CROSS JOIN generate_series(date '2026-04-01', date '2026-09-01', interval '1 month')
         AS m(month_start);
CREATE TABLE {SCHEMA}.pilot_inventory AS
SELECT s.sku, s.sku_name, b.name AS branch, mo.as_of AS as_of_date, 100 AS qty_on_hand,
       s.unit_cost::numeric AS unit_cost
FROM (VALUES ('TP-01', 'Gạo mẫu', 18000), ('DM-01', 'Nồi mẫu', 900000),
             ('TT-01', 'Áo mẫu', 120000)) AS s(sku, sku_name, unit_cost)
CROSS JOIN (VALUES ('Hà Nội'), ('TP. Hồ Chí Minh'), ('Đà Nẵng')) AS b(name)
CROSS JOIN (SELECT (date '2026-05-01' + (m || ' month')::interval - interval '1 day')::date AS as_of
            FROM generate_series(0, 5) AS m) AS mo;
INSERT INTO {SCHEMA}.pilot_inventory
VALUES ('TT-01', 'Áo mẫu', 'Kho Hà Nội', date '2026-09-30', 100, 120000);
CREATE TABLE {SCHEMA}.pilot_period_edition AS
SELECT date_trunc('month', sold_on)::date AS period_month, 'final'::text AS edition, category,
       sum(net_revenue) AS net_revenue, sum(cogs) AS cogs
FROM {SCHEMA}.pilot_sales_line GROUP BY 1, 3;
INSERT INTO {SCHEMA}.pilot_period_edition
SELECT period_month, 'preliminary', category,
       CASE WHEN category = 'Thời trang' THEN round(net_revenue * 1.25, 0) ELSE net_revenue END,
       cogs
FROM {SCHEMA}.pilot_period_edition WHERE period_month = date '2026-09-01' AND edition = 'final';
"""

SALES = f"{SCHEMA}.pilot_sales_line"
STOCK = f"{SCHEMA}.pilot_inventory"
OPEN = f"{SCHEMA}.pilot_receivable"
EDITIONS = f"{SCHEMA}.pilot_period_edition"
SEP = "sold_on >= DATE '2026-09-01' AND sold_on < DATE '2026-10-01'"
NOT_RETIRED = "branch <> 'Kho Hà Nội'"
D = datetime.date
SEPTEMBER = c.Period(D(2026, 9, 1), D(2026, 10, 1))
Q3 = c.Period(D(2026, 7, 1), D(2026, 10, 1))
YEAR = c.Period(D(2026, 1, 1), D(2027, 1, 1))

# (metric, group_by, period, grain, reference SQL: the dimension columns, then the value)
CASES: list[tuple[str, list[str], c.Period | None, str | None, str]] = [
    (
        "DOANH_THU",
        ["chi_nhanh"],
        SEPTEMBER,
        None,
        f"SELECT branch, SUM(net_revenue) FROM {SALES} WHERE {SEP} GROUP BY branch",
    ),
    (
        "GIA_VON",
        ["nhom_hang"],
        SEPTEMBER,
        None,
        f"SELECT category, SUM(cogs) FROM {SALES} WHERE {SEP} GROUP BY category",
    ),
    ("BIEN_GOP", [], YEAR, None, f"SELECT SUM(net_revenue - cogs) FROM {SALES}"),
    (
        "BIEN_GOP_TY_LE",
        ["chi_nhanh"],
        SEPTEMBER,
        None,
        f"SELECT branch, SUM(net_revenue - cogs) / SUM(net_revenue) * 100 FROM {SALES} WHERE {SEP}"
        " GROUP BY branch",
    ),
    (
        "TY_LE_GIA_VON",
        ["ngay_ban"],
        YEAR,
        "month",
        f"SELECT to_char(sold_on, 'YYYY-MM'), SUM(cogs) / SUM(net_revenue) * 100 FROM {SALES} GROUP BY 1",
    ),
    (
        "SO_LUONG_BAN",
        ["nhom_hang"],
        Q3,
        None,
        f"SELECT category, SUM(qty) FROM {SALES} WHERE sold_on >= DATE '2026-07-01'"
        " AND sold_on < DATE '2026-10-01' GROUP BY category",
    ),
    (
        "SO_DON",
        ["chi_nhanh"],
        SEPTEMBER,
        None,
        f"SELECT branch, COUNT(DISTINCT order_id) FROM {SALES} WHERE {SEP} GROUP BY branch",
    ),
    (
        "GIA_TRI_DON_TB",
        ["chi_nhanh"],
        SEPTEMBER,
        None,
        f"SELECT branch, SUM(net_revenue) / COUNT(DISTINCT order_id) FROM {SALES} WHERE {SEP}"
        " GROUP BY branch",
    ),
    (
        "GIA_BAN_TB",
        ["nhom_hang"],
        SEPTEMBER,
        None,
        f"SELECT category, SUM(net_revenue) / SUM(qty) FROM {SALES} WHERE {SEP} GROUP BY category",
    ),
    (
        "DOANH_THU_BQ_NGAY",
        ["chi_nhanh"],
        SEPTEMBER,
        None,
        f"SELECT branch, AVG(day_total) FROM (SELECT branch, sold_on, SUM(net_revenue) AS day_total"
        f" FROM {SALES} WHERE {SEP} GROUP BY branch, sold_on) AS d GROUP BY branch",
    ),
    (
        "TON_KHO",
        ["kho"],
        SEPTEMBER,
        None,
        f"SELECT branch, SUM(qty_on_hand) FROM {STOCK} WHERE as_of_date = DATE '2026-09-30'"
        f" AND {NOT_RETIRED} GROUP BY branch",
    ),
    (
        "TON_KHO",
        ["ngay_ton"],
        Q3,
        "month",
        f"SELECT to_char(as_of_date, 'YYYY-MM'), SUM(qty_on_hand) FROM {STOCK}"
        f" WHERE as_of_date >= DATE '2026-07-01' AND as_of_date < DATE '2026-10-01'"
        f" AND {NOT_RETIRED} GROUP BY 1",
    ),
    (
        "GIA_TRI_TON_KHO",
        ["ma_hang"],
        SEPTEMBER,
        None,
        f"SELECT sku, SUM(qty_on_hand * unit_cost) FROM {STOCK} WHERE as_of_date = DATE '2026-09-30'"
        f" AND {NOT_RETIRED} GROUP BY sku",
    ),
    (
        "TON_KHO_BQ",
        [],
        YEAR,
        None,
        f"SELECT AVG(total) FROM (SELECT as_of_date, SUM(qty_on_hand) AS total FROM {STOCK}"
        f" WHERE {NOT_RETIRED} GROUP BY as_of_date) AS d",
    ),
    (
        "VONG_QUAY_TON_KHO",
        ["ngay_ban"],
        YEAR,
        "month",
        f"SELECT s.m, s.cogs / i.stock FROM (SELECT to_char(sold_on, 'YYYY-MM') AS m, SUM(cogs) AS cogs"
        f" FROM {SALES} GROUP BY 1) AS s LEFT JOIN (SELECT to_char(as_of_date, 'YYYY-MM') AS m,"
        f" SUM(qty_on_hand * unit_cost) AS stock FROM {STOCK} WHERE {NOT_RETIRED} GROUP BY 1) AS i"
        " ON i.m = s.m",
    ),
    (
        "DSO",
        ["ngay_ban"],
        YEAR,
        "month",
        f"SELECT s.m, r.open / s.rev * 30 FROM (SELECT to_char(sold_on, 'YYYY-MM') AS m,"
        f" SUM(net_revenue) AS rev FROM {SALES} GROUP BY 1) AS s LEFT JOIN (SELECT"
        f" to_char(as_of_date, 'YYYY-MM') AS m, SUM(amount - paid) AS open FROM {OPEN} GROUP BY 1) AS r"
        " ON r.m = s.m",
    ),
    (
        "CONG_NO",
        ["chi_nhanh_no"],
        None,
        None,
        f"SELECT branch, SUM(amount - paid) FROM {OPEN} WHERE as_of_date = (SELECT MAX(as_of_date)"
        f" FROM {OPEN}) GROUP BY branch",
    ),
    (
        "CONG_NO_QUA_HAN",
        [],
        SEPTEMBER,
        None,
        f"SELECT SUM(CASE WHEN due_on < as_of_date THEN amount - paid ELSE 0 END) FROM {OPEN}"
        " WHERE as_of_date = DATE '2026-09-30'",
    ),
    (
        "TY_LE_QUA_HAN",
        ["chi_nhanh_no"],
        SEPTEMBER,
        None,
        f"SELECT branch, SUM(CASE WHEN due_on < as_of_date THEN amount - paid ELSE 0 END)"
        f" / SUM(amount - paid) * 100 FROM {OPEN} WHERE as_of_date = DATE '2026-09-30' GROUP BY branch",
    ),
    (
        "SO_HOA_DON_MO",
        [],
        SEPTEMBER,
        None,
        f"SELECT COUNT(*) FROM {OPEN} WHERE amount > paid AND as_of_date = DATE '2026-09-30'",
    ),
    (
        "DOANH_THU_CHOT",
        ["nhom_hang_chot"],
        SEPTEMBER,
        None,
        f"SELECT category, SUM(net_revenue) FROM {EDITIONS} WHERE edition = 'final'"
        " AND period_month = DATE '2026-09-01' GROUP BY category",
    ),
]


def _measure(m: dict[str, Any]) -> str:
    """semantic_yaml.measure_expression, the api's, for the worker side of the file."""
    if re.search(r"\b(?:SUM|COUNT|AVG|MIN|MAX)\s*\(", m["expr"], re.IGNORECASE):
        return str(m["expr"])
    if m.get("agg") == "count_distinct":
        return f"COUNT(DISTINCT {m['expr']})"
    return f"{_AGGS[m.get('agg', 'sum')]}({m['expr']})"


def _layer() -> tuple[c.Layer, dict[str, c.MetricVersion]]:
    model = yaml.safe_load(MODEL.read_text(encoding="utf-8"))
    tables: dict[uuid.UUID, c.Table] = {}
    measures: dict[str, c.Measure] = {}
    dims: dict[str, c.Dimension] = {}
    for sm in model["semantic_models"]:
        entity = uuid.uuid4()
        _, table = sm["model"].split(".")
        tables[entity] = c.Table(uuid.uuid4(), SCHEMA, table)
        for m in sm["measures"]:
            measures[m["name"]] = c.Measure(m["name"], _measure(m), entity)
        for d in sm["dimensions"]:
            dims[d["name"]] = c.Dimension(
                uuid.uuid4(), d["name"], d["label"], d["expr"], entity, d["type"] == "time"
            )
    metrics = {
        m["name"]: c.MetricVersion(
            id=uuid.uuid4(),
            metric_code=m["name"],
            display_name=m["label"],
            expression=m["expr"],
            filters=tuple(m.get("filters", ())),
            unit=m.get("unit"),
            decimals=m.get("decimals", 2),
            period_semantics=m["period_semantics"],
            allowed_dimension_ids=frozenset(dims[d].id for d in m.get("dimensions", ())),
            certified=True,
            time_grain=m.get("time_grain", "day"),
        )
        for m in model["metrics"]
    }
    return c.Layer(measures, dims, tables), metrics


@pytest.fixture(scope="module")
def pilot(ddl_server: dict[str, str]) -> Any:
    conn = psycopg2.connect(ddl_server["dataagent"])
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(DATA)
    yield conn
    with conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA {SCHEMA} CASCADE")
    conn.close()


def _rows(conn: Any, sql: str) -> dict[tuple[str, ...], decimal.Decimal | float | None]:
    with conn.cursor() as cur:
        cur.execute(sql)
        return {tuple(str(v) for v in r[:-1]): r[-1] for r in cur.fetchall()}


def _close(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-6)


def test_the_pilot_has_twenty_metrics_each_reconciled_at_least_once() -> None:
    _, metrics = _layer()
    assert 15 <= len(metrics) <= 25
    assert {case[0] for case in CASES} == set(metrics)


@pytest.mark.parametrize(
    ("code", "group_by", "period", "grain", "reference"),
    CASES,
    ids=[f"{case[0]}-{'-'.join(case[1]) or 'total'}" for case in CASES],
)
def test_the_compiled_metric_reconciles_with_hand_written_sql(
    pilot: Any,
    code: str,
    group_by: list[str],
    period: c.Period | None,
    grain: str | None,
    reference: str,
) -> None:
    layer, metrics = _layer()
    compiled = c.compile_metric(
        c.Request(metrics[code], group_by=group_by, period=period, grain=grain),
        layer,
        dialect="postgres",
    )
    got, want = _rows(pilot, compiled.sql), _rows(pilot, reference)
    assert got.keys() == want.keys(), compiled.sql
    wrong = {k: (got[k], want[k]) for k in want if not _close(got[k], want[k])}
    assert not wrong, f"{wrong}\n{compiled.sql}"


def test_the_traps_give_what_the_policy_says(pilot: Any) -> None:
    layer, metrics = _layer()

    def value(code: str, group_by: list[str], period: c.Period | None) -> dict[Any, Any]:
        sql = c.compile_metric(
            c.Request(metrics[code], group_by=group_by, period=period), layer, dialect="postgres"
        ).sql
        return _rows(pilot, sql)

    # Fashion repriced at Hà Nội in September: the revenue-weighted margin falls from August's.
    august = c.Period(D(2026, 8, 1), D(2026, 9, 1))
    before = value("BIEN_GOP_TY_LE", ["chi_nhanh"], august)[("Hà Nội",)]
    after = value("BIEN_GOP_TY_LE", ["chi_nhanh"], SEPTEMBER)[("Hà Nội",)]
    assert float(after) < float(before)
    # Stock is one month end, not the sum of six, and never counts the retired code.
    assert value("TON_KHO", [], SEPTEMBER) == {(): 900}
    # The final edition of September equals the sales lines; the preliminary one would not.
    final = value("DOANH_THU_CHOT", ["nhom_hang_chot"], SEPTEMBER)
    sales = value("DOANH_THU", ["nhom_hang"], SEPTEMBER)
    assert {k: float(v) for k, v in final.items()} == {k: float(v) for k, v in sales.items()}


def _verified() -> list[tuple[uuid.UUID, str, str]]:
    """The pilot's verified queries as the seed writes them (R1-08)."""
    found = re.findall(
        r"\('(a0000000-0000-0000-0000-000000000c\d\d)', :org::uuid, :space::uuid,\s*'([^']+)',"
        r"\s*\$vq\$(.*?)\$vq\$",
        SEED.read_text(encoding="utf-8"),
        flags=re.S,
    )
    return [(uuid.UUID(i), q, sql) for i, q, sql in found]


def test_the_pilot_has_thirty_verified_queries_each_running_clear_of_the_traps(pilot: Any) -> None:
    examples = _verified()
    assert len(examples) >= 30
    today = D(2026, 10, 30)
    for example_id, question, sql in examples:
        # Each answers its own question, and only it.
        found = match(question, examples, today)
        assert found is not None and found.example_id == example_id, question
        assert "pilot_order_header" not in sql, question  # trap 1: the header join
        bound = bind(sql, find_period(question, today), today).replace("workspace.", f"{SCHEMA}.")
        assert _rows(pilot, bound), question
    stock = next(sql for _, q, sql in examples if q == "Tồn kho cuối kỳ theo chi nhánh")
    # Trap 3: one month end, without the retired "Kho Hà Nội".
    assert _rows(pilot, stock.replace("workspace.", f"{SCHEMA}.")) == {
        ("Hà Nội",): 300,
        ("TP. Hồ Chí Minh",): 300,
        ("Đà Nẵng",): 300,
    }
    # Trap 5: the final edition of September is the sales lines'.
    final = next(sql for _, q, sql in examples if q.startswith("Doanh thu bản chốt chính thức"))
    lines = next(sql for _, q, sql in examples if q == "Doanh thu theo ngành hàng tháng 9/2026")
    sep = find_period("tháng 9/2026", today)

    def run(sql: str) -> dict[Any, float]:
        bound = bind(sql, sep, today).replace("workspace.", f"{SCHEMA}.")
        return {k: float(v) for k, v in _rows(pilot, bound).items()}

    assert run(final) == run(lines)
