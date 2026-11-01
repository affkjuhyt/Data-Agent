"""The metrics of DS-AW (deploy/docker/semantic/ds-aw.yaml) compile, and each compiled result
reconciles with SQL written by hand on AdventureWorksDW as `make adventureworks` loads it
(tests/e2e/adventureworks, fetched once into its .cache/ at the pinned commit).

The reference SQL reads the tables directly, as an analyst would write the definition, so a
difference is the compiler's (a fan-out through the snowflaked product tables, a stock balance
summed over days, a finance scenario left in) or the model's, never both the same way.
"""

from __future__ import annotations

import datetime
import decimal
import importlib.util
import io
import math
import os
import pathlib
import re
import uuid
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import pytest
import yaml

from services.worker_agent.semantic import compiler as c

pytestmark = [pytest.mark.integration]

REPO = pathlib.Path(__file__).parents[3]
MODEL = REPO / "deploy" / "docker" / "semantic" / "ds-aw.yaml"
LOADER = REPO / "tests" / "e2e" / "adventureworks" / "load.py"
CACHE = pathlib.Path(os.environ.get("AW_CACHE") or LOADER.with_name(".cache"))
DATABASE = "adventureworks_test"
_AGGS = {"sum": "SUM", "count": "COUNT", "avg": "AVG", "min": "MIN", "max": "MAX"}

_spec = importlib.util.spec_from_file_location("aw_load", LOADER)
assert _spec is not None and _spec.loader is not None
aw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(aw)

Y2013 = c.Period(datetime.date(2013, 1, 1), datetime.date(2014, 1, 1))
DEC2013 = c.Period(datetime.date(2013, 12, 1), datetime.date(2014, 1, 1))
MAY2014 = c.Period(datetime.date(2014, 5, 1), datetime.date(2014, 6, 1))
IN_2013 = "order_date >= DATE '2013-01-01' AND order_date < DATE '2014-01-01'"

INTERNET_BY_CATEGORY = f"""
    SELECT pc.english_product_category_name AS k, SUM(f.sales_amount) AS v
    FROM fact_internet_sales f
    JOIN dim_product p ON p.product_key = f.product_key
    JOIN dim_product_subcategory ps ON ps.product_subcategory_key = p.product_subcategory_key
    JOIN dim_product_category pc ON pc.product_category_key = ps.product_category_key
    WHERE f.{IN_2013} GROUP BY 1"""
RESELLER_BY_CATEGORY = INTERNET_BY_CATEGORY.replace("fact_internet_sales", "fact_reseller_sales")
# The last day of December with stock, per category; the balance of that one day.
STOCK_DEC = """
    SELECT {group}SUM({value})
    FROM fact_product_inventory i
    JOIN dim_product p ON p.product_key = i.product_key
    LEFT JOIN dim_product_subcategory ps ON ps.product_subcategory_key = p.product_subcategory_key
    LEFT JOIN dim_product_category pc ON pc.product_category_key = ps.product_category_key
    WHERE i.movement_date = (SELECT max(movement_date) FROM fact_product_inventory
                             WHERE movement_date >= DATE '2013-12-01' AND movement_date < DATE '2014-01-01')
    {group_by}"""
FINANCE = """
    SELECT a.account_type, SUM(f.amount)
    FROM fact_finance f
    JOIN dim_scenario s ON s.scenario_key = f.scenario_key
    JOIN dim_account a ON a.account_key = f.account_key
    WHERE s.scenario_name = '{scenario}' AND f.date >= DATE '2013-01-01' AND f.date < DATE '2014-01-01'
    GROUP BY 1"""

# (metric, group by, period, grain, reference SQL: group columns then the value)
CASES: list[Any] = [
    (
        "DOANH_THU",
        [],
        Y2013,
        None,
        f"SELECT (SELECT SUM(sales_amount) FROM fact_internet_sales WHERE {IN_2013})"
        f" + (SELECT SUM(sales_amount) FROM fact_reseller_sales WHERE {IN_2013})",
    ),
    pytest.param(
        "DOANH_THU",
        ["nhom_hang"],
        Y2013,
        None,
        f"SELECT k, SUM(v) FROM ({INTERNET_BY_CATEGORY} UNION ALL {RESELLER_BY_CATEGORY}) t GROUP BY k",
        # A metric over two tables adds MAX() of each side, so a group one side has no row for
        # (Components: sold to resellers only) comes out NULL instead of the other side's sum.
        marks=pytest.mark.xfail(
            strict=True, reason="compiler: a group missing on one side of a cross-table sum is NULL"
        ),
        id="DOANH_THU-nhom_hang",
    ),
    ("DOANH_THU_INTERNET", ["nhom_hang"], Y2013, None, INTERNET_BY_CATEGORY),
    (
        "DOANH_THU_INTERNET",
        ["ngay_dat_internet"],
        Y2013,
        "month",
        f"SELECT to_char(order_date, 'YYYY-MM'), SUM(sales_amount) FROM fact_internet_sales"
        f" WHERE {IN_2013} GROUP BY 1",
    ),
    (
        "DOANH_THU_INTERNET",
        ["nam_tai_chinh"],
        None,
        None,
        "SELECT d.fiscal_year, SUM(f.sales_amount) FROM fact_internet_sales f"
        " JOIN dim_date d ON d.date_key = f.order_date_key GROUP BY 1",
    ),
    (
        "DOANH_THU_INTERNET",
        ["quoc_gia_khach"],
        Y2013,
        None,
        "SELECT g.english_country_region_name, SUM(f.sales_amount) FROM fact_internet_sales f"
        " JOIN dim_customer cu ON cu.customer_key = f.customer_key"
        f" JOIN dim_geography g ON g.geography_key = cu.geography_key WHERE f.{IN_2013} GROUP BY 1",
    ),
    (
        "DOANH_THU_DAI_LY",
        ["loai_dai_ly"],
        Y2013,
        None,
        "SELECT r.business_type, SUM(f.sales_amount) FROM fact_reseller_sales f"
        f" JOIN dim_reseller r ON r.reseller_key = f.reseller_key WHERE f.{IN_2013} GROUP BY 1",
    ),
    (
        "LAI_GOP_INTERNET",
        ["khu_vuc"],
        Y2013,
        None,
        "SELECT t.sales_territory_group, SUM(f.sales_amount - f.total_product_cost)"
        " FROM fact_internet_sales f"
        " JOIN dim_sales_territory t ON t.sales_territory_key = f.sales_territory_key"
        f" WHERE f.{IN_2013} GROUP BY 1",
    ),
    (
        "BIEN_GOP_INTERNET",
        ["nhom_hang"],
        Y2013,
        None,
        INTERNET_BY_CATEGORY.replace(
            "SUM(f.sales_amount) AS v",
            "SUM(f.sales_amount - f.total_product_cost) / SUM(f.sales_amount) * 100 AS v",
        ),
    ),
    (
        "LAI_GOP_DAI_LY",
        [],
        Y2013,
        None,
        f"SELECT SUM(sales_amount - total_product_cost) FROM fact_reseller_sales WHERE {IN_2013}",
    ),
    (
        "SO_DON_INTERNET",
        ["quoc_gia_ban"],
        Y2013,
        None,
        "SELECT t.sales_territory_country, COUNT(DISTINCT f.sales_order_number)"
        " FROM fact_internet_sales f"
        " JOIN dim_sales_territory t ON t.sales_territory_key = f.sales_territory_key"
        f" WHERE f.{IN_2013} GROUP BY 1",
    ),
    (
        "GIA_TRI_DON_TB_INTERNET",
        [],
        Y2013,
        None,
        "SELECT SUM(sales_amount) / COUNT(DISTINCT sales_order_number) FROM fact_internet_sales"
        f" WHERE {IN_2013}",
    ),
    (
        "SO_LUONG_BAN_INTERNET",
        ["tieu_nhom"],
        Y2013,
        None,
        "SELECT ps.english_product_subcategory_name, SUM(f.order_quantity)"
        " FROM fact_internet_sales f JOIN dim_product p ON p.product_key = f.product_key"
        " JOIN dim_product_subcategory ps ON ps.product_subcategory_key = p.product_subcategory_key"
        f" WHERE f.{IN_2013} GROUP BY 1",
    ),
    (
        "SO_LUONG_BAN_DAI_LY",
        [],
        Y2013,
        None,
        f"SELECT SUM(order_quantity) FROM fact_reseller_sales WHERE {IN_2013}",
    ),
    (
        "TON_KHO",
        ["nhom_hang"],
        DEC2013,
        None,
        STOCK_DEC.format(
            group="pc.english_product_category_name, ",
            value="i.units_balance",
            group_by="GROUP BY 1",
        ),
    ),
    (
        "GIA_TRI_TON_KHO",
        [],
        DEC2013,
        None,
        STOCK_DEC.format(group="", value="i.units_balance * i.unit_cost", group_by=""),
    ),
    (
        "CHI_TIEU_DOANH_SO",
        [],
        Y2013,
        None,
        "SELECT SUM(sales_amount_quota) FROM fact_sales_quota"
        " WHERE date >= DATE '2013-01-01' AND date < DATE '2014-01-01'",
    ),
    (
        "SO_CUOC_GOI",
        ["ca_lam"],
        MAY2014,
        None,
        "SELECT shift, SUM(calls) FROM fact_call_center"
        " WHERE date >= DATE '2014-05-01' AND date < DATE '2014-06-01' GROUP BY 1",
    ),
    (
        "SU_CO_TREN_CUOC_GOI",
        [],
        MAY2014,
        None,
        "SELECT SUM(issues_raised)::numeric / SUM(calls) * 100 FROM fact_call_center"
        " WHERE date >= DATE '2014-05-01' AND date < DATE '2014-06-01'",
    ),
    ("TAI_CHINH_THUC_TE", ["loai_tai_khoan"], Y2013, None, FINANCE.format(scenario="Actual")),
    ("TAI_CHINH_NGAN_SACH", ["loai_tai_khoan"], Y2013, None, FINANCE.format(scenario="Budget")),
]


def _values(case: Any) -> tuple[Any, ...]:
    return tuple(case.values) if hasattr(case, "values") else tuple(case)


def _id(case: tuple[Any, ...]) -> str:
    return f"{case[0]}-{'-'.join(case[1]) or 'total'}"


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
    datasets: dict[str, uuid.UUID] = {}
    measures: dict[str, c.Measure] = {}
    dims: dict[str, c.Dimension] = {}
    for sm in model["semantic_models"]:
        entity = uuid.uuid4()
        schema, table = sm["model"].split(".")
        datasets[sm["model"]] = uuid.uuid4()
        tables[entity] = c.Table(datasets[sm["model"]], schema, table)
        for m in sm.get("measures", ()):
            measures[m["name"]] = c.Measure(
                m["name"], _measure(m), entity, m.get("additivity", "full")
            )
        for d in sm.get("dimensions", ()):
            dims[d["name"]] = c.Dimension(
                uuid.uuid4(), d["name"], d["label"], d["expr"], entity, d["type"] == "time"
            )
    # Every declared relationship approved, as a steward would after the import.
    joins = tuple(
        c.Join(
            datasets[r["from"]],
            tuple(r["from_columns"]),
            datasets[r["to"]],
            tuple(r["to_columns"]),
        )
        for r in model["relationships"]
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
    return c.Layer(measures, dims, tables, joins), metrics


def _load(dsn: str) -> None:
    aw.fetch(CACHE)
    script = (CACHE / "instawdbdw.sql").read_text(encoding="utf-8")
    found = aw.tables(script)
    conn = psycopg2.connect(dsn)
    try:
        with conn, conn.cursor() as cur:
            for name, columns in found.items():
                body = ", ".join(f"{aw.snake(col)} {t}" for col, t, _ in columns)
                cur.execute(f"CREATE TABLE {aw.snake(name)} ({body})")
                data = (CACHE / f"{name}.csv").read_bytes()
                text = "".join(line + "\n" for line in aw.rows(data, name, columns))
                cur.copy_expert(f"COPY {aw.snake(name)} FROM STDIN", io.StringIO(text))
            for statement in aw.constraints(script):
                cur.execute(statement)
    finally:
        conn.close()


@pytest.fixture(scope="module")
def adventureworks(request: pytest.FixtureRequest) -> Iterator[Any]:
    admin = os.environ.get("DATAAGENT_TEST_ADMIN_DSN")
    if not admin:
        admin = request.getfixturevalue("postgres_container")["dsn"]
    server = psycopg2.connect(admin)
    server.autocommit = True
    with server.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {DATABASE}")
        cur.execute(f"CREATE DATABASE {DATABASE}")
    dsn = urlunsplit(urlsplit(admin)._replace(path=f"/{DATABASE}"))
    _load(dsn)
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    yield conn
    conn.close()
    with server.cursor() as cur:
        cur.execute(f"DROP DATABASE {DATABASE}")
    server.close()


def _rows(conn: Any, sql: str) -> dict[tuple[str, ...], decimal.Decimal | float | None]:
    with conn.cursor() as cur:
        cur.execute(sql)
        return {tuple(str(v) for v in r[:-1]): r[-1] for r in cur.fetchall()}


def _close(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-6)


def test_every_metric_of_the_model_is_reconciled_at_least_once() -> None:
    _, metrics = _layer()
    assert {_values(case)[0] for case in CASES} == set(metrics)


def test_the_load_matches_the_published_totals(adventureworks: Any) -> None:
    # Microsoft's own figures for the warehouse: a conversion that drops or doubles rows moves them.
    assert _rows(adventureworks, "SELECT SUM(sales_amount) FROM fact_internet_sales") == {
        (): decimal.Decimal("29358677.2207")
    }
    assert _rows(adventureworks, "SELECT SUM(sales_amount) FROM fact_reseller_sales") == {
        (): decimal.Decimal("80450596.9823")
    }


@pytest.mark.parametrize(
    ("code", "group_by", "period", "grain", "reference"),
    [case if hasattr(case, "values") else pytest.param(*case, id=_id(case)) for case in CASES],
)
def test_the_compiled_metric_reconciles_with_hand_written_sql(
    adventureworks: Any,
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
    got, want = _rows(adventureworks, compiled.sql), _rows(adventureworks, reference)
    assert got.keys() == want.keys(), compiled.sql
    wrong = {k: (got[k], want[k]) for k in want if not _close(got[k], want[k])}
    assert not wrong, f"{wrong}\n{compiled.sql}"


def test_two_date_roles_are_refused_rather_than_guessed() -> None:
    # dim_date reached by the order date and the ship date: the compiler names both and stops.
    layer, metrics = _layer()
    sales = next(t for t in layer.entity_tables.values() if t.name == "fact_internet_sales")
    calendar = next(t for t in layer.entity_tables.values() if t.name == "dim_date")
    ship = c.Join(sales.dataset_id, ("ship_date_key",), calendar.dataset_id, ("date_key",))
    two_roles = c.Layer(layer.measures, layer.dimensions, layer.entity_tables, (*layer.joins, ship))
    with pytest.raises(c.CompileError, match="nhiều cách nối"):
        c.compile_metric(
            c.Request(metrics["DOANH_THU_INTERNET"], group_by=["nam_tai_chinh"]),
            two_roles,
            dialect="postgres",
        )
