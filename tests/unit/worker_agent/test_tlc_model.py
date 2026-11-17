"""The DS-TLC model (deploy/docker/semantic/ds-tlc.yaml) compiles: every metric, alone and cut by
each dimension its owner allows, reaches its tables by one path. Reconciling the numbers needs the
9.5 million trips of `make tlc`, so it was done against a load (tlc_trips, 16/11/2026: 16 checks,
every one equal to hand-written SQL) rather than in this suite."""

from __future__ import annotations

import datetime
import pathlib
import uuid
from typing import Any

import pytest
import yaml

from services.worker_agent.semantic import compiler as c

pytestmark = [pytest.mark.unit]

MODEL = pathlib.Path(__file__).parents[3] / "deploy" / "docker" / "semantic" / "ds-tlc.yaml"
_AGGS = {"sum": "SUM", "count": "COUNT", "avg": "AVG", "min": "MIN", "max": "MAX"}
Q1_2024 = c.Period(datetime.date(2024, 1, 1), datetime.date(2024, 4, 1))


def _layer() -> tuple[c.Layer, dict[str, c.MetricVersion], dict[str, Any]]:
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
            expr = f"{_AGGS[m.get('agg', 'sum')]}({m['expr']})"
            measures[m["name"]] = c.Measure(m["name"], expr, entity, m.get("additivity", "full"))
        for d in sm.get("dimensions", ()):
            dims[d["name"]] = c.Dimension(
                uuid.uuid4(), d["name"], d["label"], d["expr"], entity, d["type"] == "time"
            )
    joins = tuple(
        c.Join(
            datasets[r["from"]], tuple(r["from_columns"]), datasets[r["to"]], tuple(r["to_columns"])
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
    return c.Layer(measures, dims, tables, joins), metrics, model


def _cuts() -> list[tuple[str, list[str]]]:
    model = yaml.safe_load(MODEL.read_text(encoding="utf-8"))
    return [
        (m["name"], cut)
        for m in model["metrics"]
        for cut in [[], *[[d] for d in m.get("dimensions", ())]]
    ]


@pytest.mark.parametrize(
    ("code", "cut"), _cuts(), ids=lambda v: "-".join(v) if isinstance(v, list) else v
)
def test_every_metric_compiles_at_every_allowed_cut(code: str, cut: list[str]) -> None:
    layer, metrics, _ = _layer()
    compiled = c.compile_metric(
        c.Request(metrics[code], group_by=cut, period=Q1_2024), layer, dialect="postgres"
    )
    assert "pickup_datetime >=" in compiled.sql


def test_a_code_left_out_of_a_dictionary_stays_a_group() -> None:
    # 140 162 yellow trips of January 2024 have no rate code: they are a group of their own,
    # not dropped by the join, so the groups add up to the total.
    layer, metrics, _ = _layer()
    compiled = c.compile_metric(
        c.Request(metrics["SO_CHUYEN_VANG"], group_by=["gia_cuoc"]), layer, dialect="postgres"
    )
    assert 'LEFT JOIN "public"."rate_code"' in compiled.sql


def test_the_card_tip_rate_reads_card_payments_only() -> None:
    layer, metrics, _ = _layer()
    compiled = c.compile_metric(
        c.Request(metrics["TY_LE_TIP_THE_VANG"], period=Q1_2024), layer, dialect="postgres"
    )
    assert "'Credit card'" in compiled.sql


def test_the_dropoff_zone_is_not_a_second_path_to_the_zones() -> None:
    _, _, model = _layer()
    to_zones = [r for r in model["relationships"] if r["to"] == "public.taxi_zone"]
    assert {(r["from"], tuple(r["from_columns"])) for r in to_zones} == {
        ("public.yellow_trip", ("pu_location_id",)),
        ("public.green_trip", ("pu_location_id",)),
    }
