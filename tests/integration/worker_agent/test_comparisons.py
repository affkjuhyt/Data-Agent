"""compare_to (R1-06) on the bank of test_answers.py: "so với tháng trước" reads both months in one
statement, and each side reconciles with the same question asked without the comparison."""

from __future__ import annotations

import json
from typing import Any

import pytest

from services.worker_agent.events import OutboxTableSource
from services.worker_agent.main import run_once

from . import test_answers
from .conftest import Seed
from .test_answers import activate, answer_of, build, drain, watch
from .test_follow_ups import plan_of

pytestmark = [pytest.mark.integration]

warehouse = test_answers.warehouse


def rows(figures: list[dict[str, Any]]) -> list[tuple[str, ...]]:
    return [
        (f["dimensions"].get("vung", ""), f["dimensions"]["ky"], f["value"], f["source"]["col"])
        for f in figures
        if "expr" not in f["source"]
    ]


async def test_a_cut_compared_with_the_month_before_reconciles_with_each_month_alone(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    q = bank.ask("Dư nợ theo vùng tháng 9/2026 so với tháng trước")
    september = bank.ask("Dư nợ theo vùng tháng 9/2026")
    august = bank.ask("Dư nợ theo vùng tháng 8/2026")
    await drain()
    status, kind, text, figures, _, _ = answer_of(seed, q)
    assert (status, kind) == ("answered", "data"), text
    assert [(r[0], r[1], float(r[2]) if r[2] else None, r[3]) for r in rows(figures)] == [
        ("Miền Bắc", "tháng 9/2026", 2000.0, "value"),
        ("Miền Bắc", "tháng 8/2026", 500.0, "base_value"),
        ("Miền Nam", "tháng 9/2026", 1000.0, "value"),
        # The South had no balance in August: no figure, and no change invented for it.
        ("Miền Nam", "tháng 8/2026", None, "base_value"),
    ]
    # Each side is what the question without a comparison answers.
    alone = {
        (f["dimensions"]["vung"], label): f["value"]
        for question, label in ((september, "tháng 9/2026"), (august, "tháng 8/2026"))
        for f in answer_of(seed, question)[3]
        if "expr" not in f["source"]
    }
    assert {(r[0], r[1]): r[2] for r in rows(figures) if r[2] is not None} == alone

    derived = {f["key"]: f for f in figures if "expr" in f["source"]}
    assert [(f["source"]["expr"], float(f["value"])) for f in derived.values()] == [
        ("F1 - F2", 1500.0),
        ("(F1 - F2) / F2 * 100", 300.0),
    ]
    assert "tăng" in text and "1.500" in text

    chips, steps = plan_of(seed, q)
    assert {"kind": "compare", "value": "previous_period", "label": "so với tháng 8/2026"} in [
        {k: c[k] for k in ("kind", "value", "label")} for c in chips
    ]
    assert ("period", "tháng 9/2026") in [(c["kind"], c["label"]) for c in chips]
    assert steps[0]["args"]["compare_to"] == "previous_period"
    spec = seed.one("SELECT chart_spec FROM qa.answer WHERE question_id = %s", q)
    assert (spec["type"], spec["x"], spec["series"]) == ("bar", "vung", "ky")


async def test_a_ratio_against_the_same_month_changes_in_points(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    q = bank.ask("Tỷ lệ nợ xấu tháng 9/2026 so với tháng trước")
    await drain()
    status, kind, text, figures, _, _ = answer_of(seed, q)
    assert (status, kind) == ("answered", "data"), text
    shown = {f["dimensions"]["ky"]: float(f["value"]) for f in figures if "expr" not in f["source"]}
    assert shown == {"tháng 9/2026": 10.0, "tháng 8/2026": 0.0}
    derived = [f for f in figures if "expr" in f["source"]]
    # A percentage changes in points, never by a percent of itself.
    assert [(f["unit"], float(f["value"])) for f in derived] == [("điểm %", 10.0)]


async def test_a_watched_metric_keeps_the_compared_periods_in_its_text(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The threshold's reading is added after the comparison's; it does not replace its labels.
    bank = build(seed, warehouse, monkeypatch)
    watch(bank, {"direction": "above", "value": 3, "early": 2})
    q = bank.ask("Tỷ lệ nợ xấu theo vùng tháng 9/2026 so với tháng trước")
    await drain()
    status, kind, text, _, _, _ = answer_of(seed, q)
    assert (status, kind) == ("answered", "data"), text
    assert "tháng 9/2026 so với tháng 8/2026" in text and "Insights(" not in text


async def test_a_comparison_without_a_period_asks_for_one(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    q = bank.ask("Dư nợ so với cùng kỳ năm trước")
    await drain()
    status, kind, text, _, _, _ = answer_of(seed, q)
    assert (status, kind) == ("refused", "refusal"), text
    assert "kỳ cần so sánh" in text
    assert seed.one("SELECT count(*) FROM query.query_run WHERE caller_id = %s", q) == 0


async def test_a_compared_receipt_replays_each_figure_from_its_own_cell(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.worker_agent import replay

    bank = build(seed, warehouse, monkeypatch)
    activate(bank, metrics=("DU_NO",))
    q = bank.ask("Dư nợ theo vùng tháng 9/2026 so với tháng trước")
    await drain()
    receipt = seed.one(
        "SELECT r.id FROM qa.answer_receipt r JOIN qa.answer a ON a.id = r.answer_id "
        "WHERE a.question_id = %s",
        q,
    )
    rid = seed.row(
        "qa.receipt_replay",
        org_id=bank.org,
        receipt_id=receipt,
        requested_by=bank.user,
        mode="figures",
        compiler_version_used="v3-compiler-1",
        status="running",
    )
    seed.row(
        "qa.outbox",
        org_id=bank.org,
        aggregate_type="receipt_replay",
        aggregate_id=rid,
        event_type="receipt_replay.requested",
        payload=json.dumps({"replay_id": str(rid), "requested_by": str(bank.user)}),
    )
    source = OutboxTableSource(schema="qa", event_types=frozenset(replay.HANDLERS))
    while await run_once(source, replay.HANDLERS, batch=50):
        pass
    outcome, results = seed.one(
        "SELECT outcome, figure_results FROM qa.receipt_replay WHERE id = %s", rid
    )
    # F2 is August's value of row 0, not September's of row 1.
    assert outcome == "identical", results
    by_key = {r["figure_key"]: r["replay_value"] for r in results}
    assert float(by_key["F2"]) == 500.0
