from __future__ import annotations

import asyncio
import datetime as dt
import pathlib
import sys
from typing import Any

import pytest

from ._paths import RUNTIME_LAYER_DIRS

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "workflow_runtime")

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (*RUNTIME_LAYER_DIRS,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


def _mod():
    import services.workflow_runtime.application.runtime_services.roi_use_cases as m
    return m


def _metrics(values: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "latency_ms": {"avg": 120.0},
        "error_rate": 0.0,
        "series": values,
    }


async def _async_value(value: Any) -> Any:
    return value


class TestRoiUseCases:
    def test_anomaly_flags_last_day_spike(self, monkeypatch):
        m = _mod()
        series = [
            {"date": f"2026-08-{i:02d}", "conversations": 10, "messages": 100, "tokens": 1000, "errors": 0}
            for i in range(18, 31)
        ] + [{"date": "2026-09-01", "conversations": 10, "messages": 400, "tokens": 1000, "errors": 0}]
        monkeypatch.setattr(m.platform_queries, "platform_metrics", lambda *_a, **_k: _async_value(_metrics(series)))

        out = asyncio.run(m.anomaly_summary("00000000-0000-0000-0000-000000000001"))

        assert out["status"] == "detected"
        assert out["signals"][0]["metric"] == "messages"

    def test_forecast_projects_next_day_from_recent_series(self, monkeypatch):
        m = _mod()
        series = [
            {"date": f"2026-08-{i:02d}", "conversations": i, "messages": i * 10, "tokens": i * 100, "errors": 0}
            for i in range(18, 32)
        ]
        monkeypatch.setattr(m.platform_queries, "platform_metrics", lambda *_a, **_k: _async_value(_metrics(series)))

        out = asyncio.run(m.forecast_summary("00000000-0000-0000-0000-000000000001"))

        assert out["projection_next_day"]["messages"] > 310
        assert out["method"].startswith("linear trend")

    def test_data_quality_reports_failed_traces(self, monkeypatch):
        m = _mod()
        monkeypatch.setattr(m.platform_queries, "platform_metrics", lambda *_a, **_k: _async_value({**_metrics([]), "error_rate": 0.2}))
        monkeypatch.setattr(m.platform_queries, "list_traces", lambda *_a, **_k: _async_value({
            "items": [
                {"tool_name": "text_to_sql", "status": "success", "latency_ms": 20},
                {"tool_name": "text_to_sql", "status": "failed", "latency_ms": None},
            ]
        }))

        out = asyncio.run(m.data_quality_summary("00000000-0000-0000-0000-000000000001"))

        assert out["status"] == "needs_attention"
        assert {issue["rule"] for issue in out["issues"]} >= {"trace_success", "error_rate"}

    def test_due_schedule_runs_and_updates_last_run(self, monkeypatch):
        m = _mod()
        saved: dict[str, Any] = {
            "schedules": [{
                "id": "s1",
                "name": "DQ daily",
                "cron": "0 0 * * *",
                "enabled": True,
                "report_kind": "data_quality",
                "last_run": "2026-08-31T00:00:00+00:00",
            }],
            "delivery_channels": [],
            "delivery_outbox": [],
        }

        async def get_setting(_tenant_id: str, key: str):
            return {"key": key, "value": saved.get(key), "updated_at": None}

        async def put_setting(_tenant_id: str, key: str, value: Any, _updated_by: str | None):
            saved[key] = value
            return {"key": key, "value": value, "updated_at": None}

        async def run_use_case(_tenant_id: str, kind: str, *, updated_by: str | None = None):
            return {"report": {"kind": kind, "title": "DQ"}, "delivery_attempts": []}

        monkeypatch.setattr(m.platform_queries, "get_setting", get_setting)
        monkeypatch.setattr(m.platform_queries, "put_setting", put_setting)
        monkeypatch.setattr(m, "run_use_case", run_use_case)

        out = asyncio.run(m.run_due_schedules(
            "00000000-0000-0000-0000-000000000001",
            now=dt.datetime(2026, 9, 1, 0, 1, tzinfo=dt.UTC),
        ))

        assert out["checked"] == 1
        assert len(out["ran"]) == 1
        assert saved["schedules"][0]["last_status"] == "success"
