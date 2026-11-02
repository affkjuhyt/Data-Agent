"""The dynamic agent's pure parts (worker_agent/agentic/): the plan DAG is validated before it
runs, blocks take their data from datasets, every number in the text is traced, the graph ends."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from services.worker_agent.agentic import blocks as b
from services.worker_agent.agentic import plan as p
from services.worker_agent.agentic import verify
from services.worker_agent.agentic.graph import END, GraphError, StateGraph
from services.worker_agent.agentic.workspace import Workspace

pytestmark = pytest.mark.unit

TOOLS = {"query_metric", "analyze", "search_catalog"}


def _plan(*steps: dict[str, Any]) -> p.Plan:
    return p.Plan(goal="g", steps=[p.PlanStep(**s) for s in steps])


def test_a_plan_with_an_unknown_tool_or_a_forward_dependency_is_refused() -> None:
    with pytest.raises(p.PlanInvalid, match="không có công cụ"):
        p.validate(_plan({"id": "s1", "tool": "drop_table", "instruction": "x"}), TOOLS)
    with pytest.raises(p.PlanInvalid, match="phụ thuộc"):
        p.validate(
            _plan(
                {"id": "s1", "tool": "analyze", "instruction": "x", "depends_on": ["s2"]},
                {"id": "s2", "tool": "query_metric", "instruction": "y"},
            ),
            TOOLS,
        )
    assert p.validate(p.Plan(goal="hi", mode="answer"), TOOLS).mode == "answer"


def test_a_step_whose_dependency_failed_is_skipped() -> None:
    plan = _plan(
        {"id": "s1", "tool": "query_metric", "instruction": "a"},
        {"id": "s2", "tool": "analyze", "instruction": "b", "depends_on": ["s1"]},
        {"id": "s3", "tool": "query_metric", "instruction": "c"},
    )
    first = p.next_ready(plan)
    assert first is not None and first.id == "s1"
    first.status = "failed"
    nxt = p.next_ready(plan)
    assert nxt is not None and nxt.id == "s3"
    assert plan.steps[1].status == "skipped"


def _ws() -> Workspace:
    ws = Workspace()
    ws.add(
        title="Doanh thu theo vùng",
        columns=["Vùng", "Doanh thu"],
        rows=[["Europe", "1500000.50"], ["Pacific", "250000"]],
        trust="certified",
        query_run_ids=["r1"],
        unit="USD",
    )
    return ws


def test_a_chart_and_a_kpi_take_their_figures_from_the_dataset() -> None:
    ws = _ws()
    chart = b.materialize(
        b.BlockDraft(type="chart", dataset="d1", chart_type="bar", x="Vùng", y=["Doanh thu"]),
        ws,
        "b1",
    )
    assert chart["spec"]["data"] == [
        {"Vùng": "Europe", "Doanh thu": 1500000.5},
        {"Vùng": "Pacific", "Doanh thu": 250000.0},
    ]
    assert chart["trust"] == "certified"
    kpi = b.materialize(
        b.BlockDraft(
            type="kpi",
            kpis=[
                b.KpiDraft(
                    label="Pacific",
                    dataset="d1",
                    column="Doanh thu",
                    match_column="Vùng",
                    match_value="Pacific",
                )
            ],
        ),
        ws,
        "b2",
    )
    assert kpi["items"][0]["value"] == 250000.0
    with pytest.raises(b.BlockError):
        b.materialize(b.BlockDraft(type="table", dataset="d9"), ws, "b3")
    with pytest.raises(b.BlockError):
        b.materialize(b.BlockDraft(type="chart", dataset="d1", x="Năm"), ws, "b4")


def test_a_sentence_with_an_untraced_number_is_dropped_and_tags_are_filled() -> None:
    ws = _ws()
    known = verify.facts(ws, "vi")
    tag = next(k for k, f in known.items() if f.value == 250000.0)
    allowed = verify.allowed_numbers("Doanh thu năm 2013 theo vùng?", ws, [])
    checked = verify.fill(
        f"Pacific đạt {{{{{tag}}}}} USD trong năm 2013. Europe tăng 37% so với năm trước.",
        known,
        allowed,
    )
    assert checked.text == "Pacific đạt 250.000 USD trong năm 2013."
    assert checked.dropped == 1
    assert checked.numbers == 1
    unknown = verify.fill("Tổng là {{F999}}.", known, allowed)
    assert unknown.text == "" and unknown.dropped == 1


def test_the_graph_routes_on_the_state_and_always_ends() -> None:
    trail: list[str] = []

    async def inc(state: dict[str, int]) -> None:
        state["n"] += 1

    g: StateGraph[dict[str, int]] = StateGraph()
    g.node("a", inc).route("a", lambda s: "a" if s["n"] < 3 else END)
    state = asyncio.run(g.run({"n": 0}, trail=trail))
    assert state["n"] == 3 and trail == ["a", "a", "a"]
    loop: StateGraph[dict[str, int]] = StateGraph()
    loop.node("a", inc).edge("a", "a")
    with pytest.raises(GraphError):
        asyncio.run(loop.run({"n": 0}, max_steps=5))


def test_the_sandbox_refuses_files_network_and_dunders() -> None:
    from libs.code_sandbox import local as sandbox

    for code in (
        "import os",
        "open('/etc/passwd')",
        "x = d1.__class__",
        "import pandas as pd\npd.read_csv('http://x')",
        "d1.to_csv('a.csv')",
    ):
        with pytest.raises(sandbox.CodeRejected):
            sandbox.check(code)
    sandbox.check("result = d1.groupby('Vùng')['Doanh thu'].sum().sort_values()")


def test_the_sandbox_runs_pandas_over_the_datasets() -> None:
    pytest.importorskip("pandas")
    from libs.code_sandbox import local as sandbox

    done = asyncio.run(
        sandbox.run(
            "result = d1.assign(share=d1['Doanh thu'] / d1['Doanh thu'].sum())",
            {"d1": {"columns": ["Vùng", "Doanh thu"], "rows": [["A", "3"], ["B", "1"]]}},
        )
    )
    assert done.ok, done.error
    assert done.columns == ["Vùng", "Doanh thu", "share"]
    assert done.rows == [["A", 3, 0.75], ["B", 1, 0.25]]
    with pytest.raises(sandbox.CodeRejected):
        asyncio.run(sandbox.run("result = __import__('os')", {}))
    failed = asyncio.run(
        sandbox.run("result = d1['nope']", {"d1": {"columns": ["a"], "rows": [[1]]}})
    )
    assert not failed.ok and failed.error is not None and "KeyError" in failed.error


def test_the_sandbox_refuses_the_ways_round_its_checks() -> None:
    from libs.code_sandbox import local as sandbox

    for code in (
        "from pandas import read_csv",
        "from pandas.io import pickle",
        "import pandas.io.pickle",
        "import numpy as np\nnp.loadtxt('/etc/passwd')",
        "b = __builtins__",
        "from math import __dict__",
        "from json import loads as __x",
        "import numpy as np\nnp.lib.npyio",
        "import datetime\nm = datetime.sys",
        "import collections\nc = collections._sys",
        "import numpy as np\nnp.savetxt('x', [1])",
        "g = (i for i in [1])\nf = g.gi_frame.f_back.f_globals",
    ):
        with pytest.raises(sandbox.CodeRejected):
            sandbox.check(code)


def test_the_sandbox_runs_lazy_library_code_but_no_system_call() -> None:
    pytest.importorskip("pandas")
    from libs.code_sandbox import local as sandbox

    printed = asyncio.run(
        sandbox.run(
            "print(d1.describe())\nresult = d1", {"d1": {"columns": ["a"], "rows": [[1], [2]]}}
        )
    )
    assert printed.ok, printed.error
    assert "mean" in printed.stdout


def test_the_audit_hook_refuses_files_even_past_the_static_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("pandas")
    from libs.code_sandbox import local as sandbox

    monkeypatch.setattr(sandbox, "check", lambda code: None)
    for code in (
        "result = pd.read_csv('/etc/hostname')",
        "result = pd.DataFrame({'a': [1]}).to_csv('/tmp/out.csv')",
        "import collections\nresult = collections._sys.modules['os'].listdir('/')",
    ):
        done = asyncio.run(sandbox.run(code, {}))
        assert not done.ok, code
        assert done.error is not None and "sandbox" in done.error, done.error


def test_a_sandbox_service_without_its_address_is_off_never_in_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.worker_agent.agentic.tools import analyze

    monkeypatch.setenv("AGENT_PYTHON_SANDBOX", "service")
    monkeypatch.delenv("AGENT_SANDBOX_URL", raising=False)
    assert analyze.mode() == "off"
    monkeypatch.setenv("AGENT_SANDBOX_URL", "http://python-sandbox:8140")
    assert analyze.mode() == "service"
    monkeypatch.setenv("AGENT_PYTHON_SANDBOX", "anything")
    assert analyze.mode() == "off"


def test_the_worker_reads_the_sandbox_answer_as_untrusted() -> None:
    from libs.code_sandbox import client

    good = client._shaped({"ok": True, "columns": ["a"], "rows": [[1]], "stdout": "x" * 9000})
    assert good.ok and good.rows == [[1]] and len(good.stdout) == 4000
    for bad in (
        [],
        {"ok": True, "columns": ["a"], "rows": [[1, 2]]},
        {"ok": True, "columns": [1], "rows": []},
        {"ok": True, "columns": ["a"], "rows": [[{"nested": 1}]]},
        {"ok": True, "columns": ["a"], "rows": [[1]] * 2001},
        {"ok": True, "columns": [], "rows": [], "error": {"x": 1}},
    ):
        assert not client._shaped(bad).ok
    assert client._shaped({"ok": "yes", "columns": [], "rows": []}).ok is False


def test_a_year_is_a_period_not_a_member_to_look_up() -> None:
    import datetime
    import types

    from libs import clock
    from services.worker_agent.agentic.tools.catalog import SearchValues, SearchValuesArgs

    ctx = types.SimpleNamespace(
        today=datetime.date(2026, 11, 2),
        layer=types.SimpleNamespace(vocabulary=types.SimpleNamespace(calendar=clock.DEFAULT)),
    )
    found = asyncio.run(
        SearchValues().run(ctx, SearchValuesArgs(text="2013", dimension="calendar_year"))  # type: ignore[arg-type]
    )
    assert found.ok
    assert '"2013-01-01/2014-01-01"' in found.observation


def test_an_analysis_that_fetched_nothing_hands_the_question_back() -> None:
    import types
    import uuid

    from services.worker_agent.agentic import engine

    ws = Workspace()
    ws.add(title="rỗng", columns=["value"], rows=[], trust="certified")
    state = engine.State(
        event=types.SimpleNamespace(),  # type: ignore[arg-type]
        question_id=uuid.uuid4(),
        model=types.SimpleNamespace(),  # type: ignore[arg-type]
        ctx=types.SimpleNamespace(ws=ws),  # type: ignore[arg-type]
        plan=_plan({"id": "s1", "tool": "query_metric", "instruction": "x", "status": "failed"}),
    )
    asyncio.run(engine.compose(state))
    assert state.handed_back and state.composition == []


def test_a_metric_that_does_not_measure_what_was_asked_hands_the_question_back() -> None:
    import datetime
    import types
    import uuid

    from libs import clock
    from services.worker_agent.agentic import engine
    from services.worker_agent.understand.resolver import MetricEntry

    units = MetricEntry(uuid.uuid4(), "SO_LUONG_BAN_INTERNET", "Số lượng bán Internet", True)
    vocab = types.SimpleNamespace(
        metrics=[units], synonyms={}, dimensions=[], values=[], calendar=clock.DEFAULT
    )
    ws = Workspace()
    ws.add(
        title="Số lượng bán Internet",
        columns=["Số lượng bán Internet"],
        rows=[[5520]],
        trust="certified",
        tool="query_metric",
        reading={"metric": "SO_LUONG_BAN_INTERNET", "group_by": [], "filters": {}},
    )

    def state(question: str) -> engine.State:
        return engine.State(
            event=types.SimpleNamespace(),  # type: ignore[arg-type]
            question_id=uuid.uuid4(),
            model=types.SimpleNamespace(),  # type: ignore[arg-type]
            ctx=types.SimpleNamespace(  # type: ignore[arg-type]
                ws=ws,
                question=question,
                today=datetime.date(2026, 11, 3),
                layer=types.SimpleNamespace(vocabulary=vocab),
            ),
            plan=_plan({"id": "s1", "tool": "query_metric", "instruction": "x", "status": "done"}),
        )

    asked_customers = state("Có bao nhiêu khách hàng mua qua Internet năm 2013")
    assert engine._unnamed_quantity(asked_customers) == ["khach", "hang", "mua"]
    asyncio.run(engine.compose(asked_customers))
    assert asked_customers.handed_back
    assert engine._unnamed_quantity(state("Vì sao số lượng bán Internet năm 2013 giảm")) == []


def test_query_metric_sends_a_quantity_no_metric_names_to_exploratory_sql() -> None:
    import datetime
    import types
    import uuid

    from libs import clock
    from services.worker_agent.agentic.tools.query import QueryMetric, QueryMetricArgs
    from services.worker_agent.understand.resolver import MetricEntry

    units = MetricEntry(uuid.uuid4(), "SO_LUONG_BAN_INTERNET", "Số lượng bán Internet", True)
    layer = types.SimpleNamespace(
        vocabulary=types.SimpleNamespace(
            metrics=[units], synonyms={}, dimensions=[], values=[], calendar=clock.DEFAULT
        ),
        versions={units.metric_id: object()},
        blocked_metric_ids=set(),
    )
    ctx = types.SimpleNamespace(
        layer=layer,
        question="Có bao nhiêu khách hàng mua qua Internet năm 2013",
        today=datetime.date(2026, 11, 3),
    )
    done = asyncio.run(
        QueryMetric().run(ctx, QueryMetricArgs(metric="SO_LUONG_BAN_INTERNET"))  # type: ignore[arg-type]
    )
    assert not done.ok
    assert "explore_sql" in done.observation
    assert done.detail["unnamed"] == ["khach", "hang", "mua"]


def test_caveats_carry_the_sql_vote_and_leave_out_a_failed_computation() -> None:
    import types
    import uuid

    from services.worker_agent.agentic import engine

    ws = Workspace()
    ws.add(
        title="Số khách hàng 2013",
        columns=["so_khach_hang"],
        rows=[[17429]],
        trust="exploratory",
        tool="explore_sql",
        consensus=(6, 6),
    )
    state = engine.State(
        event=types.SimpleNamespace(),  # type: ignore[arg-type]
        question_id=uuid.uuid4(),
        model=types.SimpleNamespace(),  # type: ignore[arg-type]
        ctx=types.SimpleNamespace(ws=ws),  # type: ignore[arg-type]
        plan=_plan(
            {"id": "s1", "tool": "explore_sql", "instruction": "a", "status": "done"},
            {"id": "s2", "tool": "analyze", "instruction": "b", "status": "failed"},
        ),
    )
    state.composition = [{"type": "kpi", "id": "b1", "items": [{"dataset": "d1"}]}]
    engine._append_caveats(state)
    caveats = state.composition[-1]["items"]
    assert caveats == [
        "Số khách hàng 2013: SQL thăm dò, chưa qua chỉ số chuẩn; 6/6 cách viết cho cùng kết quả."
    ]
    assert engine._consensus(state) == (6, 6)


def test_steps_that_need_nothing_of_each_other_run_together() -> None:
    plan = _plan(
        {"id": "s1", "tool": "query_metric", "instruction": "a"},
        {"id": "s2", "tool": "query_metric", "instruction": "b"},
        {"id": "s3", "tool": "analyze", "instruction": "c", "depends_on": ["s1", "s2"]},
    )
    assert [s.id for s in p.ready(plan, 3)] == ["s1", "s2"]
    assert all(s.status == "pending" for s in plan.steps)
    plan.steps[0].status = plan.steps[1].status = "done"
    assert [s.id for s in p.ready(plan, 3)] == ["s3"]


def test_a_chart_type_the_rows_do_not_fit_is_changed_and_one_row_is_a_table() -> None:
    ws = Workspace()
    ws.add(
        title="Doanh thu theo tháng",
        columns=["Tháng", "Doanh thu"],
        rows=[["2013-01", "10"], ["2013-02", "-3"], ["2013-03", "12"]],
        trust="certified",
    )
    ws.add(title="Tổng", columns=["Vùng", "Doanh thu"], rows=[["Europe", "5"]], trust="certified")
    # A pie of a month series with a negative month: a line along time instead.
    chart = b.materialize(
        b.BlockDraft(type="chart", dataset="d1", chart_type="pie", x="Tháng", y=["Doanh thu"]),
        ws,
        "b1",
    )
    assert chart["spec"]["chart_type"] == "line"
    assert chart["alternatives"][0] == "line" and "pie" not in chart["alternatives"]
    single = b.materialize(
        b.BlockDraft(type="chart", dataset="d2", chart_type="bar", x="Vùng", y=["Doanh thu"]),
        ws,
        "b2",
    )
    assert single["type"] == "table"
