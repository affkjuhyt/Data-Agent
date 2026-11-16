"""The dynamic agent's pure parts (worker_agent/agentic/): the plan DAG is validated before it
runs, blocks take their data from datasets, every number in the text is traced, the graph ends."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
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
    ws.datasets["d1"].reading = {
        "metric": "DT",
        "metric_id": "m-1",
        "group_by": ["vung"],
        "filters": {"nam": ["2013"]},
        "cuts": {"Vùng": "vung"},
        "time_cuts": [],
    }
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
    assert chart["insight"] == "Europe cao nhất, Pacific thấp nhất."
    # What Khám phá opens again: a certified metric's reading only.
    assert chart["explore"]["metric_id"] == "m-1"
    assert chart["explore"]["cuts"] == {"Vùng": "vung"}
    ws.datasets["d1"].trust = "exploratory"
    assert "explore" not in b.materialize(b.BlockDraft(type="table", dataset="d1"), ws, "t")
    ws.datasets["d1"].trust = "certified"
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
    # A row of a many-row result is named, never taken by index, and never called a total.
    for draft in (
        b.KpiDraft(label="Doanh thu", dataset="d1", column="Doanh thu", row=1),
        b.KpiDraft(
            label="Tổng doanh thu 2013",
            dataset="d1",
            column="Doanh thu",
            match_column="Vùng",
            match_value="Pacific",
        ),
    ):
        with pytest.raises(b.BlockError):
            b.materialize(b.BlockDraft(type="kpi", kpis=[draft]), ws, "b2")
    with pytest.raises(b.BlockError):
        b.materialize(b.BlockDraft(type="table", dataset="d9"), ws, "b3")
    with pytest.raises(b.BlockError):
        b.materialize(b.BlockDraft(type="chart", dataset="d1", x="Năm"), ws, "b4")


def test_the_cut_a_question_names_is_found_by_its_full_name() -> None:
    import uuid

    from services.worker_agent.agentic.tools.query import named_cut
    from services.worker_agent.understand.resolver import DimensionEntry, Vocabulary

    ids = [uuid.uuid4() for _ in range(4)]
    vocab = Vocabulary(
        metrics=[],
        dimensions=[
            DimensionEntry(ids[0], "san_pham", "Sản phẩm"),
            DimensionEntry(ids[1], "nhom_san_pham", "Nhóm sản phẩm"),
            DimensionEntry(ids[2], "vung", "Vùng bán hàng"),
            DimensionEntry(ids[3], "ngay", "Ngày đặt hàng", is_time=True),
        ],
    )
    allowed = set(ids)
    q = "Cơ cấu doanh thu theo nhóm sản phẩm năm 2013."
    assert named_cut(q, vocab, allowed) == "nhom_san_pham"
    # A cut the metric does not allow, or one named only in part ("theo vùng"), is left alone.
    assert named_cut(q, vocab, {ids[0]}) is None
    assert named_cut("Doanh thu theo vùng năm 2013", vocab, allowed) is None


def test_two_adjacent_years_are_read_as_a_comparison() -> None:
    from services.worker_agent.agentic.tools.query import two_years

    assert two_years("So sánh doanh thu theo vùng năm 2012 và 2013.") == (2012, 2013)
    assert two_years("Doanh thu năm 2013 so với năm 2012") == (2012, 2013)
    # Not two adjacent years, or not years at all: left to the planner.
    assert two_years("Doanh thu năm 2010 và 2013") is None
    assert two_years("Doanh thu tháng 8/2013 và 8/2012") is None


def test_series_named_by_periods_are_drawn_earlier_first_and_read_together() -> None:
    ws = Workspace()
    ws.add(
        title="Doanh thu theo vùng",
        columns=["Vùng", "Doanh thu 2013", "Doanh thu 2012"],
        rows=[["Europe", 300, 100], ["Pacific", 150, 200]],
        trust="derived",
    )
    chart = b.materialize(b.BlockDraft(type="chart", dataset="d1", chart_type="bar"), ws, "c")
    assert chart["spec"]["y_keys"] == ["Doanh thu 2012", "Doanh thu 2013"]
    assert chart["insight"] == (
        "So với 2012, Europe tăng mạnh nhất (+200,0%), Pacific giảm nhiều nhất (25,0%)."
    )
    months = ["2013-01", "2013-12"]
    assert b.series_insight(months, [[10.0, 14.0], [10.0, 5.0]], ["Internet", "Đại lý"]) == (
        "Từ 2013-01 đến 2013-12: Internet tăng 40,0%, Đại lý giảm 50,0%."
    )
    # A reading ranked by value is drawn along time, and read from its first month.
    ws.add(
        title="Doanh thu theo tháng",
        columns=["Tháng", "Doanh thu"],
        rows=[["2013-12", 18], ["2013-01", 9], ["2013-06", 16]],
        trust="certified",
    )
    line = b.materialize(b.BlockDraft(type="chart", dataset="d2", chart_type="line"), ws, "c3")
    assert [p["Tháng"] for p in line["spec"]["data"]] == ["2013-01", "2013-06", "2013-12"]
    assert line["insight"].startswith("Từ 2013-01 đến 2013-12 tăng 100,0%")
    assert b.time_order("Q2/2013") == (2013, 6, 0) and b.time_order("1234") is None
    # Two quantities side by side have no change between them to read: no line, not a wrong one.
    ws.add(
        title="Doanh thu và biên",
        columns=["Vùng", "Doanh thu", "Biên lợi nhuận"],
        rows=[["Europe", 300, 0.4], ["Pacific", 150, 0.3]],
        trust="derived",
    )
    assert "insight" not in b.materialize(
        b.BlockDraft(type="chart", dataset="d3", chart_type="bar"), ws, "c2"
    )


def test_a_merge_of_two_readings_is_named_by_what_each_read() -> None:
    from services.worker_agent.agentic.tools.analyze import merged_names

    ws = Workspace()
    for year in ("2012", "2013"):
        ws.add(
            title="Doanh thu theo vùng",
            columns=["Vùng", "Doanh thu"],
            rows=[["Europe", 1]],
            trust="certified",
            reading={"metric": "DT", "metric_name": "Doanh thu", "period": year},
        )
    cols = ["Vùng", "Doanh thu_x", "Doanh thu_y"]
    assert merged_names(cols, "result = d2.merge(d1, on='Vùng')", ws.datasets) == {
        "Doanh thu_x": "Doanh thu 2013",
        "Doanh thu_y": "Doanh thu 2012",
    }
    # Through the code's names for the frames (the 4B model wrote df_2011 = d1, 16/11).
    aliased = "df_2012 = d1\ndf_2013 = d2.copy()\nresult = pd.merge(df_2012, df_2013, on='Vùng')"
    assert merged_names(cols, aliased, ws.datasets) == {
        "Doanh thu_x": "Doanh thu 2012",
        "Doanh thu_y": "Doanh thu 2013",
    }
    assert merged_names(["Vùng", "Doanh thu"], "result = d1", ws.datasets) == {}
    # Sides that cannot be told apart are the model's to name.
    assert merged_names(cols, "result = something(d1, d2)", ws.datasets) is None


def test_a_quantity_no_metric_measures_is_asked_back_with_the_closest_metrics() -> None:
    import uuid

    from services.worker_agent.agentic import clarify_policy
    from services.worker_agent.understand.resolver import DimensionEntry, MetricEntry, Vocabulary

    buyers, revenue = uuid.uuid4(), uuid.uuid4()
    layer = SimpleNamespace(
        vocabulary=Vocabulary(
            metrics=[
                MetricEntry(buyers, "SO_KHACH_HANG", "Số khách hàng mua qua Internet", True),
                MetricEntry(revenue, "DOANH_THU", "Doanh thu", True),
            ],
            dimensions=[DimensionEntry(uuid.uuid4(), "nhom_san_pham", "Nhóm sản phẩm")],
        ),
        versions={buyers: object(), revenue: object()},
        blocked_metric_ids=frozenset(),
    )
    ask = clarify_policy.unnamed_quantity(
        "Khách hàng rời bỏ là bao nhiêu?", ["khach", "hang", "roi", "bo"], layer
    )
    assert ask is not None
    assert ask.question == (
        "Chưa có chỉ số chuẩn nào đo “Khách hàng rời bỏ”. Bạn muốn xem chỉ số nào dưới đây?"
    )
    # Each option is the question again with a metric in place of the quantity.
    assert ask.options == ["Số khách hàng mua qua Internet là bao nhiêu?"]
    # Read nothing, nothing stands out: the closest metrics, asked with the question's period.
    fallback = clarify_policy.nothing_read("Khách hàng rời bỏ năm 2013?", layer, "năm 2013")
    assert fallback is not None
    assert fallback.options == ["Số khách hàng mua qua Internet năm 2013 là bao nhiêu?"]
    # A question naming a member read nothing for the member: no metrics offered in its place.
    assert clarify_policy.nothing_read("Doanh thu nhóm Bikes năm 2013?", layer, "năm 2013") is None
    # A member written as a name is not a quantity.
    assert (
        clarify_policy.unnamed_quantity("Doanh thu nhóm Bikes năm 2013?", ["bikes"], layer) is None
    )
    # A cut's words are a missed cut, not a missing metric: no question back.
    assert (
        clarify_policy.unnamed_quantity("Cơ cấu theo nhóm sản phẩm", ["nhom", "san", "pham"], layer)
        is None
    )


def test_a_turn_that_read_nothing_asks_back_before_writing() -> None:
    import datetime
    import types
    import uuid

    from services.worker_agent.agentic import engine
    from services.worker_agent.understand.resolver import MetricEntry, Vocabulary

    buyers = uuid.uuid4()
    vocab = Vocabulary(
        metrics=[MetricEntry(buyers, "SO_KHACH_HANG", "Số khách hàng mua qua Internet", True)],
        dimensions=[],
    )
    state = engine.State(
        event=types.SimpleNamespace(),  # type: ignore[arg-type]
        question_id=uuid.uuid4(),
        model=types.SimpleNamespace(),  # type: ignore[arg-type]
        ctx=types.SimpleNamespace(  # type: ignore[arg-type]
            ws=Workspace(),
            question="Khách hàng rời bỏ là bao nhiêu?",
            today=datetime.date(2014, 1, 15),
            layer=types.SimpleNamespace(
                vocabulary=vocab, versions={buyers: object()}, blocked_metric_ids=frozenset()
            ),
            period=None,
            period_moves={},
        ),
        # q8: two steps ran (catalogue, values), then a replan left no step, in "answer" mode.
        plan=_plan().model_copy(update={"mode": "answer"}),
    )
    assert not engine._ask_for_unnamed_question(state)  # nothing tried: a greeting
    state.step_runs = 2
    assert engine._ask_for_unnamed_question(state)
    assert state.clarify is not None
    assert state.clarify.options == ["Số khách hàng mua qua Internet là bao nhiêu?"]


def test_the_live_timeline_says_what_each_step_found_from_its_data() -> None:
    import uuid

    from services.worker_agent.agentic import narrate
    from services.worker_agent.agentic.tools.base import ToolResult
    from services.worker_agent.understand.resolver import MetricEntry, Vocabulary

    vocab = Vocabulary(
        metrics=[MetricEntry(uuid.uuid4(), "DOANH_THU_INTERNET", "Doanh thu Internet", True)],
        dimensions=[],
    )
    # The planner's thought for a reader: names, not codes; titles, not step ids.
    assert narrate.reader("Bước s2 lỗi, đọc lại DOANH_THU_INTERNET theo tháng", vocab) == (
        "bước trước lỗi, đọc lại Doanh thu Internet theo tháng"
    )
    ws = _ws()
    found = ToolResult(True, "…", ["d1"])
    assert narrate.said("query_metric", found, ws, vocab) == (
        "Doanh thu theo vùng: 2 dòng; cao nhất Europe (1.500.000,50)."
    )
    refused = ToolResult(
        False, "không chỉ số chuẩn nào đo 'khach hang'; không tự tính", failure="logic"
    )
    assert narrate.said("query_metric", refused, ws, vocab) == (
        "Không chỉ số chuẩn nào đo 'khach hang'"
    )
    # A comparison's base period is not its value: the first measure after the cut is.
    ws.add(
        title="Doanh thu tháng 12/2013",
        columns=["Nhóm vùng", "Doanh thu", "Doanh thu (kỳ so sánh)"],
        rows=[["Europe", 500, 90], ["North America", 900, 40]],
        trust="certified",
    )
    compared = ToolResult(True, "…", ["d2"])
    assert narrate.said("query_metric", compared, ws, vocab).endswith(
        "cao nhất North America (900)."
    )
    # Two periods side by side: the latest is the one said, by its name.
    ws.add(
        title="Doanh thu theo vùng",
        columns=["Vùng", "Doanh thu 2012", "Doanh thu 2013"],
        rows=[["Southwest", 85, 97], ["Canada", 57, 62]],
        trust="certified",
    )
    two = ToolResult(True, "…", ["d3"])
    assert narrate.said("query_metric", two, ws, vocab).endswith(
        "cao nhất Southwest (Doanh thu 2013: 97)."
    )
    crashed = ToolResult(False, "code lỗi: NameError: x", failure="arguments")
    assert narrate.said("analyze", crashed, ws, vocab).startswith("Tham số chưa đúng")
    coverage = ToolResult(
        True,
        "…",
        detail={"metric": "Doanh thu", "first": "2010-12", "last": "2014-01", "partial_last": True},
    )
    assert narrate.said("data_coverage", coverage, ws, vocab) == (
        "Doanh thu có số từ 2010-12 đến 2014-01, kỳ cuối chưa đủ."
    )


def test_a_charts_one_line_reading_is_computed_from_its_rows() -> None:
    months = ["2013-01", "2013-06", "2013-12"]
    assert (
        b.chart_insight("line", "time", months, [857689.91, 1643177.78, 1874360.29])
        == "Từ 2013-01 đến 2013-12 tăng 118,5%; cao nhất 2013-12, thấp nhất 2013-01."
    )
    shares = b.chart_insight("pie", "category", ["Bikes", "Clothing"], [42302193.5, 1202657.7])
    assert shares == "Bikes lớn nhất, chiếm 97,2% tổng."
    # One point says nothing; a missing value is left out, not read as zero.
    assert b.chart_insight("line", "time", ["2013-01"], [1.0]) is None
    assert b.chart_insight("bar", "category", ["A", "B", "C"], [3.0, None, 1.0]) == (
        "A cao nhất, C thấp nhất."
    )
    # Two series side by side are read as the change from the first to the second.
    regions = ["Australia", "Northeast", "Canada"]
    assert b.comparison_insight(
        regions, [100.0, 300.0, 200.0], [270.0, 206.0, 220.0], ("Doanh thu 2012", "Doanh thu 2013")
    ) == ("So với 2012, Australia tăng mạnh nhất (+170,0%), Northeast giảm nhiều nhất (31,3%).")


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
    from services.worker_agent.agentic import engine

    # What is left of a callout whose only sentence was dropped is not drawn as an empty box; a
    # sentence that leads into a chart is kept.
    assert engine._label_only("**Lưu ý:**") and engine._label_only("Lưu ý:")
    assert not engine._label_only("Dưới đây là xu hướng doanh thu theo tháng:")


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
    # AGC-11: the reason goes back to the replanner once, then the question is handed over.
    asyncio.run(engine.compose(state))
    assert not state.handed_back and state.feedback and state.composition == []
    state.feedback = None
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
    asked_customers.gate_retries = engine.MAX_GATE_RETRIES
    asyncio.run(engine.compose(asked_customers))
    assert asked_customers.handed_back
    assert engine._unnamed_quantity(state("Vì sao số lượng bán Internet năm 2013 giảm")) == []
    # "Cơ cấu" folds to "co cau": how to read the quantity, not one the catalogue lacks.
    assert engine._unnamed_quantity(state("Cơ cấu số lượng bán Internet năm 2013")) == []


def test_query_metric_refuses_a_quantity_no_metric_names_without_inventing_sql() -> None:
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
        versions={units.metric_id: SimpleNamespace(allowed_dimension_ids=[])},
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
    assert "explore_sql" not in done.observation
    assert "không tự tính" in done.observation
    assert done.detail["unnamed"] == ["khach", "hang", "mua"]


def test_query_metric_drops_a_made_up_calendar_cut_when_a_grain_is_set(monkeypatch) -> None:
    import datetime
    import types
    import uuid

    from libs import clock
    from services.worker_agent.agentic.tools.query import QueryMetric, QueryMetricArgs
    from services.worker_agent.semantic import compiler as c
    from services.worker_agent.understand.resolver import MetricEntry

    revenue = MetricEntry(uuid.uuid4(), "DOANH_THU_INTERNET", "Doanh thu Internet", True)
    region = types.SimpleNamespace(
        dimension_id=uuid.uuid4(), code="vung", name="Vùng", is_time=False
    )
    colour = types.SimpleNamespace(
        dimension_id=uuid.uuid4(), code="mau_sac", name="Màu", is_time=False
    )
    day = types.SimpleNamespace(
        dimension_id=uuid.uuid4(), code="ngay_dat", name="Ngày", is_time=True
    )
    layer = types.SimpleNamespace(
        vocabulary=types.SimpleNamespace(
            metrics=[revenue],
            synonyms={},
            dimensions=[region, colour, day],
            values=[],
            calendar=clock.DEFAULT,
        ),
        versions={
            revenue.metric_id: types.SimpleNamespace(allowed_dimension_ids={region.dimension_id})
        },
        blocked_metric_ids=set(),
        layer=object(),
    )
    ctx = types.SimpleNamespace(
        layer=layer,
        question="Doanh thu Internet theo tháng",
        today=datetime.date(2026, 11, 3),
        period_moves={},
    )
    compiled: list[c.Request] = []

    def refuse(request, *_a, **_k):
        compiled.append(request)
        raise c.CompileError("NO_JOIN_PATH", "stop here")

    monkeypatch.setattr(c, "compile_metric", refuse)
    monkeypatch.setattr(c, "time_dimension_of", lambda *_a: day)
    by_month = QueryMetricArgs(
        metric="DOANH_THU_INTERNET", grain="month", group_by=["thang_tai_chinh", "vung"]
    )
    asyncio.run(QueryMetric().run(ctx, by_month))  # type: ignore[arg-type]
    # The made-up month cut never reaches the compiler; the month is cut along the metric's own
    # time, which the catalogue the planner reads does not list.
    assert compiled and compiled[0].group_by == ["vung", "ngay_dat"]
    # A grain the model set unasked ("cơ cấu … theo vùng") adds no month cut to the groups.
    ctx.question = "Cơ cấu doanh thu Internet theo vùng năm 2013"
    asyncio.run(
        QueryMetric().run(
            ctx, QueryMetricArgs(metric="DOANH_THU_INTERNET", grain="month", group_by=["vung"])
        )  # type: ignore[arg-type]
    )
    assert compiled[-1].group_by == ["vung"]
    ctx.question = "Doanh thu Internet theo tháng"

    refused = asyncio.run(
        QueryMetric().run(
            ctx, QueryMetricArgs(metric="DOANH_THU_INTERNET", grain="month", group_by=["mau_sac"])
        )  # type: ignore[arg-type]
    )
    assert not refused.ok and refused.failure == "arguments"
    # The refusal says how to ask again, so the replan has something to change.
    assert (
        "dùng grain" in refused.observation and "các chiều được phép: vung" in refused.observation
    )


def test_explore_sql_refuses_a_quantity_no_metric_in_the_catalogue_names() -> None:
    """RES-04: churn / invented KPIs must not be computed by exploratory SQL."""
    import datetime
    import types
    import uuid

    from libs import clock
    from services.worker_agent.agentic.tools.query import ExploreSql, ExploreSqlArgs
    from services.worker_agent.understand.resolver import MetricEntry

    units = MetricEntry(uuid.uuid4(), "SO_LUONG_BAN_INTERNET", "Số lượng bán Internet", True)
    ctx = types.SimpleNamespace(
        layer=types.SimpleNamespace(
            vocabulary=types.SimpleNamespace(
                metrics=[units], synonyms={}, dimensions=[], values=[], calendar=clock.DEFAULT
            )
        ),
        question="Tỷ lệ rời bỏ khách hàng năm 2013",
        today=datetime.date(2026, 11, 3),
        org_id=uuid.uuid4(),
        space_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        question_id=uuid.uuid4(),
        ws=Workspace(),
        refused_sql=[],
        models=[],
        step_id="s1",
    )
    done = asyncio.run(
        ExploreSql().run(ctx, ExploreSqlArgs(question="Tỷ lệ rời bỏ khách hàng năm 2013"))  # type: ignore[arg-type]
    )
    assert not done.ok
    assert "không tự tính" in done.observation
    assert done.detail["unnamed"]


def test_explore_sql_answer_for_an_uncatalogued_quantity_hands_the_question_back() -> None:
    """Compose still catches an exploratory dataset that invented a quantity (RES-04)."""
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
        title="Churn",
        columns=["ty_le_roi_bo"],
        rows=[[0.12]],
        trust="exploratory",
        tool="explore_sql",
        sql="SELECT 0.12",
    )
    state = engine.State(
        event=types.SimpleNamespace(),  # type: ignore[arg-type]
        question_id=uuid.uuid4(),
        model=types.SimpleNamespace(),  # type: ignore[arg-type]
        ctx=types.SimpleNamespace(  # type: ignore[arg-type]
            ws=ws,
            question="Tỷ lệ rời bỏ khách hàng năm 2013",
            today=datetime.date(2026, 11, 3),
            layer=types.SimpleNamespace(vocabulary=vocab),
        ),
        plan=_plan({"id": "s1", "tool": "explore_sql", "instruction": "x", "status": "done"}),
    )
    assert engine._unnamed_quantity(state)
    state.gate_retries = engine.MAX_GATE_RETRIES
    asyncio.run(engine.compose(state))
    assert state.handed_back


def test_explore_sql_for_a_catalogued_quantity_is_not_an_unnamed_gate() -> None:
    import datetime
    import types
    import uuid

    from libs import clock
    from services.worker_agent.agentic import engine
    from services.worker_agent.understand.resolver import MetricEntry

    revenue = MetricEntry(uuid.uuid4(), "DOANH_THU_INTERNET", "Doanh thu Internet", True)
    vocab = types.SimpleNamespace(
        metrics=[revenue], synonyms={}, dimensions=[], values=[], calendar=clock.DEFAULT
    )
    ws = Workspace()
    ws.add(
        title="Doanh thu",
        columns=["doanh_thu"],
        rows=[[15359502.36]],
        trust="exploratory",
        tool="explore_sql",
        sql="SELECT SUM(x) FROM f",
    )
    state = engine.State(
        event=types.SimpleNamespace(),  # type: ignore[arg-type]
        question_id=uuid.uuid4(),
        model=types.SimpleNamespace(),  # type: ignore[arg-type]
        ctx=types.SimpleNamespace(  # type: ignore[arg-type]
            ws=ws,
            question="Doanh thu Internet năm 2013",
            today=datetime.date(2026, 11, 3),
            layer=types.SimpleNamespace(vocabulary=vocab),
        ),
        plan=_plan({"id": "s1", "tool": "explore_sql", "instruction": "x", "status": "done"}),
    )
    assert engine._unnamed_quantity(state) == []


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
    # Draft sentences the number check dropped are the timeline's, not the reader's caveat.
    state.dropped_sentences = 2
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


def _gate_state(question: str, ws: Workspace, values: list[Any]) -> Any:
    import datetime
    import types
    import uuid

    from libs import clock
    from services.worker_agent.agentic import engine

    vocab = types.SimpleNamespace(
        metrics=[], synonyms={}, dimensions=[], values=values, calendar=clock.DEFAULT
    )
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
        plan=_plan({"id": "s1", "tool": "explore_sql", "instruction": "x", "status": "done"}),
    )


def test_a_member_the_question_names_but_no_dataset_used_hands_the_question_back() -> None:
    from services.worker_agent.agentic import engine
    from services.worker_agent.understand.resolver import ValueEntry
    from services.worker_agent.understand.value_retrieval import profile_supplements

    bikes = ValueEntry("nhom_hang", "Bikes", "Bikes")
    total = Workspace()
    total.add(
        title="t",
        columns=["doanh_thu"],
        rows=[[16351550.34]],
        trust="exploratory",
        tool="explore_sql",
        sql="SELECT SUM(x) FROM f",
    )
    state = _gate_state("Doanh thu Internet nhóm hàng Bikes năm 2013", total, [bikes])
    assert engine._dropped_members(state) == ["Bikes"]
    state.gate_retries = engine.MAX_GATE_RETRIES
    asyncio.run(engine.compose(state))
    assert state.handed_back

    filtered = Workspace()
    filtered.add(
        title="t",
        columns=["doanh_thu"],
        rows=[[15359502.36]],
        trust="exploratory",
        tool="explore_sql",
        sql="SELECT SUM(x) FROM f WHERE category = 'Bikes'",
    )
    assert (
        engine._dropped_members(
            _gate_state("Doanh thu Internet nhóm hàng Bikes năm 2013", filtered, [bikes])
        )
        == []
    )

    # Empty value_index: loader would merge column-profile top_values (RES-03).
    from_profile = [
        ValueEntry(dim, code, label)
        for dim, code, label in profile_supplements(
            [],
            [
                (
                    "nhom_hang",
                    [{"value": "Bikes", "count": 40}, {"value": "Components", "count": 20}],
                )
            ],
        )
    ]
    assert engine._dropped_members(
        _gate_state("Doanh thu Internet nhóm hàng Bikes năm 2013", total, from_profile)
    ) == ["Bikes"]
    assert (
        engine._dropped_members(
            _gate_state("Doanh thu Internet nhóm hàng Bikes năm 2013", total, [])
        )
        == []
    )


def test_figures_that_are_all_zero_are_no_answer() -> None:
    from services.worker_agent.agentic import engine

    zero = Workspace()
    zero.add(title="t", columns=["Năm", "Doanh thu"], rows=[[2009, 0]], trust="certified")
    assert engine._no_figure(zero)
    some = Workspace()
    some.add(title="t", columns=["Năm", "Doanh thu"], rows=[[2013, 5]], trust="certified")
    assert not engine._no_figure(some)


def test_a_sql_that_ran_is_audited_against_the_question() -> None:
    import types

    from services.worker_agent.agentic.tools import query
    from services.worker_agent.llm import Reply

    seen: list[str] = []

    class Model:
        async def json_chat(self, role, system, user, output, **_):  # type: ignore[no-untyped-def]
            seen.append(user)
            return Reply(
                output(passed=False, reason="reads the sales quota, not revenue"), "m", 9, 1
            )

    ctx = types.SimpleNamespace(question="Doanh thu năm 2013", model=Model())
    audit = asyncio.run(
        query._audit(
            ctx,  # type: ignore[arg-type]
            "tổng doanh thu 2013",
            "dw.fact_sales_quota(amount, year)",
            "SELECT SUM(amount) FROM dw.fact_sales_quota WHERE year = 2013",
            ["sum"],
            [[29507000]],
        )
    )
    assert audit is not None and not audit.passed and "quota" in audit.reason
    assert "<sql>" in seen[0] and "29507000" in seen[0] and "Doanh thu năm 2013" in seen[0]


def test_explore_skips_sql_the_audit_already_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """RES-02: a statement audited as the wrong quantity must not run again."""
    import uuid
    from collections.abc import Sequence

    from services.worker_agent.query.executor import QueryOutcome
    from services.worker_agent.sqlgen import run as sql_run
    from services.worker_agent.sqlgen.check import fingerprint
    from services.worker_agent.sqlgen.generate import Candidate
    from services.worker_agent.sqlgen.schema import Col, Linked, Tbl

    bad = "SELECT SUM(amount) FROM dw.fact_sales_quota WHERE year = 2013"
    good = "SELECT SUM(sales_amount) FROM dw.fact_internet_sales WHERE year = 2013"
    tables = [
        Tbl(
            dataset_id=uuid.uuid4(),
            schema="dw",
            name="fact_sales_quota",
            label="quota",
            columns=(Col("amount", "amount", "decimal"), Col("year", "year", "integer")),
        ),
        Tbl(
            dataset_id=uuid.uuid4(),
            schema="dw",
            name="fact_internet_sales",
            label="sales",
            columns=(
                Col("sales_amount", "sales", "decimal"),
                Col("year", "year", "integer"),
            ),
        ),
    ]
    linked = Linked(tables=tables, fks=[], values=[])
    ran: list[str] = []

    async def fake_candidates(*_a, refuse: Sequence[str] = (), **_k):  # type: ignore[no-untyped-def]
        assert fingerprint(bad, "postgres") in {fingerprint(s, "postgres") for s in refuse}
        return [
            Candidate("skeleton", bad),
            Candidate("examples", good),
            Candidate("divide", bad),
            Candidate("skeleton", good),
            Candidate("examples", bad),
            Candidate("divide", good),
        ]

    async def run_sql(sql: str) -> QueryOutcome:
        ran.append(sql)
        return QueryOutcome(
            query_run_id=uuid.uuid4(),
            status="succeeded",
            columns=[{"name": "sum"}],
            rows=[[1]],
            truncated=False,
            sql=sql,
            error=None,
        )

    monkeypatch.setattr(sql_run, "candidates", fake_candidates)
    monkeypatch.setattr(sql_run, "problems", lambda *_a, **_k: [])
    found = asyncio.run(
        sql_run.explore(
            gateway=None,  # type: ignore[arg-type]
            question="Doanh thu năm 2013",
            linked=linked,
            dialect="postgres",
            examples=[],
            run_sql=run_sql,
            refuse=(fingerprint(bad, "postgres"),),
        )
    )
    assert found.decision != "failed" and found.sql is not None
    assert fingerprint(found.sql, "postgres") == fingerprint(good, "postgres")
    assert all(fingerprint(s, "postgres") != fingerprint(bad, "postgres") for s in ran)
    assert any(t.get("status") == "rejected" for t in found.tried)


def test_explore_sql_bans_audited_sql_on_the_shared_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After an audit miss, the fingerprint stays on ToolContext for the next explore / replan."""
    import datetime
    import types
    import uuid

    from services.worker_agent.agentic.tools.base import ToolContext
    from services.worker_agent.agentic.tools.query import ExploreSql, ExploreSqlArgs
    from services.worker_agent.agentic.workspace import Workspace
    from services.worker_agent.llm import Reply
    from services.worker_agent.sqlgen.check import fingerprint
    from services.worker_agent.sqlgen.run import Exploration

    bad_sql = "SELECT SUM(amount) FROM dw.fact_sales_quota WHERE year = 2013"
    calls = {"n": 0}

    class Model:
        async def json_chat(self, role, system, user, output, **_):  # type: ignore[no-untyped-def]
            return Reply(
                output(passed=False, reason="reads the sales quota, not revenue"), "m", 3, 1
            )

    async def fake_explore(*_a, refuse=(), **_k):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        assert (
            fingerprint(bad_sql, "postgres") in {fingerprint(s, "postgres") for s in refuse}
            or calls["n"] == 1
        )
        return Exploration(
            decision="answer",
            confidence=1.0,
            agreeing=6,
            sql=bad_sql,
            columns=["sum"],
            rows=[[29507000]],
            query_run_id=uuid.uuid4(),
            tried=[{"method": "skeleton", "sql": bad_sql, "status": "ran"}],
            tokens=1,
            models=["m"],
        )

    monkeypatch.setattr("services.worker_agent.agentic.tools.query.explore", fake_explore)

    async def fake_load_space(*_a, **_k):
        return types.SimpleNamespace(
            schema=types.SimpleNamespace(examples=[]),
            source_of={},
        )

    monkeypatch.setattr("services.worker_agent.agentic.tools.query.load_space", fake_load_space)
    monkeypatch.setattr(
        "services.worker_agent.agentic.tools.query.link",
        lambda *_a, **_k: types.SimpleNamespace(
            tables=[types.SimpleNamespace(qualified="dw.t")],
        ),
    )
    monkeypatch.setattr(
        "services.worker_agent.agentic.tools.query._keep_one_source",
        lambda linked, _src: (linked, uuid.uuid4()),
    )

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, *_a, **_k):
            return types.SimpleNamespace(
                id=uuid.uuid4(),
                name="aw",
                kind="postgresql",
                mode="federated",
                status="active",
            )

        def expunge(self, _):
            return None

    monkeypatch.setattr(
        "services.worker_agent.agentic.tools.query.scoped_session",
        lambda *_a, **_k: _Session(),
    )
    monkeypatch.setattr(
        "services.worker_agent.agentic.tools.query.schema_text", lambda _l: "schema"
    )

    from libs import clock
    from services.worker_agent.understand.resolver import MetricEntry

    revenue = MetricEntry(uuid.uuid4(), "DOANH_THU", "Doanh thu", True)
    shared: list[str] = []
    ctx = ToolContext(
        org_id=uuid.uuid4(),
        space_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        question_id=uuid.uuid4(),
        question="Doanh thu năm 2013",
        today=datetime.date(2026, 11, 5),
        as_of=None,
        purpose=None,
        layer=types.SimpleNamespace(  # type: ignore[arg-type]
            vocabulary=types.SimpleNamespace(
                metrics=[revenue],
                synonyms={},
                dimensions=[],
                values=[],
                calendar=clock.DEFAULT,
            )
        ),
        model=Model(),  # type: ignore[arg-type]
        ws=Workspace(),
        refused_sql=shared,
    )
    result = asyncio.run(ExploreSql().run(ctx, ExploreSqlArgs(question="doanh thu 2013")))
    assert not result.ok
    assert "quota" in result.observation
    assert shared and fingerprint(bad_sql, "postgres") in shared
    assert calls["n"] == 2


def test_a_lookup_and_one_governed_query_is_a_simple_question() -> None:
    import types
    import uuid

    from services.worker_agent.agentic import engine

    one = Workspace()
    one.add(title="t", columns=["v"], rows=[[1]], trust="certified")

    def state(*steps: dict[str, Any], ws: Workspace = one) -> engine.State:
        return engine.State(
            event=types.SimpleNamespace(),  # type: ignore[arg-type]
            question_id=uuid.uuid4(),
            model=types.SimpleNamespace(),  # type: ignore[arg-type]
            ctx=types.SimpleNamespace(ws=ws),  # type: ignore[arg-type]
            plan=_plan(*steps),
        )

    assert engine.simple(
        state(
            {"id": "s1", "tool": "search_catalog", "instruction": "a"},
            {"id": "s2", "tool": "query_metric", "instruction": "b"},
        )
    )
    assert not engine.simple(state({"id": "s1", "tool": "explore_sql", "instruction": "a"}))
    assert not engine.simple(
        state(
            {"id": "s1", "tool": "query_metric", "instruction": "a"},
            {"id": "s2", "tool": "query_metric", "instruction": "b"},
        )
    )


def test_citation_markers_off_format_are_repaired_and_unknown_ones_dropped() -> None:
    text = "Theo quy định [ID:1] và báo cáo 【2】, xem thêm [^3] và [7]."
    assert (
        verify.repair_citations(text, {1, 2, 3})
        == "Theo quy định [1] và báo cáo [2], xem thêm [3] và ."
    )


def test_signals_turn_quality_freshness_and_moved_structure_into_caveats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import contextlib
    import datetime
    import types
    import uuid

    from services.worker_agent.agentic import signals

    table, metric = uuid.uuid4(), uuid.uuid4()
    ws = Workspace()
    ws.add(
        title="Doanh thu",
        columns=["v"],
        rows=[[1]],
        trust="certified",
        sources=[str(table)],
        reading={"metric": "DT", "metric_id": str(metric), "group_by": [], "filters": {}},
    )
    certified = datetime.datetime(2026, 10, 1, tzinfo=datetime.UTC)
    layer = types.SimpleNamespace(
        datasets={
            table: types.SimpleNamespace(
                display_name="Bán hàng", table_name="f", quality_state="blocked"
            )
        },
        version_rows={metric: types.SimpleNamespace(certified_at=certified)},
    )
    results = iter(
        [
            [types.SimpleNamespace(member_ref=table, freshness_state="fresh")],
            [
                types.SimpleNamespace(
                    dataset_id=table, version=2, valid_from=certified + datetime.timedelta(days=3)
                )
            ],
        ]
    )

    class Session:
        async def scalars(self, _query: object) -> Any:
            return types.SimpleNamespace(all=lambda: next(results))

    @contextlib.asynccontextmanager
    async def scoped(*_a: object) -> Any:
        yield Session()

    monkeypatch.setattr(signals, "scoped_session", scoped)
    ctx = types.SimpleNamespace(ws=ws, layer=layer, org_id=uuid.uuid4(), user_id=uuid.uuid4())
    found = asyncio.run(signals.read(ctx))  # type: ignore[arg-type]
    assert not found.out_of_time
    assert any("chất lượng mức cao" in c for c in found.caveats)
    assert any("đổi cấu trúc" in c for c in found.caveats)


def _cancel_state() -> Any:
    import types
    import uuid

    from services.worker_agent.agentic import engine

    return engine.State(
        event=types.SimpleNamespace(),  # type: ignore[arg-type]
        question_id=uuid.uuid4(),
        model=types.SimpleNamespace(),  # type: ignore[arg-type]
        plan=_plan(
            {"id": "s1", "tool": "query_metric", "instruction": "a", "status": "running"},
            {"id": "s2", "tool": "query_metric", "instruction": "b", "status": "done"},
        ),
    )


def test_a_cancel_stops_the_running_node_and_skips_its_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.worker_agent.agentic import engine

    asked: list[bool] = []

    async def requested(_qid: str) -> bool:
        asked.append(True)
        return len(asked) > 1  # not before the node starts; on its first poll

    monkeypatch.setattr(engine, "is_cancel_requested", requested)
    monkeypatch.setattr(engine, "CANCEL_POLL_SECONDS", 0.01)
    finished: list[bool] = []

    async def slow(_s: Any) -> None:
        await asyncio.sleep(5)
        finished.append(True)

    state = _cancel_state()
    asyncio.run(engine._cancellable(slow)(state))
    assert state.cancelled and finished == []
    assert [st.status for st in state.plan.steps] == ["skipped", "done"]
    assert engine._or_stop(lambda _s: "act")(state) == "persist"


def test_a_cancel_asked_before_a_node_starts_runs_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.worker_agent.agentic import engine

    async def requested(_qid: str) -> bool:
        return True

    monkeypatch.setattr(engine, "is_cancel_requested", requested)
    ran: list[bool] = []

    async def node(_s: Any) -> None:
        ran.append(True)

    state = _cancel_state()
    asyncio.run(engine._cancellable(node)(state))
    assert state.cancelled and ran == []


# ---- AGC-02: what a failed step does next depends on why it failed


class _Args(__import__("pydantic").BaseModel):
    n: int = 0


def _step_world(
    monkeypatch: pytest.MonkeyPatch, results: list[Any], *, timeout: float = 5.0
) -> Any:
    """A state whose only tool answers `results` in turn, and an executor that records the error it
    was asked to fix and writes n + 1."""
    import datetime
    import types
    import uuid

    from services.worker_agent.agentic import engine
    from services.worker_agent.agentic.tools import Tool, ToolContext, ToolResult

    runs: list[int] = []
    fixes: list[str] = []

    class Fake(Tool):
        name = "fake"
        title = "Fake"
        description = "fake"
        Args = _Args
        timeout_seconds = timeout

        async def run(self, ctx: ToolContext, args: Any) -> ToolResult:
            runs.append(args.n)
            r = results[min(len(runs), len(results)) - 1]
            if r == "hang":
                await asyncio.sleep(10)
            return r if isinstance(r, ToolResult) else ToolResult(True, "ok")

    async def json_chat(_role: str, _system: str, user: str, *_a: Any, **_k: Any) -> Any:
        fixes.append(user.rsplit("Lần trước lỗi: ", 1)[-1] if "Lần trước lỗi" in user else "")
        return types.SimpleNamespace(content=engine.ExecutorReply(args={"n": len(fixes)}))

    monkeypatch.setitem(engine.TOOLS, "fake", Fake())
    monkeypatch.setattr(engine, "TRANSIENT_PAUSE_SECONDS", 0)
    monkeypatch.setattr(engine, "_situation", lambda _s: "")
    monkeypatch.setattr(engine, "_progress", lambda _s: "")
    ctx = ToolContext(
        org_id=uuid.uuid4(),
        space_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        question_id=uuid.uuid4(),
        question="q",
        today=datetime.date(2026, 11, 4),
        as_of=None,
        purpose=None,
        layer=types.SimpleNamespace(),  # type: ignore[arg-type]
        model=types.SimpleNamespace(),  # type: ignore[arg-type]
        ws=Workspace(),
    )
    state = engine.State(
        event=types.SimpleNamespace(),  # type: ignore[arg-type]
        question_id=ctx.question_id,
        model=types.SimpleNamespace(json_chat=json_chat),  # type: ignore[arg-type]
        ctx=ctx,
        plan=_plan(
            {"id": "s1", "tool": "fake", "instruction": "a", "args": {"n": 0}},
            {"id": "s2", "tool": "fake", "instruction": "b", "depends_on": ["s1"]},
        ),
    )
    return types.SimpleNamespace(engine=engine, state=state, runs=runs, fixes=fixes)


def _failed(failure: str) -> Any:
    from services.worker_agent.agentic.tools import ToolResult

    return ToolResult(False, f"lỗi {failure}", failure=failure)  # type: ignore[arg-type]


def test_the_executor_is_not_handed_the_plans_invalid_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import types

    w = _step_world(monkeypatch, ["ok"])
    asked: list[str] = []

    async def json_chat(_role: str, _system: str, user: str, *_a: Any, **_k: Any) -> Any:
        asked.append(user)
        return types.SimpleNamespace(content=w.engine.ExecutorReply(args={"n": 7}))

    w.state.model = types.SimpleNamespace(json_chat=json_chat)
    step = w.state.plan.steps[0]
    # As the planner wrote "analysis": "luy_ke": a value outside the schema.
    step.args = {"n": "luy_ke"}
    result = asyncio.run(w.engine._run_step(w.state, step))
    assert result.ok and w.runs == [7]
    assert "luy_ke" not in asked[0]
    assert "Đã bỏ các tham số kế hoạch sai schema: n" in asked[0]


def test_wrong_arguments_are_written_again_with_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    w = _step_world(monkeypatch, [_failed("arguments"), "ok"])
    result = asyncio.run(w.engine._run_step(w.state, w.state.plan.steps[0]))
    assert result.ok and w.runs == [0, 1] and w.fixes == ["lỗi arguments"]


def test_a_transient_failure_runs_the_same_arguments_once_more(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = _step_world(monkeypatch, [_failed("transient")])
    result = asyncio.run(w.engine._run_step(w.state, w.state.plan.steps[0]))
    assert not result.ok and w.runs == [0, 0] and w.fixes == []


def test_a_logic_or_unavailable_failure_is_not_tried_again(monkeypatch: pytest.MonkeyPatch) -> None:
    for kind in ("logic", "unavailable", "forbidden"):
        w = _step_world(monkeypatch, [_failed(kind)])
        result = asyncio.run(w.engine._run_step(w.state, w.state.plan.steps[0]))
        assert result.failure == kind and w.runs == [0], kind


def test_wrong_arguments_stop_at_the_step_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    w = _step_world(monkeypatch, [_failed("arguments")])
    asyncio.run(w.engine._run_step(w.state, w.state.plan.steps[0]))
    assert len(w.runs) == w.engine.STEP_ATTEMPTS


def test_a_tool_past_its_timeout_is_a_transient_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    w = _step_world(monkeypatch, ["hang"], timeout=0.01)
    result = asyncio.run(w.engine._run_step(w.state, w.state.plan.steps[0]))
    assert result.failure == "transient" and w.runs == [0, 0]


def test_a_forbidden_step_is_not_replanned_and_its_dependents_are_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = _step_world(monkeypatch, [_failed("forbidden")])
    asyncio.run(w.engine.act(w.state))
    assert w.state.failed is None and w.state.plan.steps[0].status == "failed"
    assert w.engine.after_act(w.state) == "act"
    assert p.ready(w.state.plan, 3) == [] and w.state.plan.steps[1].status == "skipped"

    w = _step_world(monkeypatch, [_failed("logic")])
    asyncio.run(w.engine.act(w.state))
    assert w.state.failed is w.state.plan.steps[0] and w.engine.after_act(w.state) == "replan"


def test_a_metric_the_asker_is_denied_is_a_forbidden_failure() -> None:
    import types

    from services.worker_agent.agentic.tools.query import QueryMetric, QueryMetricArgs

    metric = types.SimpleNamespace(code="revenue", metric_id=1)
    layer = types.SimpleNamespace(
        vocabulary=types.SimpleNamespace(metrics=[metric]),
        versions={1: object()},
        blocked_metric_ids={1},
    )
    ctx = types.SimpleNamespace(layer=layer)
    denied = asyncio.run(QueryMetric().run(ctx, QueryMetricArgs(metric="revenue")))  # type: ignore[arg-type]
    unknown = asyncio.run(QueryMetric().run(ctx, QueryMetricArgs(metric="profit")))  # type: ignore[arg-type]
    assert (denied.ok, denied.failure) == (False, "forbidden")
    assert (unknown.ok, unknown.failure) == (False, "logic")
    # The asker cannot tell a denied metric from one that does not exist.
    assert denied.observation == unknown.observation.replace("profit", "revenue")


def test_a_series_longer_than_a_chart_holds_is_a_table_not_a_cut_chart() -> None:
    ws = Workspace()
    days = [[f"2013-{1 + i // 28:02d}-{1 + i % 28:02d}", str(i)] for i in range(200)]
    ws.add(title="Doanh thu theo ngày", columns=["Ngày", "Doanh thu"], rows=days, trust="certified")
    block = b.materialize(
        b.BlockDraft(type="chart", dataset="d1", chart_type="line", x="Ngày", y=["Doanh thu"]),
        ws,
        "b1",
    )
    # A line of the first 120 days would end in May with nothing saying the rest is missing.
    assert block["type"] == "table"
    assert block["row_count"] == 200


def test_the_reporter_lays_out_by_question_and_names_no_internals() -> None:
    from services.worker_agent.agentic import prompts

    # The one recipe every baseline answer followed (conclusion, evidence, insights, caveat) is gone.
    assert "mở bằng kết luận, rồi" not in prompts.REPORTER
    for rule in ("- Một con số: kpi", "- Xu hướng", "- Vì sao", "mã dataset (d1, d2)"):
        assert rule in prompts.REPORTER
    assert "thuật ngữ nội bộ" in prompts.REVIEW_ANSWER
    # "theo tháng" is a grain; a running total or a comparison is not added unasked.
    assert "không tự thêm" in prompts.EXECUTOR
