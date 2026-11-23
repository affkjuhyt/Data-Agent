"""An investigation opened from an alert (AL-11, ANALYTICS_SPEC F-20 B.4): a workspace created
from the alert, seeded with its metric, its period and a two-step plan, which nothing runs."""

from __future__ import annotations

from typing import Any

import pytest
from psycopg2.extras import Json

from .test_monitor import Org, client, seed_alert, seed_monitor
from .test_monitor import mon as mon

pytestmark = [pytest.mark.integration, pytest.mark.req("AL-11")]


def body(alert: object, **extra: Any) -> dict[str, Any]:
    return {
        "title": "Điều tra nợ xấu T9",
        "created_from": "alert",
        "created_from_ref": str(alert),
        **extra,
    }


async def test_an_alert_opens_a_workspace_with_its_plan(mon: Org) -> None:  # noqa: F811
    split = mon.seed.row(
        "monitor.monitor",
        org_id=mon.org,
        data_space_id=mon.space,
        owner_id=mon.users["owner"],
        metric_id=mon.metric,
        scope_filter=Json({"split": "vung"}),
        rule_kind="threshold",
        rule=Json({"direction": "above", "value": 5}),
        schedule_cron="0 7 * * *",
    )
    alert = seed_alert(mon, split, period_label="2026-09", summary="Tỷ lệ nợ xấu kỳ 2026-09")
    async with client(mon) as api:
        made = await api.post(
            "/v1/workspaces",
            json=body(alert, params={"note": "giữ lại"}),
            headers=mon.headers("colleague"),
        )
    assert made.status_code == 201, made.text
    ws = made.json()
    assert (ws["created_from"], ws["created_from_ref"]) == ("alert", str(alert))
    assert ws["description"] == "Tỷ lệ nợ xấu kỳ 2026-09"
    params = ws["params"]
    assert params["note"] == "giữ lại"
    assert (params["alert_id"], params["monitor_id"]) == (str(alert), str(split))
    assert (params["metric"], params["period"]) == ("npl_ratio", "2026-09")
    plan = params["investigation"]["plan"]
    assert plan["mode"] == "analyze"
    first, second = plan["steps"]
    assert (first["tool"], second["tool"]) == ("query_metric", "explain_change")
    assert second["depends_on"] == [first["id"]] and first["depends_on"] == []
    assert first["args"] == {
        "metric": "npl_ratio",
        "period": "2026-09",
        "compare_to": "previous_period",
        "group_by": ["vung"],
    }
    # Every dimension the asker may cut by, decided with their rights when it runs.
    assert second["args"]["dimensions"] == []
    assert "2026-09" in params["investigation"]["question"]
    # Nothing runs until the person asks: no tile, no question.
    assert mon.one("SELECT tile_count FROM workspace.workspace WHERE id = %s", ws["id"]) == 0


async def test_an_alert_of_another_space_or_without_a_period_opens_nothing(
    mon: Org,  # noqa: F811
) -> None:
    elsewhere = seed_alert(
        mon, seed_monitor(mon, mon.other, mon.other_metric), period_label="2026-09"
    )
    undated = seed_alert(mon, seed_monitor(mon))
    async with client(mon) as api:
        for ref in (elsewhere, undated):
            refused = await api.post("/v1/workspaces", json=body(ref), headers=mon.headers("owner"))
            assert refused.status_code == 422, refused.text
            assert refused.json()["errors"][0]["field"] == "created_from_ref"
        missing = await api.post(
            "/v1/workspaces",
            json={"title": "Điều tra", "created_from": "alert"},
            headers=mon.headers("owner"),
        )
        assert missing.status_code == 422
