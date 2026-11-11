"""forecast_metric lands mean + intervals on the workspace when a job is ready."""

from __future__ import annotations

import asyncio
import datetime as dt
import decimal
import types
import uuid
from typing import Any

import pytest

from services.worker_agent.agentic.tools.base import ToolContext
from services.worker_agent.agentic.tools.forecast import ForecastMetric, ForecastMetricArgs
from services.worker_agent.agentic.workspace import Workspace

pytestmark = pytest.mark.unit


class _Pred:
    def __init__(self, period: dt.date, point: float) -> None:
        self.period = period
        self.point = decimal.Decimal(str(point))
        self.lo80 = decimal.Decimal(str(point - 1))
        self.hi80 = decimal.Decimal(str(point + 1))
        self.lo95 = decimal.Decimal(str(point - 2))
        self.hi95 = decimal.Decimal(str(point + 2))


class _Job:
    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.status = "ready"
        self.frequency = "month"
        self.cutoff_at = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)
        self.model_version_id = uuid.uuid4()
        self.progress: dict[str, Any] = {}


class _Version:
    algorithm = "ets"


def _ctx() -> ToolContext:
    return ToolContext(
        org_id=uuid.uuid4(),
        space_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        question_id=uuid.uuid4(),
        question="Dự báo doanh thu Internet 3 tháng tới",
        today=dt.date(2026, 11, 5),
        as_of=None,
        purpose=None,
        layer=types.SimpleNamespace(),  # type: ignore[arg-type]
        model=types.SimpleNamespace(),  # type: ignore[arg-type]
        ws=Workspace(),
        step_id="s1",
    )


def test_ready_job_writes_interval_dataset(monkeypatch: pytest.MonkeyPatch) -> None:
    job = _Job()
    preds = [
        _Pred(dt.date(2026, 11, 1), 100),
        _Pred(dt.date(2026, 12, 1), 110),
        _Pred(dt.date(2027, 1, 1), 120),
    ]

    async def fake_resolve(ctx, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["metric"] == "DOANH_THU_INTERNET"
        assert kwargs["horizon"] == 3
        return job, preds, _Version()

    monkeypatch.setattr(
        "services.worker_agent.agentic.tools.forecast._resolve",
        fake_resolve,
    )
    ctx = _ctx()
    result = asyncio.run(
        ForecastMetric().run(
            ctx,
            ForecastMetricArgs(metric="DOANH_THU_INTERNET", horizon=3, frequency="month"),
        )
    )
    assert result.ok
    assert result.datasets
    assert result.detail["forecast_id"] == str(job.id)
    ds = ctx.ws.get(result.datasets[0])
    assert ds is not None
    assert ds.columns == ["period", "mean", "lo80", "hi80", "lo95", "hi95"]
    assert len(ds.rows) == 3
    assert ds.trust == "exploratory"
    assert ds.tool == "forecast_metric"
    assert ds.reading and ds.reading["forecast_id"] == str(job.id)


def test_no_model_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_resolve(ctx, **kwargs):  # type: ignore[no-untyped-def]
        from services.worker_agent.agentic.tools.forecast import _NoModel

        raise _NoModel("không có mô hình dự báo đã phát hành cho chỉ số X")

    monkeypatch.setattr(
        "services.worker_agent.agentic.tools.forecast._resolve",
        fake_resolve,
    )
    result = asyncio.run(
        ForecastMetric().run(_ctx(), ForecastMetricArgs(metric="X", horizon=3))
    )
    assert not result.ok
    assert result.failure == "unavailable"


def test_bad_frequency_is_arguments() -> None:
    result = asyncio.run(
        ForecastMetric().run(
            _ctx(), ForecastMetricArgs(metric="X", horizon=3, frequency="yearly")
        )
    )
    assert not result.ok
    assert result.failure == "arguments"


def test_tool_is_registered() -> None:
    from services.worker_agent.agentic.tools import TOOLS
    from services.worker_agent.agentic.specialists import NUMBERS

    assert "forecast_metric" in TOOLS
    assert "forecast_metric" in NUMBERS.tool_names
