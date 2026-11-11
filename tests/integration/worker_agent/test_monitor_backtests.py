"""The monitor agent backtests a rule on the metric's past periods (UC-08-01 step 3, BR-32), on
the bank of test_answers.py: NPL_RATIO is 0% in 08/2026 and 10% in 09/2026 (both regions at 10%),
DU_NO 500 then 3000."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from services.worker_agent.main import HANDLERS, run_once, sources

from . import test_answers
from .conftest import Seed
from .test_answers import Bank, build

pytestmark = [pytest.mark.integration]

warehouse = test_answers.warehouse

PENDING = {"status": "pending", "periods": 12, "requested_at": "2026-11-11T08:00:00+00:00"}


def watch(
    bank: Bank,
    rule_kind: str,
    rule: dict[str, Any],
    *,
    metric: uuid.UUID | None = None,
    scope: dict[str, Any] | None = None,
    event: str = "monitor.backtest_requested",
) -> uuid.UUID:
    seed = bank.seed
    made = seed.row(
        "monitor.monitor",
        org_id=bank.org,
        data_space_id=bank.space,
        owner_id=bank.user,
        metric_id=metric or bank.npl,
        scope_filter=json.dumps(scope or {}),
        rule_kind=rule_kind,
        rule=json.dumps(rule),
        schedule_cron="0 7 * * *",
        backtest=json.dumps(PENDING),
    )
    seed.row(
        "monitor.outbox",
        org_id=bank.org,
        aggregate_type="monitor",
        aggregate_id=made,
        event_type=event,
        payload=json.dumps({"monitor_id": str(made), "data_space_id": str(bank.space)}),
    )
    return made


async def drain() -> None:
    for source in await sources():
        while await run_once(source, HANDLERS, batch=50):
            pass


def result(seed: Seed, made: uuid.UUID) -> dict[str, Any]:
    return seed.one("SELECT backtest FROM monitor.monitor WHERE id = %s", made)  # type: ignore[return-value]


async def test_a_threshold_backtest_counts_the_periods_it_would_have_fired(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    made = watch(bank, "threshold", {"direction": "above", "value": 2, "early": 1.9})
    await drain()
    out = result(seed, made)
    assert {k: out[k] for k in ("status", "periods", "fired", "fired_at", "grain")} == {
        "status": "done",
        "periods": 2,
        "fired": 1,
        "fired_at": ["2026-09"],
        "grain": "month",
    }
    assert out["requested_at"] == PENDING["requested_at"] and out["requested_periods"] == 12
    # Read in place through the Query engine, as the owner, recorded against the monitor.
    assert seed.one(
        "SELECT caller_kind, status, principal_id::text FROM query.query_run WHERE caller_id = %s",
        made,
    ) == ("eval", "succeeded", str(bank.user))


async def test_a_split_monitor_fires_when_a_member_does(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    split = watch(bank, "threshold", {"direction": "above", "value": 5}, scope={"split": "vung"})
    south = watch(
        bank,
        "threshold",
        {"direction": "above", "value": 5},
        scope={"filters": {"vung": ["Miền Nam"]}},
    )
    await drain()
    assert result(seed, split)["fired_at"] == ["2026-09"]
    # Miền Nam has no loan in August: one period judged.
    assert (result(seed, south)["periods"], result(seed, south)["fired"]) == (1, 1)


async def test_a_change_backtest_compares_each_period_with_the_one_before(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    du_no = seed.one("SELECT id FROM context.metric WHERE org_id = %s AND code = 'DU_NO'", bank.org)
    up = watch(
        bank, "change_vs_previous", {"direction": "up", "value": 5, "base": "month"}, metric=du_no
    )
    down = watch(bank, "change_vs_previous", {"direction": "down", "value": 5}, metric=du_no)
    await drain()
    assert {k: result(seed, up)[k] for k in ("status", "periods", "fired_at")} == {
        "status": "done",
        "periods": 1,
        "fired_at": ["2026-09"],
    }
    assert (result(seed, down)["periods"], result(seed, down)["fired"]) == (1, 0)


async def test_a_seasonal_rule_says_it_is_not_backtested_and_a_bad_rule_fails(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    seasonal = watch(bank, "seasonal_anomaly", {"sensitivity": "medium"})
    broken = watch(bank, "threshold", {"direction": "above"})
    await drain()
    assert result(seed, seasonal)["status"] == "unsupported"
    assert "mùa vụ" in result(seed, seasonal)["reason"]
    assert "fired" not in result(seed, seasonal)
    assert result(seed, broken)["status"] == "failed"
    assert seed.one("SELECT count(*) FROM query.query_run WHERE caller_id = %s", seasonal) == 0


async def test_created_and_updated_run_a_pending_backtest_once(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    made = watch(bank, "threshold", {"direction": "above", "value": 2}, event="monitor.created")
    await drain()
    first = result(seed, made)
    assert first["status"] == "done"
    # A later update that did not touch the rule leaves the result as it is.
    seed.row(
        "monitor.outbox",
        org_id=bank.org,
        aggregate_type="monitor",
        aggregate_id=made,
        event_type="monitor.updated",
        payload=json.dumps({"monitor_id": str(made), "changed": ["schedule_cron"]}),
    )
    await drain()
    assert result(seed, made) == first
    assert seed.one("SELECT count(*) FROM query.query_run WHERE caller_id = %s", made) == 1


async def test_a_new_request_during_the_run_is_not_overwritten(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.worker_agent import monitor_backtests

    bank = build(seed, warehouse, monkeypatch)
    made = watch(bank, "threshold", {"direction": "above", "value": 2})
    newer = {**PENDING, "requested_at": "2026-11-11T09:00:00+00:00"}
    series = monitor_backtests._series

    async def patched_in_between(row: Any) -> Any:
        # The owner changes the rule while the series is read: the api asks again.
        seed.one(
            "UPDATE monitor.monitor SET backtest = %s WHERE id = %s RETURNING id",
            json.dumps(newer),
            made,
        )
        return await series(row)

    monkeypatch.setattr(monitor_backtests, "_series", patched_in_between)
    await drain()
    assert result(seed, made) == newer
