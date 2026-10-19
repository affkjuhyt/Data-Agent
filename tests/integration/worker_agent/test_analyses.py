"""Share of total and accumulations (semantic/analysis.py) through the question path, on the bank
of test_answers.py: the figure shown is the analysis's own cell, the plain value stays beside it,
and what does not add up is refused before anything runs."""

from __future__ import annotations

from typing import Any

import pytest

from . import test_answers
from .conftest import Seed
from .test_answers import answer_of, build, drain
from .test_follow_ups import plan_of

pytestmark = [pytest.mark.integration]

warehouse = test_answers.warehouse


async def test_the_regions_shares_of_the_september_balance(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    q = bank.ask("Tỷ trọng dư nợ theo vùng tháng 9/2026")
    await drain()
    status, kind, text, figures, _, _ = answer_of(seed, q)
    assert (status, kind) == ("answered", "data"), text
    shown = {
        f["dimensions"]["vung"]: (round(float(f["value"]), 1), f["unit"], f["source"]["col"])
        for f in figures
        if "expr" not in f["source"]
    }
    # Miền Bắc 2.000 and Miền Nam 1.000 of 3.000; the figure is the `share` cell itself.
    assert shown == {"Miền Bắc": (66.7, "%", "share"), "Miền Nam": (33.3, "%", "share")}
    assert "Tỷ trọng" in text

    chips, steps = plan_of(seed, q)
    assert {"kind": "analysis", "value": "share_of_total", "label": "tỷ trọng trên tổng"} in [
        {k: c[k] for k in ("kind", "value", "label")} for c in chips
    ]
    assert steps[0]["args"]["analysis"] == {"kind": "share_of_total", "unit": None}
    sql = seed.one(
        "SELECT sql_executed FROM query.query_run WHERE caller_id = %s ORDER BY started_at", q
    )
    assert "OVER" in sql and '"share"' in sql


async def test_a_share_of_a_ratio_is_refused_before_anything_runs(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    q = bank.ask("Tỷ trọng tỷ lệ nợ xấu theo vùng tháng 9/2026")
    await drain()
    status, kind, text, _, _, _ = answer_of(seed, q)
    assert (status, kind) == ("refused", "refusal"), text
    assert "không cộng được" in text
    assert seed.one("SELECT count(*) FROM query.query_run WHERE caller_id = %s", q) == 0


async def test_a_balance_accumulated_from_the_start_of_the_year_is_refused(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    q = bank.ask("Dư nợ luỹ kế từ đầu năm tháng 9/2026")
    await drain()
    status, kind, text, _, _, _ = answer_of(seed, q)
    assert (status, kind) == ("refused", "refusal"), text
    assert "số dư tại một ngày" in text
    assert seed.one("SELECT count(*) FROM query.query_run WHERE caller_id = %s", q) == 0
