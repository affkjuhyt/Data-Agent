"""AGC-09..11: the planner sees where a metric has data and what its description says, an empty
period is planned around, and a check compose cannot pass goes back to the replanner once."""

from __future__ import annotations

import asyncio
import datetime
import types
import uuid

import pytest

from libs import clock
from services.worker_agent.agentic import engine
from services.worker_agent.agentic import plan as p
from services.worker_agent.agentic.workspace import Workspace
from services.worker_agent.semantic import compiler as c
from services.worker_agent.semantic import coverage

pytestmark = pytest.mark.unit

D = datetime.date

# AdventureWorks' actual finance by month at the end of its data: December 2013 holds 27 adjusting
# entries against some 1,150 rows in a whole month.
FINANCE = [
    ("2012-07", 40_000_000.0),
    ("2012-08", 41_000_000.0),
    ("2012-09", 42_000_000.0),
    ("2013-07", 48_000_000.0),
    ("2013-08", 49_800_000.0),
    ("2013-09", 39_713_837.0),
    ("2013-10", 50_198_548.0),
    ("2013-11", 50_992_903.0),
    ("2013-12", -3_102.0),
]


def test_a_last_period_that_is_a_sliver_of_the_ones_before_is_cut_off() -> None:
    cov = coverage.from_rows(FINANCE, "month")
    assert cov.first == D(2012, 7, 1) and cov.last == D(2013, 12, 1)
    assert cov.partial_last and cov.last_complete == D(2013, 11, 1)
    # A month that falls but stays of the same order is a month, not a cut-off one.
    assert not coverage.from_rows([*FINANCE[:-1], ("2013-12", 30_000_000.0)], "month").partial_last
    # Too little history to tell.
    assert not coverage.from_rows(FINANCE[-2:], "month").partial_last
    # A null figure is no period with data; a quarter label is read as its first month.
    cov = coverage.from_rows([("2013-Q3", 5), ("2013-Q4", None)], "quarter")
    assert cov.periods == ((D(2013, 7, 1), 5.0),)


def test_the_planner_is_told_an_asked_quarter_the_data_never_reaches() -> None:
    cov = coverage.from_rows(FINANCE, "month")
    text = coverage.describe(cov, "Số tài chính thực tế", c.Period(D(2026, 10, 1), D(2027, 1, 1)))
    assert "từ 2012-07 đến 2013-12" in text
    assert "kỳ đầy đủ cuối là 2013-11" in text
    assert "nằm sau dữ liệu" in text and "không truy vấn kỳ đó" in text
    # Q4 2013 holds the cut-off December: the latest whole quarter is Q3 2013, and Q3 2012 is
    # whole to compare with.
    assert 'period="2013-Q3"' in text and 'period="2012-Q3"' in text
    asked_month = coverage.suggest(cov, c.Period(D(2026, 10, 1), D(2026, 11, 1)))
    assert asked_month is not None and coverage.iso(asked_month[0]) == "2013-11"
    assert asked_month[1] is None  # no November 2012 in the data
    inside = coverage.describe(cov, "X", c.Period(D(2013, 10, 1), D(2014, 1, 1)))
    assert "nằm sau" not in inside and "không có dữ liệu" not in inside


def _state(ws: Workspace) -> engine.State:
    return engine.State(
        event=types.SimpleNamespace(),  # type: ignore[arg-type]
        question_id=uuid.uuid4(),
        model=types.SimpleNamespace(),  # type: ignore[arg-type]
        ctx=types.SimpleNamespace(  # type: ignore[arg-type]
            ws=ws,
            question="Số tài chính thực tế theo tháng quý 4/2026",
            today=D(2026, 11, 4),
            layer=types.SimpleNamespace(
                vocabulary=types.SimpleNamespace(
                    metrics=[], synonyms={}, dimensions=[], values=[], calendar=clock.DEFAULT
                )
            ),
        ),
        plan=p.Plan(
            goal="g",
            steps=[p.PlanStep(id="s1", tool="query_metric", instruction="x", status="done")],
        ),
    )


def test_an_empty_answer_goes_back_to_the_replanner_once_with_its_reason() -> None:
    ws = Workspace()
    ws.add(title="t", columns=["Kỳ", "Số"], rows=[["2026-Q4", 0]], trust="certified")
    state = _state(ws)
    asyncio.run(engine.compose(state))
    assert not state.handed_back and state.gate_retries == 1
    assert state.feedback is not None and "data_coverage" in state.feedback
    route = engine.GRAPH.edges["compose"]
    assert route(state) == "replan"
    state.feedback = None
    asyncio.run(engine.compose(state))
    assert state.handed_back and route(state) == engine.END


def test_the_catalogue_carries_unit_how_it_adds_up_cuts_and_description() -> None:
    account = types.SimpleNamespace(
        dimension_id=uuid.uuid4(), code="loai_tai_khoan", name="Loại tài khoản", is_time=False
    )
    day = types.SimpleNamespace(dimension_id=uuid.uuid4(), code="ngay", name="Ngày", is_time=True)
    metric_id = uuid.uuid4()
    version = types.SimpleNamespace(
        unit="USD",
        period_semantics="sum_over_period",
        allowed_dimension_ids=frozenset({account.dimension_id, day.dimension_id}),
    )
    entry = types.SimpleNamespace(metric_id=metric_id, name="Số tài chính thực tế", certified=True)
    layer = types.SimpleNamespace(
        vocabulary=types.SimpleNamespace(dimensions=[account, day], metrics=[]),
        versions={metric_id: version},
        version_rows={
            metric_id: types.SimpleNamespace(
                description="Số tiền kịch bản Actual. Chỉ có nghĩa khi tách theo tài khoản."
            )
        },
    )
    state = _state(Workspace())
    state.ctx = types.SimpleNamespace(layer=layer)  # type: ignore[assignment]
    import services.worker_agent.understand.analyst as analyst

    original = analyst._visible
    analyst._visible = lambda _layer: {"TAI_CHINH_THUC_TE": entry}  # type: ignore[assignment]
    try:
        line = engine._catalog(state).splitlines()[1]
    finally:
        analyst._visible = original  # type: ignore[assignment]
    assert line.startswith("TAI_CHINH_THUC_TE: Số tài chính thực tế")
    assert "đơn vị USD" in line and "cộng dồn trong kỳ" in line
    assert "chia theo loai_tai_khoan" in line and "ngay" not in line.split("chia theo")[1]
    assert "Chỉ có nghĩa khi tách theo tài khoản" in line
