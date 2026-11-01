"""The pilot seed certifies the semantic model of deploy/docker/semantic/ds-bh.yaml (R1-16): the
same 20 metrics, each with the label and formula the yaml gives, so the cluster's DS-BH is the
model test_pilot_reconciliation.py reconciles."""

from __future__ import annotations

import json
import pathlib
import re

import pytest
import yaml

pytestmark = [pytest.mark.unit]

ROOT = pathlib.Path(__file__).resolve().parents[3]
SEED = (ROOT / "deploy" / "docker" / "sql" / "v3-pilot-seed.sql").read_text(encoding="utf-8")
MODEL = yaml.safe_load((ROOT / "deploy" / "docker" / "semantic" / "ds-bh.yaml").read_text())


def _quoted(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def test_the_seed_has_the_yamls_metrics_and_no_other() -> None:
    inserts = re.findall(r"INSERT INTO context\.metric \(.*?ON CONFLICT", SEED, flags=re.S)
    codes = {
        c for block in inserts for c in re.findall(r":space::uuid, '([A-Z][A-Z0-9_]+)'", block)
    }
    assert codes == {m["name"] for m in MODEL["metrics"]}


@pytest.mark.parametrize("metric", MODEL["metrics"], ids=lambda m: m["name"])
def test_each_metric_is_seeded_as_the_yaml_defines_it(metric: dict) -> None:
    assert _quoted(metric["label"]) in SEED
    assert _quoted(metric["expr"]) in SEED
    if metric.get("filters"):
        assert _quoted(json.dumps(metric["filters"], ensure_ascii=False)) in SEED


def test_every_measure_and_dimension_of_the_yaml_is_seeded() -> None:
    for model in MODEL["semantic_models"]:
        for item in model.get("measures", []) + model.get("dimensions", []):
            assert f"'{item['name']}'" in SEED, item["name"]
