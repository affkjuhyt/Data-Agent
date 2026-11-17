"""The catalogue's rules for exploratory SQL (sqlgen/audit_rules.py, AE-07) never refuse the right
statement: every reference SQL of the DS-AW eval set (deploy/docker/eval/ds-aw-cases.yaml, each
reconciled on the real data by test_adventureworks_reconciliation.py) passes the checks with the
context the question gets from DS-AW's own model (deploy/docker/semantic/ds-aw.yaml).

This is the false-refusal half of PoC-3 (docs/blueprint/OPEN_SOURCE_RESEARCH.md §4); the misses the
rules are there to catch are in tests/unit/worker_agent/test_audit_rules.py. The space is built as
the loader would see it after the YAML import: its metrics, dimensions and synonyms, and the values
of its categorical dimensions read from the data as the profile's value index would hold them.
"""

from __future__ import annotations

import datetime
import pathlib
import uuid
from typing import Any

import pytest
import sqlglot
import yaml
from sqlglot import exp

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
from services.worker_agent.understand.text import norm

pytestmark = [pytest.mark.integration]

REPO = pathlib.Path(__file__).parents[3]
MODEL = REPO / "deploy" / "docker" / "semantic" / "ds-aw.yaml"
EVAL_CASES = REPO / "deploy" / "docker" / "eval" / "ds-aw-cases.yaml"
# The last month AdventureWorksDW has sales for; the eval questions name their own periods.
TODAY = datetime.date(2014, 6, 30)
_AGGS = {"sum": "SUM", "count": "COUNT", "avg": "AVG", "min": "MIN", "max": "MAX"}
_TYPES = {"integer": "integer", "bigint": "integer", "smallint": "integer", "date": "date"}


def _measure(m: dict[str, Any]) -> str:
    if m.get("agg") == "count_distinct":
        return f"COUNT(DISTINCT {m['expr']})"
    return f"{_AGGS[m.get('agg', 'sum')]}({m['expr']})"


def _space(conn: Any) -> SpaceLayer:
    model = yaml.safe_load(MODEL.read_text(encoding="utf-8"))
    tables: dict[uuid.UUID, c.Table] = {}
    measures: dict[str, c.Measure] = {}
    dims: dict[str, c.Dimension] = {}
    values: list[ValueEntry] = []
    for sm in model["semantic_models"]:
        entity = uuid.uuid4()
        schema, table = sm["model"].split(".")
        tables[entity] = c.Table(uuid.uuid4(), schema, table)
        for m in sm.get("measures", ()):
            if m.get("agg") == "last":
                continue
            measures[m["name"]] = c.Measure(
                m["name"], _measure(m), entity, m.get("additivity", "full")
            )
        for d in sm.get("dimensions", ()):
            dims[d["name"]] = c.Dimension(
                uuid.uuid4(), d["name"], d["label"], d["expr"], entity, d["type"] == "time"
            )
            if d["type"] == "categorical":
                with conn.cursor() as cur:
                    cur.execute(f'SELECT DISTINCT "{d["expr"]}"::text FROM {sm["model"]} LIMIT 501')
                    found = [r[0] for r in cur.fetchall() if r[0]]
                if len(found) <= 500:
                    values += [ValueEntry(d["name"], v, v) for v in found]
    metric_ids = {m["name"]: uuid.uuid4() for m in model["metrics"]}
    versions = {
        metric_ids[m["name"]]: c.MetricVersion(
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
        )
        for m in model["metrics"]
    }
    synonyms: dict[str, tuple[str, ...]] = {}
    for s in model.get("synonyms", ()):
        if "metric" in s:
            synonyms[norm(s["phrase"])] = ("metric", str(metric_ids[s["metric"]]))
        elif "dimension" in s and s["dimension"] in dims:
            synonyms[norm(s["phrase"])] = ("dimension", str(dims[s["dimension"]].id))
    vocab = Vocabulary(
        metrics=[
            MetricEntry(metric_ids[m["name"]], m["name"], m["label"], certified=True)
            for m in model["metrics"]
        ],
        dimensions=[DimensionEntry(d.id, d.code, d.name, d.is_time) for d in dims.values()],
        values=values,
        synonyms=synonyms,
    )
    return SpaceLayer(
        vocabulary=vocab,
        layer=c.Layer(measures, dims, tables),
        versions=versions,
        version_rows={},
        datasets={},
        blocked_metric_ids=frozenset(),
    )


def _linked(conn: Any) -> Linked:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'public' ORDER BY table_name, ordinal_position"
        )
        rows = cur.fetchall()
    columns: dict[str, list[Col]] = {}
    for table, column, kind in rows:
        columns.setdefault(table, []).append(Col(column, column, _TYPES.get(kind, "decimal")))
    return Linked(
        [Tbl(uuid.uuid4(), "public", t, t, tuple(cols)) for t, cols in columns.items()], [], []
    )


def _qualified(sql: str) -> str:
    """The reference SQL names its tables bare (the eval database's search path); the checks hold
    a statement to schema.table, as the model is shown."""
    tree = sqlglot.parse_one(sql, read="postgres")
    ctes = {cte.alias for cte in tree.find_all(exp.CTE)}
    for t in tree.find_all(exp.Table):
        if not t.db and t.name not in ctes:
            t.set("db", exp.to_identifier("public"))
    return tree.sql(dialect="postgres")


def test_no_reference_answer_of_the_eval_set_is_refused(adventureworks: Any) -> None:
    space, linked = _space(adventureworks), _linked(adventureworks)
    cases = yaml.safe_load(EVAL_CASES.read_text(encoding="utf-8"))["cases"]
    checked = [case for case in cases if case.get("reference_sql")]
    assert len(checked) >= 40
    refused: dict[str, list[str]] = {}
    held = 0
    for case in checked:
        ctx = audit_rules.context_for(case["question"], space, today=TODAY)
        held += bool(ctx.reads)
        found = problems(_qualified(case["reference_sql"]), linked, "postgres", ctx)
        if found:
            refused[f"{case['id']} {case['question']}"] = found
    print(f"PoC-3 false refusals: {len(refused)}/{len(checked)}; held to a metric: {held}")
    assert refused == {}
    # The rules must actually hold most answers to a metric, or passing proves nothing.
    assert held >= len(checked) * 0.6, (held, len(checked))


IN_2013 = "order_date >= DATE '2013-01-01' AND order_date < DATE '2014-01-01'"


@pytest.mark.parametrize(
    ("question", "sql"),
    [
        # The misses of the DS-AW agent eval (03/11 and 12/11/2026), on the real model.
        (
            "Doanh thu năm 2013",
            "SELECT SUM(sales_amount_quota) FROM public.fact_sales_quota WHERE calendar_year = 2013",
        ),
        (
            "Doanh thu năm 2013",
            f"SELECT SUM(sales_amount) FROM public.fact_reseller_sales WHERE {IN_2013}",
        ),
        (
            "Biên lợi nhuận gộp Internet năm 2013",
            f"SELECT SUM(sales_amount - total_product_cost) FROM public.fact_internet_sales WHERE {IN_2013}",
        ),
        (
            "Số đơn Internet năm 2013",
            f"SELECT COUNT(*) FROM public.fact_internet_sales WHERE {IN_2013}",
        ),
    ],
)
def test_the_recorded_misses_are_refused_on_the_real_model(
    adventureworks: Any, question: str, sql: str
) -> None:
    space, linked = _space(adventureworks), _linked(adventureworks)
    ctx = audit_rules.context_for(question, space, today=TODAY)
    assert problems(sql, linked, "postgres", ctx), sql
