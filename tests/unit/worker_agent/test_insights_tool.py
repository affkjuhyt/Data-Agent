"""AL-02: the agentic insights tool reads a dataset by arithmetic, every derived figure with its
expression over the input's members, recomputable from it."""

from __future__ import annotations

import datetime
import decimal
import re
import types
import uuid

import pytest

from services.worker_agent.agentic.tools import TOOLS
from services.worker_agent.agentic.tools.base import ToolContext
from services.worker_agent.agentic.tools.insights import Insights, InsightsArgs
from services.worker_agent.agentic.workspace import Workspace

pytestmark = [pytest.mark.unit, pytest.mark.req("AL-02")]


def _ctx(ws: Workspace) -> ToolContext:
    return ToolContext(
        org_id=uuid.uuid4(),
        space_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        question_id=uuid.uuid4(),
        question="q",
        today=datetime.date(2026, 11, 23),
        as_of=None,
        purpose=None,
        layer=types.SimpleNamespace(),  # type: ignore[arg-type]
        model=types.SimpleNamespace(),  # type: ignore[arg-type]
        ws=ws,
    )


def _recompute(expr: str, measure: str, by_member: dict[str, float]) -> decimal.Decimal:
    """An expression over the input's members, evaluated again from the input alone."""
    from services.worker_agent.answer.insights import evaluate

    names: dict[str, decimal.Decimal] = {}

    def name(m: re.Match[str]) -> str:
        key = f"V{len(names)}"
        names[key] = decimal.Decimal(str(by_member[m.group(1)]))
        return key

    return evaluate(re.sub(re.escape(measure) + r"\[([^\]]+)\]", name, expr), names)


# Revenue by month of 2013, DS-AW's shape: it climbs, then one month stands apart.
MONTHS = [f"2013-{m:02d}" for m in range(1, 13)]
REVENUE = [100, 110, 120, 130, 140, 150, 160, 170, 180, 190, 200, 900]


async def test_over_time_it_reads_changes_slope_streak_and_an_outlying_period() -> None:
    ws = Workspace()
    # Months given out of order, as a ranked reading returns them.
    pairs = list(zip(MONTHS, REVENUE, strict=True))[::-1]
    ds = ws.add(
        title="Doanh thu theo tháng",
        columns=["Tháng", "Doanh thu"],
        rows=[[m, v] for m, v in pairs],
        trust="certified",
        unit="USD",
        reading={"metric_name": "Doanh thu", "additivity": "full"},
    )
    done = await Insights().run(_ctx(ws), InsightsArgs(dataset=ds.id))
    assert done.ok, done.observation
    out = ws.datasets[done.datasets[0]]
    assert out.trust == "derived" and out.reading["insights_of"] == ds.id
    by_member = dict(zip(MONTHS, REVENUE, strict=True))
    labels = [r[0] for r in out.rows]
    # The last change, against the month before (in time order, not as given).
    assert any("2013-12 so với 2013-11" in label for label in labels), labels
    for label, value, expr in out.rows:
        assert expr and "Doanh thu[" in expr
        # Every figure is recomputed from the input's members alone.
        assert _recompute(expr, "Doanh thu", by_member) == value, (label, expr)
    assert "lệch hẳn" in done.observation and "2013-12" in done.observation
    assert "không phải nguyên nhân" in done.observation


REGIONS = {"Bắc": 400, "Trung": 120, "Nam": 300, "Tây Bắc": 90, "Tây Nguyên": 80, "Đông Nam Bộ": 60}


def _regions(additivity: str) -> tuple[Workspace, str]:
    ws = Workspace()
    ds = ws.add(
        title="Doanh thu theo vùng",
        columns=["Vùng", "Doanh thu"],
        rows=[[k, v] for k, v in REGIONS.items()],
        trust="certified",
        unit="USD",
        reading={"metric_name": "Doanh thu", "additivity": additivity},
    )
    return ws, ds.id


async def test_across_members_it_reads_top_gap_times_and_the_share_of_an_additive_metric() -> None:
    ws, ds_id = _regions("full")
    done = await Insights().run(_ctx(ws), InsightsArgs(dataset=ds_id))
    assert done.ok, done.observation
    out = ws.datasets[done.datasets[0]]
    labels = " | ".join(r[0] for r in out.rows)
    assert "tỷ trọng" in labels.lower() or "chiếm" in labels.lower(), labels
    for label, value, expr in out.rows:
        assert _recompute(expr, "Doanh thu", REGIONS) == value, (label, expr)


async def test_a_metric_that_does_not_add_up_gets_no_share() -> None:
    ws, ds_id = _regions("none")
    done = await Insights().run(_ctx(ws), InsightsArgs(dataset=ds_id))
    out = ws.datasets[done.datasets[0]]
    labels = " | ".join(r[0] for r in out.rows).lower()
    assert "tỷ trọng" not in labels and "chiếm" not in labels, labels


async def test_two_cuts_or_no_measure_are_refused_with_a_reason() -> None:
    ws = Workspace()
    two = ws.add(
        title="t",
        columns=["Vùng", "Tháng", "Doanh thu"],
        rows=[["Bắc", "2013-01", 1], ["Nam", "2013-02", 2]],
        trust="certified",
    )
    done = await Insights().run(_ctx(ws), InsightsArgs(dataset=two.id))
    assert not done.ok and "hơn một chiều" in done.observation
    words = ws.add(title="w", columns=["a", "b"], rows=[["x", "y"]], trust="certified")
    done = await Insights().run(_ctx(ws), InsightsArgs(dataset=words.id))
    assert not done.ok and done.failure == "arguments"


def test_the_numbers_specialist_and_the_registry_offer_it() -> None:
    from services.worker_agent.agentic import specialists

    assert "insights" in TOOLS
    assert "insights" in specialists.NUMBERS.tool_names
