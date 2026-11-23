"""EV-03: the reconciliation of DS-TLC against hand-written SQL (`make tlc-reconcile`) covers every
metric of the model, compiles each check, and tells a difference from an equal value."""

from __future__ import annotations

import datetime
import pathlib

import pytest
import yaml

from services.worker_agent.semantic import compiler as c
from services.worker_agent.semantic.reconcile import differences, layer_of

pytestmark = [pytest.mark.unit, pytest.mark.req("EV-03")]

SEMANTIC = pathlib.Path(__file__).parents[3] / "deploy" / "docker" / "semantic"


def _load() -> tuple[dict, dict]:
    model = yaml.safe_load((SEMANTIC / "ds-tlc.yaml").read_text(encoding="utf-8"))
    reference = yaml.safe_load((SEMANTIC / "ds-tlc-reference.yaml").read_text(encoding="utf-8"))
    return model, reference


def test_every_metric_has_a_hand_written_check_and_every_check_compiles() -> None:
    model, reference = _load()
    assert {m["name"] for m in model["metrics"]} == {ch["metric"] for ch in reference["checks"]}
    layer, metrics = layer_of(model)
    period = c.Period(
        datetime.date.fromisoformat(reference["period"]["from"]),
        datetime.date.fromisoformat(reference["period"]["to"]),
    )
    for check in reference["checks"]:
        cut = [check["cut"]] if check.get("cut") else []
        compiled = c.compile_metric(
            c.Request(metrics[check["metric"]], group_by=cut, period=period),
            layer,
            dialect="postgres",
        )
        assert compiled.columns[-1] == "value" and len(compiled.columns) == len(cut) + 1
        # The reference reads the same quarter, never another.
        assert "'2024-01-01'" in check["sql"] and "'2024-04-01'" in check["sql"]


def test_a_difference_is_named_and_an_equal_value_is_not() -> None:
    assert differences({None: 1_000_000.004}, {None: 1_000_000.004}) == []
    assert differences({"Bronx": 0.1 + 0.2}, {"Bronx": 0.3}) == []
    assert differences({"Bronx": 10.0}, {"Bronx": 10.5}) == ["Bronx: compiled 10.0, by hand 10.5"]
    # A member one side has and the other does not, the NULL group included.
    assert differences({"A": 1.0, None: 2.0}, {"A": 1.0}) == ["None: compiled 2.0, by hand absent"]
