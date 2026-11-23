"""A monitor evaluated on its schedule raises alerts (AL-10, ANALYTICS_SPEC F-20), on the bank of
test_answers.py: NPL_RATIO is 0% in 08/2026 and 10% in 09/2026 (both regions at 10%), DU_NO 500
then 3000. Evaluated at 07:00 in Hà Nội on 23/11/2026, 09/2026 is the last complete period with a
figure."""

from __future__ import annotations

import datetime
import decimal
import json
import uuid
from typing import Any

import pytest
from psycopg2.extras import Json

from services.worker_agent import monitor_runs
from services.worker_agent.schedules import targets

from .conftest import Seed
from .test_answers import Bank, _hold_back, build, warehouse  # noqa: F401  (fixture)

pytestmark = [pytest.mark.integration, pytest.mark.req("AL-10")]

AT = datetime.datetime(2026, 11, 23, 0, 0, tzinfo=datetime.UTC)  # 07:00 in Hà Nội


def watch(
    bank: Bank,
    rule_kind: str,
    rule: dict[str, Any],
    *,
    metric: uuid.UUID | None = None,
    scope: dict[str, Any] | None = None,
) -> uuid.UUID:
    return bank.seed.row(
        "monitor.monitor",
        org_id=bank.org,
        data_space_id=bank.space,
        owner_id=bank.user,
        metric_id=metric or bank.npl,
        scope_filter=json.dumps(scope or {}),
        rule_kind=rule_kind,
        rule=json.dumps(rule),
        schedule_cron="0 7 * * *",
    )


def alerts(seed: Seed, made: uuid.UUID) -> list[tuple[Any, ...]]:
    with seed.conn.cursor() as cur:
        cur.execute(
            "SELECT period_label, dedup_key, figures, occurrences, status, value, threshold_value,"
            " summary FROM monitor.alert WHERE monitor_id = %s ORDER BY fired_at",
            (str(made),),
        )
        return list(cur.fetchall())


def fired_events(seed: Seed, made: uuid.UUID) -> int:
    return seed.one(  # type: ignore[return-value]
        "SELECT count(*) FROM monitor.outbox WHERE event_type = 'alert.fired'"
        " AND payload->>'monitor_id' = %s",
        str(made),
    )


async def test_a_threshold_monitor_on_its_schedule_raises_one_alert_for_the_period(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    made = watch(bank, "threshold", {"direction": "above", "value": 5})
    wanted = {t.schedule_id: t for t in await targets.wanted(bank.org)}
    assert wanted[f"monitor:{bank.org}:{made}"].zone == "Asia/Ho_Chi_Minh"

    assert await targets.fire("monitor", bank.org, made, AT + datetime.timedelta(hours=1)) == (
        "not due"
    )
    assert (await targets.fire("monitor", bank.org, made, AT)).startswith("alert ")
    # The same period is not alerted twice, on a retried firing or the next day's.
    assert await targets.fire("monitor", bank.org, made, AT) == "already alerted 2026-09"
    next_day = AT + datetime.timedelta(days=1)
    assert await targets.fire("monitor", bank.org, made, next_day) == "already alerted 2026-09"

    [(period, key, figures, occurrences, status, value, threshold, summary)] = alerts(seed, made)
    assert (period, key, occurrences, status) == ("2026-09", "2026-09", 1, "open")
    [figure] = figures
    assert figure["member"] is None
    assert threshold == 5 and value == decimal.Decimal(figure["value"])
    # Every figure points at the recorded query that read it.
    assert seed.one(
        "SELECT caller_id::text FROM query.query_run WHERE id = %s", figure["query_run_id"]
    ) == str(made)
    assert "2026-09" in summary and "ngưỡng 5" in summary
    assert fired_events(seed, made) == 1


async def test_a_split_monitor_puts_every_member_that_fired_in_one_alert(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    made = watch(bank, "threshold", {"direction": "above", "value": 5}, scope={"split": "vung"})
    await monitor_runs.evaluate(bank.org, made, AT)
    [(_, _, figures, *_)] = alerts(seed, made)
    members = {f["member"] for f in figures}
    assert len(members) == len(figures) >= 2
    assert {f["query_run_id"] for f in figures} != {None}


async def test_a_member_firing_later_joins_the_open_alert_of_its_period(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    made = watch(bank, "threshold", {"direction": "above", "value": 5}, scope={"split": "vung"})
    await monitor_runs.evaluate(bank.org, made, AT)
    [(_, _, figures, *_)] = alerts(seed, made)
    # As if one member had not fired at the first evaluation.
    kept, dropped = figures[:-1], figures[-1]
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE monitor.alert SET figures = %s WHERE monitor_id = %s", (Json(kept), str(made))
        )
    again = await monitor_runs.evaluate(bank.org, made, AT + datetime.timedelta(days=1))
    assert again.endswith("+1")
    [(_, _, merged, occurrences, *_)] = alerts(seed, made)
    assert occurrences == 2
    assert merged[-1]["member"] == dropped["member"]


async def test_a_change_monitor_compares_with_the_period_before(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    du_no = seed.one("SELECT id FROM context.metric WHERE org_id = %s AND code = 'DU_NO'", bank.org)
    up = watch(bank, "change_vs_previous", {"direction": "up", "value": 5}, metric=du_no)
    down = watch(bank, "change_vs_previous", {"direction": "down", "value": 5}, metric=du_no)
    assert (await monitor_runs.evaluate(bank.org, up, AT)).startswith("alert ")
    assert await monitor_runs.evaluate(bank.org, down, AT) == "quiet 2026-09"
    [(period, _, [figure], _, _, value, threshold, _)] = alerts(seed, up)
    assert period == "2026-09" and threshold is None
    assert (figure["value"], figure["compared_to"], figure["change"]) == ("3000", "500", "500")
    assert value == 3000
    assert alerts(seed, down) == []


async def test_a_new_alert_waits_out_the_cooldown(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    made = watch(bank, "threshold", {"direction": "above", "value": 5, "cooldown_hours": 48})
    seed.row(
        "monitor.alert",
        org_id=bank.org,
        monitor_id=made,
        fired_at=AT - datetime.timedelta(hours=30),
        period_label="2026-08",
        summary="Kỳ trước",
        dedup_key="2026-08",
    )
    assert await monitor_runs.evaluate(bank.org, made, AT) == "cooldown 2026-09"
    assert (
        await monitor_runs.evaluate(bank.org, made, AT + datetime.timedelta(days=1))
    ).startswith("alert ")


async def test_held_back_data_and_a_seasonal_rule_raise_nothing(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    seasonal = watch(bank, "seasonal_anomaly", {"sensitivity": "medium"})
    assert await monitor_runs.evaluate(bank.org, seasonal, AT) == "skipped unsupported"
    made = watch(bank, "threshold", {"direction": "above", "value": 5})
    _hold_back(bank)
    assert await monitor_runs.evaluate(bank.org, made, AT) == "skipped QUALITY_BLOCKED"
    assert alerts(seed, made) == [] and alerts(seed, seasonal) == []
    # The gate is asked before the source: nothing was read.
    assert seed.one("SELECT count(*) FROM query.query_run WHERE caller_id = %s", made) == 0
