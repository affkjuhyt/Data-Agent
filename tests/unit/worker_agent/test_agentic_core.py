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


def test_two_periods_of_one_kind_are_read_side_by_side() -> None:
    import datetime

    from services.worker_agent.agentic.tools.query import side_by_side, two_periods

    months = two_periods("So sánh doanh thu tháng 8/2013 với tháng 3/2013")
    assert months is not None
    assert [label for _, label in months] == ["tháng 3/2013", "tháng 8/2013"]
    assert months[0][0].start == datetime.date(2013, 3, 1)
    assert months[0][0].end == datetime.date(2013, 4, 1)
    quarters = two_periods("Doanh thu quý 4/2012 so với quý 3/2013 theo vùng")
    assert quarters is not None and quarters[1][0].end == datetime.date(2013, 10, 1)
    years = two_periods("So sánh doanh thu theo vùng năm 2011 và 2013")
    assert years is not None and [label for _, label in years] == ["2011", "2013"]
    # Adjacent years are one comparison (two_years); one period is no comparison.
    assert two_periods("So sánh doanh thu năm 2012 và 2013") is None
    assert two_periods("Doanh thu tháng 8/2013") is None
    # The second period of a comparison is a date, never a quantity no metric measures.
    from services.worker_agent.understand.resolver import Vocabulary, quantity_words

    words = quantity_words(
        "So sánh doanh thu tháng 8/2013 với tháng 3/2013",
        Vocabulary(metrics=[], dimensions=[]),
        today=datetime.date(2014, 1, 15),
    )
    assert "3/2013" not in words
    columns, rows = side_by_side(
        ["vung", "value"],
        ["Vùng", "Doanh thu"],
        [["Europe", 1.0], ["Canada", 2.0]],
        [["Europe", 3.0], ["Pacific", 4.0]],
        ("Doanh thu 2011", "Doanh thu 2013"),
    )
    assert columns == ["Vùng", "Doanh thu 2011", "Doanh thu 2013"]
    # A member one period lacks is empty there, not dropped.
    assert rows == [["Europe", 1.0, 3.0], ["Canada", 2.0, None], ["Pacific", None, 4.0]]


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
    assert b.period_key("Doanh thu tháng 3/2013") == (2013, 3)
    assert b.period_key("Doanh thu quý 3/2013") == (2013, 9)
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

    from services.worker_agent import clarify_policy
    from services.worker_agent.understand.resolver import DimensionEntry, MetricEntry, Vocabulary

    buyers, revenue, orders = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    layer = SimpleNamespace(
        vocabulary=Vocabulary(
            metrics=[
                MetricEntry(buyers, "SO_KHACH_HANG", "Số khách hàng mua qua Internet", True),
                MetricEntry(revenue, "DOANH_THU", "Doanh thu", True),
                # Shares one word of "khách hàng" (a synonym's "hàng"), the customers two: not
                # offered beside them.
                MetricEntry(orders, "SO_DON_HANG", "Số đơn hàng Internet", True),
            ],
            dimensions=[DimensionEntry(uuid.uuid4(), "nhom_san_pham", "Nhóm sản phẩm")],
        ),
        versions={buyers: object(), revenue: object(), orders: object()},
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


def test_a_quantity_several_metrics_are_named_after_is_asked_back() -> None:
    """F2: "Lãi gộp năm 2013" was answered with Internet's alone; DS-AW's names and synonyms."""
    import datetime
    import uuid

    from services.worker_agent import clarify_policy
    from services.worker_agent.understand.resolver import DimensionEntry, MetricEntry, Vocabulary

    named = {
        "DOANH_THU": "Doanh thu",
        "DOANH_THU_INTERNET": "Doanh thu Internet",
        "LAI_GOP_INTERNET": "Lãi gộp Internet",
        "LAI_GOP_DAI_LY": "Lãi gộp đại lý",
        "BIEN_GOP_INTERNET": "Biên lợi nhuận gộp Internet",
        "SO_LUONG_BAN_INTERNET": "Số lượng bán Internet",
        "SO_LUONG_BAN_DAI_LY": "Số lượng bán đại lý",
        "SO_KHACH_HANG_INTERNET": "Số khách hàng mua qua Internet",
        "GIA_TRI_TON_KHO": "Giá trị tồn kho",
    }
    ids = {code: uuid.uuid4() for code in named}
    synonyms = {
        "doanh so": "DOANH_THU",
        "loi nhuan gop internet": "LAI_GOP_INTERNET",
        "so luong khach hang": "SO_KHACH_HANG_INTERNET",
    }
    layer = SimpleNamespace(
        vocabulary=Vocabulary(
            metrics=[MetricEntry(ids[c], c, n, True) for c, n in named.items()],
            # Cuts are named with metric words: they must not hide "đại lý" from the check.
            dimensions=[
                DimensionEntry(uuid.uuid4(), "khu_vuc", "Khu vực"),
                DimensionEntry(uuid.uuid4(), "ngay_dat_dai_ly", "Ngày đặt hàng (đại lý)"),
            ],
            synonyms={p: ("metric", str(ids[c])) for p, c in synonyms.items()},
        ),
        versions={i: object() for i in ids.values()},
        blocked_metric_ids=frozenset(),
    )
    today = datetime.date(2026, 11, 17)

    def ask(question: str) -> Any:
        return clarify_policy.same_named(question, layer, today)

    gross = ask("Lãi gộp năm 2013?")
    assert gross is not None
    assert gross.question == "“Lãi gộp” có ở nhiều chỉ số chuẩn. Bạn muốn xem chỉ số nào?"
    assert gross.options == ["Lãi gộp Internet năm 2013?", "Lãi gộp đại lý năm 2013?"]
    # "bán" folds to "bạn", a filler; the customers' synonym holds "lượng" but not "bán".
    units = ask("Số lượng bán theo khu vực năm 2013")
    assert units is not None
    assert units.options == [
        "Số lượng bán Internet theo khu vực năm 2013",
        "Số lượng bán đại lý theo khu vực năm 2013",
    ]
    # Named exactly, by a name or a synonym; "biên"/"gộp" are metric words, not analysis.
    for question in (
        "Doanh thu năm 2013",
        "Doanh số năm 2013",
        "Lãi gộp đại lý năm 2013",
        "Biên lợi nhuận gộp Internet năm 2013",
        "So sánh doanh thu theo khu vực năm 2012 và 2013",
        # A word no metric holds: not this check's question.
        "Giá cổ phiếu hôm nay",
        "Khách hàng rời bỏ là bao nhiêu?",
    ):
        assert ask(question) is None, question


def test_a_period_with_no_data_of_the_metric_is_asked_back_before_any_plan() -> None:
    """F1: "Doanh thu Internet tháng 3/2015" was answered with 12/2013's figure, q6 replanned
    around 2026; both are asked back with the nearest whole period, before the planner runs."""
    import datetime
    import types
    import uuid

    from services.worker_agent.agentic import engine
    from services.worker_agent.semantic import compiler as c
    from services.worker_agent.understand.resolver import DimensionEntry, MetricEntry, Vocabulary
    from services.worker_agent.understand.text import find_period

    revenue, budget = uuid.uuid4(), uuid.uuid4()
    layer = SimpleNamespace(
        vocabulary=Vocabulary(
            metrics=[
                MetricEntry(revenue, "DOANH_THU_INTERNET", "Doanh thu Internet", True),
                MetricEntry(budget, "TAI_CHINH_NGAN_SACH", "Số tài chính ngân sách", True),
            ],
            dimensions=[DimensionEntry(uuid.uuid4(), "phong_ban", "Phòng ban")],
        ),
        versions={revenue: object(), budget: object()},
        blocked_metric_ids=frozenset(),
    )
    today = datetime.date(2026, 11, 17)

    def state(question: str, code: str, nearest: c.Period | None, held: Any = None) -> Any:
        found = find_period(question, today)
        assert found is not None
        asked = c.Period(found.start, found.end)
        return engine.State(
            event=types.SimpleNamespace(),  # type: ignore[arg-type]
            question_id=uuid.uuid4(),
            model=types.SimpleNamespace(),  # type: ignore[arg-type]
            ctx=types.SimpleNamespace(  # type: ignore[arg-type]
                question=question,
                today=today,
                period=found,
                period_moves={code: (asked, nearest)} if nearest else {},
                period_gaps={code: (asked, held)} if held else {},
                layer=layer,
            ),
            likely=[code],
        )

    month = state(
        "Doanh thu Internet tháng 3/2015?",
        "DOANH_THU_INTERNET",
        c.Period(datetime.date(2013, 12, 1), datetime.date(2014, 1, 1)),
    )
    plan = engine._missing_period(month)
    assert plan is not None and plan.mode == "ask" and not plan.steps
    assert month.clarify is not None
    assert month.clarify.question == (
        "Chưa có dữ liệu Doanh thu Internet cho tháng 3/2015. Kỳ đầy đủ gần nhất có dữ liệu là"
        " tháng 12/2013. Bạn muốn xem kỳ đó?"
    )
    # The option is the question again, at the nearest period.
    assert month.clarify.options == ["Doanh thu Internet tháng 12/2013?"]

    q6 = state(
        "Tài chính ngân sách theo phòng ban năm 2026",
        "TAI_CHINH_NGAN_SACH",
        c.Period(datetime.date(2013, 1, 1), datetime.date(2014, 1, 1)),
    )
    assert engine._missing_period(q6) is not None
    assert q6.clarify is not None
    assert q6.clarify.options == ["Tài chính ngân sách theo phòng ban năm 2013"]
    # DS-AW's budget runs 12/2010 to 11/2011: no whole year to move to, the span is said and the
    # latest year holding data offered (q6, 17/11).
    gap = state(
        "Tài chính ngân sách theo phòng ban năm 2026",
        "TAI_CHINH_NGAN_SACH",
        None,
        c.Period(datetime.date(2010, 12, 1), datetime.date(2011, 12, 1)),
    )
    assert engine._missing_period(gap) is not None and gap.clarify is not None
    assert gap.clarify.question == (
        "Chưa có dữ liệu Số tài chính ngân sách cho năm 2026; dữ liệu chỉ có từ tháng 12/2010"
        " đến tháng 11/2011. Bạn muốn xem năm 2011?"
    )
    assert gap.clarify.options == ["Tài chính ngân sách theo phòng ban năm 2011"]

    # A period with data (nothing moved), or a quantity beyond the metric, is the planner's.
    covered = state("Doanh thu Internet năm 2013", "DOANH_THU_INTERNET", None)
    assert engine._missing_period(covered) is None
    beyond = state(
        "Giá cổ phiếu Internet tháng 3/2015",
        "DOANH_THU_INTERNET",
        c.Period(datetime.date(2013, 12, 1), datetime.date(2014, 1, 1)),
    )
    assert engine._missing_period(beyond) is None
    # Two periods compared: the planner reads them side by side.
    both = state(
        "So sánh doanh thu Internet năm 2009 và năm 2013",
        "DOANH_THU_INTERNET",
        c.Period(datetime.date(2013, 1, 1), datetime.date(2014, 1, 1)),
    )
    assert engine._missing_period(both) is None


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


def test_next_questions_are_kept_once_and_only_when_they_name_a_metric() -> None:
    import uuid

    from services.worker_agent.agentic import engine
    from services.worker_agent.understand.resolver import DimensionEntry, MetricEntry, Vocabulary

    vocab = Vocabulary(
        metrics=[MetricEntry(uuid.uuid4(), "DOANH_THU_INTERNET", "Doanh thu Internet", True)],
        dimensions=[DimensionEntry(uuid.uuid4(), "vung", "Vùng bán hàng")],
    )
    offered = [
        "Biến động doanh thu Internet theo từng dịch vụ Internet",
        "Doanh thu Internet theo quý năm 2013",
        "Doanh thu Internet theo quý năm 2013?",
        "Liệu có cần thu thập dữ liệu bổ sung cho kỳ này không?",
        "Doanh thu Internet năm 2013 là bao nhiêu?",
        "So sánh doanh thu Internet 2013 với 2012",
        "Doanh thu Internet theo vùng bán hàng năm 2013",
        "Doanh thu Internet theo nhóm sản phẩm năm 2013",
    ]
    assert engine.runnable_follow_ups(
        offered, "Doanh thu Internet năm 2013 là bao nhiêu?", vocab
    ) == [
        "Doanh thu Internet theo quý năm 2013",
        "So sánh doanh thu Internet 2013 với 2012",
        "Doanh thu Internet theo vùng bán hàng năm 2013",
    ]


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


def test_unnamed_words_on_a_certified_reading_are_not_a_gate() -> None:
    """A certified reading can leave quantity words unnamed. Those words are still computed;
    they no longer hand the question back — `unnamed` is not a present_gate hit."""
    import datetime
    import types
    import uuid

    from libs import clock
    from services.worker_agent.agentic import engine, present_gate
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
    unnamed = engine._unnamed_quantity(asked_customers)
    assert unnamed == ["khach", "hang", "mua"]
    assert (
        present_gate.before_write(
            mode="analyze",
            ws=ws,
            dropped_members=[],
            unnamed=unnamed,
            out_of_time=[],
        )
        is None
    )
    assert engine._unnamed_quantity(state("Vì sao số lượng bán Internet năm 2013 giảm")) == []
    # "Cơ cấu" folds to "co cau": how to read the quantity, not one the catalogue lacks.
    assert engine._unnamed_quantity(state("Cơ cấu số lượng bán Internet năm 2013")) == []


def test_query_metric_refuses_a_quantity_no_metric_names_without_inventing_sql() -> None:
    """Leftover words are not a catalogue refusal. The step continues, and a metric that does
    not compile comes back as a compile error with no unnamed detail."""
    import datetime
    import types
    import uuid

    from libs import clock
    from services.worker_agent.agentic.tools.query import QueryMetric, QueryMetricArgs
    from services.worker_agent.semantic import compiler as c
    from services.worker_agent.understand.resolver import MetricEntry

    units = MetricEntry(uuid.uuid4(), "SO_LUONG_BAN_INTERNET", "Số lượng bán Internet", True)
    version = c.MetricVersion(
        id=uuid.uuid4(),
        metric_code="SO_LUONG_BAN_INTERNET",
        display_name="Số lượng bán Internet",
        expression="so_luong",
        filters=(),
        unit="đơn vị",
        decimals=0,
        period_semantics="sum_over_period",
        allowed_dimension_ids=frozenset(),
        certified=True,
    )
    layer = types.SimpleNamespace(
        vocabulary=types.SimpleNamespace(
            metrics=[units], synonyms={}, dimensions=[], values=[], calendar=clock.DEFAULT
        ),
        versions={units.metric_id: version},
        blocked_metric_ids=set(),
        layer=c.Layer(measures={}, dimensions={}, entity_tables={}),
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
    assert "không tự tính" not in done.observation
    assert "unnamed" not in done.detail
    assert "không compile được" in done.observation


def test_query_metric_sends_a_missed_cut_back_as_an_argument_error() -> None:
    """F0: "Doanh thu Internet khu vực Europe" filtered on the wrong cut left "khu vực", refused as
    a quantity no metric measures; the step is now read again with the cut."""
    import datetime
    import types
    import uuid

    from libs import clock
    from services.worker_agent.agentic.tools.query import QueryMetric, QueryMetricArgs
    from services.worker_agent.understand.resolver import DimensionEntry, MetricEntry

    revenue = MetricEntry(uuid.uuid4(), "DOANH_THU_INTERNET", "Doanh thu Internet", True)
    layer = types.SimpleNamespace(
        vocabulary=types.SimpleNamespace(
            metrics=[revenue],
            synonyms={},
            dimensions=[
                DimensionEntry(uuid.uuid4(), "khu_vuc", "Khu vực"),
                DimensionEntry(uuid.uuid4(), "vung", "Vùng bán hàng"),
            ],
            values=[],
            calendar=clock.DEFAULT,
        ),
        versions={revenue.metric_id: SimpleNamespace(allowed_dimension_ids=[])},
        blocked_metric_ids=set(),
    )
    ctx = types.SimpleNamespace(
        layer=layer,
        question="Doanh thu Internet khu vực Europe năm 2013",
        today=datetime.date(2026, 11, 17),
    )
    done = asyncio.run(
        QueryMetric().run(
            ctx,  # type: ignore[arg-type]
            QueryMetricArgs(metric="DOANH_THU_INTERNET", filters={"vung": ["Europe"]}),
        )
    )
    assert not done.ok and done.failure == "arguments"
    assert "Khu vực" in done.observation and "unnamed" not in done.detail


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
    channel = types.SimpleNamespace(
        dimension_id=uuid.uuid4(), code="kenh", name="Kênh", is_time=False
    )
    day = types.SimpleNamespace(
        dimension_id=uuid.uuid4(), code="ngay_dat", name="Ngày", is_time=True
    )
    layer = types.SimpleNamespace(
        vocabulary=types.SimpleNamespace(
            metrics=[revenue],
            synonyms={},
            dimensions=[region, colour, channel, day],
            values=[],
            calendar=clock.DEFAULT,
        ),
        versions={
            revenue.metric_id: types.SimpleNamespace(
                allowed_dimension_ids={region.dimension_id, channel.dimension_id}
            )
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
    # F0: the grain the question names wins over the one the planner wrote.
    ctx.question = "Doanh thu Internet theo quý năm 2013"
    asyncio.run(
        QueryMetric().run(ctx, QueryMetricArgs(metric="DOANH_THU_INTERNET", grain="year"))  # type: ignore[arg-type]
    )
    assert compiled[-1].grain == "quarter" and compiled[-1].group_by == ["ngay_dat"]
    # F0: the count and the end a ranking question names win too ("3 … thấp nhất").
    ctx.question = "2 vùng có doanh thu Internet thấp nhất năm 2013"
    asyncio.run(
        QueryMetric().run(ctx, QueryMetricArgs(metric="DOANH_THU_INTERNET", group_by=["vung"]))  # type: ignore[arg-type]
    )
    assert (compiled[-1].top_n, compiled[-1].top_desc) == (2, False)
    # The members counted are the cut named after the count, whatever else the planner cut by;
    # a cut the question names with "theo" stays beside it.
    by_both = QueryMetricArgs(metric="DOANH_THU_INTERNET", group_by=["kenh", "vung"])
    asyncio.run(QueryMetric().run(ctx, by_both))  # type: ignore[arg-type]
    assert compiled[-1].group_by == ["vung"]
    ctx.question = "2 vùng có doanh thu Internet thấp nhất theo kênh năm 2013"
    asyncio.run(QueryMetric().run(ctx, by_both))  # type: ignore[arg-type]
    assert compiled[-1].group_by == ["kenh", "vung"]
    # F4: which member fell most over two years is read from all of them, not the top value.
    ctx.question = (
        "Doanh thu Internet theo vùng năm 2013 so với năm 2012: Vùng nào giảm nhiều nhất?"
    )
    asyncio.run(
        QueryMetric().run(
            ctx, QueryMetricArgs(metric="DOANH_THU_INTERNET", group_by=["vung"], top_n=1)
        )  # type: ignore[arg-type]
    )
    assert compiled[-1].top_n is None
    # A ranking and a comparison are the question's to ask: a count the planner set on a plain
    # breakdown, and a base period beside a question that names none, are not read.
    ctx.question = "Doanh thu Internet năm 2013 theo từng vùng"
    asyncio.run(
        QueryMetric().run(
            ctx, QueryMetricArgs(metric="DOANH_THU_INTERNET", group_by=["vung"], top_n=10)
        )  # type: ignore[arg-type]
    )
    assert compiled[-1].top_n is None
    from services.worker_agent.agentic.tools.query import unasked_dropped

    compared = QueryMetricArgs(metric="DOANH_THU_INTERNET", compare_to="same_period_last_year")
    assert unasked_dropped("Doanh thu Internet năm 2013", compared).compare_to is None
    for asks in (
        "Doanh thu Internet năm 2013 so với cùng kỳ năm trước",
        "Doanh thu Internet năm 2013 giảm bao nhiêu",
        "Doanh thu Internet năm 2012 và 2013",
    ):
        assert unasked_dropped(asks, compared).compare_to == "same_period_last_year"
    ranked = QueryMetricArgs(metric="DOANH_THU_INTERNET", group_by=["vung"], top_n=1)
    assert unasked_dropped("Vùng nào có doanh thu Internet cao nhất", ranked).top_n == 1
    assert unasked_dropped("Top 5 vùng theo doanh thu Internet", ranked).top_n == 1
    # A calendar part the question does not name is no filter beside the period it reads.
    ctx.question = "Top 10 vùng theo doanh thu Internet năm 2013"
    asyncio.run(
        QueryMetric().run(
            ctx,
            QueryMetricArgs(
                metric="DOANH_THU_INTERNET",
                group_by=["vung"],
                period="2013",
                filters={"nam_tai_chinh": ["2013"]},
            ),
        )  # type: ignore[arg-type]
    )
    assert compiled[-1].filters == {}
    ctx.question = "Doanh thu Internet theo tháng"

    refused = asyncio.run(
        QueryMetric().run(
            ctx, QueryMetricArgs(metric="DOANH_THU_INTERNET", grain="month", group_by=["mau_sac"])
        )  # type: ignore[arg-type]
    )
    assert not refused.ok and refused.failure == "arguments"
    # The refusal says how to ask again, so the replan has something to change.
    assert (
        "dùng grain" in refused.observation
        and "các chiều được phép: kenh, vung" in refused.observation
    )


def test_explore_sql_refuses_a_quantity_no_metric_in_the_catalogue_names() -> None:
    """An explore_sql row is an answer even when the catalogue does not name the quantity."""
    from services.worker_agent.agentic import present_gate

    ws = Workspace()
    ws.add(
        title="Tỷ lệ",
        columns=["ty_le"],
        rows=[[0.12]],
        trust="exploratory",
        tool="explore_sql",
        sql="SELECT 0.12",
    )
    hit = present_gate.before_write(
        mode="analyze",
        ws=ws,
        dropped_members=[],
        unnamed=["ty", "le"],
        out_of_time=[],
    )
    assert hit is None


def test_explore_sql_answer_for_an_uncatalogued_quantity_hands_the_question_back() -> None:
    """explore_sql is the reading: the catalogue gap is not handed back, and nothing is asked."""
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
    assert engine._unnamed_quantity(state) == []
    assert engine._ask_for_unnamed_question(state) is False


def test_no_figure_still_blocks_an_all_zero_period() -> None:
    from services.worker_agent.agentic import present_gate

    ws = Workspace()
    ws.add(title="t", columns=["Năm", "Doanh thu"], rows=[[2009, 0]], trust="certified")
    hit = present_gate.before_write(
        mode="analyze",
        ws=ws,
        dropped_members=[],
        unnamed=[],
        out_of_time=[],
    )
    assert hit is not None
    assert hit.target.startswith("Không có số liệu")


def test_certified_metric_still_binds_the_audit() -> None:
    from services.worker_agent.sqlgen import audit_rules
    from tests.unit.worker_agent.test_audit_rules import SPACE, TODAY

    ctx = audit_rules.context_for("Doanh thu năm 2013", SPACE, today=TODAY)
    assert ctx.metric == "Doanh thu"


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


def test_an_answer_that_read_numbers_shows_them_and_says_them() -> None:
    """F0 (17/11/2026): "Doanh thu năm 2013" kept only a callout, "3 đại lý cao nhất" a table and
    no text, "Top 3 tiểu nhóm … là:" named none of the three, and a headline showed "{{F1}}"."""
    import types
    import uuid

    from services.worker_agent.agentic import engine

    ws = Workspace()
    total = ws.add(
        title="t",
        columns=["Doanh thu"],
        rows=[[49926384.4972]],
        trust="certified",
        tool="query_metric",
        unit="USD",
    )
    top = ws.add(
        title="t",
        columns=["Đại lý", "Doanh thu đại lý"],
        rows=[["Roadway Bicycle Supply", "436921.6551"], ["Field Trip Store", "427305.5875"]],
        trust="certified",
        tool="query_metric",
        unit="USD",
    )
    assert engine._rows_said(total, 5) == "Doanh thu: 49.926.384,50 USD."
    assert engine._rows_said(top, 5) == (
        "Doanh thu đại lý: Roadway Bicycle Supply (436.921,66 USD),"
        " Field Trip Store (427.305,59 USD)."
    )
    assert engine._rows_said(top, 1) == ""

    state = engine.State(
        event=types.SimpleNamespace(),  # type: ignore[arg-type]
        question_id=uuid.uuid4(),
        model=types.SimpleNamespace(),  # type: ignore[arg-type]
        ctx=types.SimpleNamespace(ws=ws),  # type: ignore[arg-type]
        plan=_plan({"id": "s1", "tool": "query_metric", "instruction": "x", "status": "done"}),
    )
    # The last dataset read, as a table when it has rows to name; one number is a KPI.
    shown = engine._data_block(state, "b2")
    assert shown is not None and shown["type"] == "table" and shown["dataset"] == top.id
    del ws.datasets[top.id]
    kpi = engine._data_block(state, "b2")
    assert kpi is not None and kpi["type"] == "kpi" and kpi["items"][0]["value"] == 49926384.4972
    assert engine._shown_dataset(kpi) == total.id

    ws.datasets[top.id] = top
    state.composition = [
        {"type": "markdown", "id": "b1", "content": "Top 2 đại lý năm 2013 là:"},
        {"type": "table", "id": "b2", "dataset": top.id},
    ]
    engine._complete_lead_ins(state)
    assert state.composition[0]["content"] == (
        "Top 2 đại lý năm 2013 là: Roadway Bicycle Supply (436.921,66 USD),"
        " Field Trip Store (427.305,59 USD)."
    )

    # The headline is filled and checked like any sentence.
    known = {"F1": verify.Fact("F1", 768.0751, "768,08", "Giá trị đơn")}
    assert verify.fill("Giá trị đơn bình quân là {{F1}} USD", known, set()).text == (
        "Giá trị đơn bình quân là 768,08 USD"
    )
    assert verify.fill("Doanh thu đạt 49.926.384,50 USD", known, set()).text == ""


def test_explain_change_tries_real_cuts_shows_two_and_checks_the_premise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F3 (q7, "Vì sao doanh thu tháng 8/2013 giảm?"): the month rose, the answer explained a fall
    by "Năm tài chính", a calendar cut two compared periods always differ by."""
    import datetime
    import types
    import uuid

    from services.worker_agent.agentic import engine
    from services.worker_agent.agentic.tools import explain
    from services.worker_agent.agentic.tools.base import ToolResult
    from services.worker_agent.understand.resolver import DimensionEntry

    dims = {
        "nam_tai_chinh": ("Năm tài chính", [["FY2014", 110, 100]]),
        "vung": ("Vùng", [["Europe", 60, 40], ["Pacific", 50, 60]]),
        "nhom_hang": ("Nhóm hàng", [["Bikes", 100, 90], ["Clothing", 10, 10]]),
        "kenh": ("Kênh", [["Online", 110, 100]]),
    }
    monkeypatch.setattr(
        explain.analyst,
        "describe_metric",
        lambda _layer, _code: {
            "name": "Doanh thu",
            "dimensions": [{"dimension": c, "name": n} for c, (n, _) in dims.items()],
        },
    )
    ws = Workspace()
    asked: list[list[str]] = []

    async def run(_self: Any, _ctx: Any, args: Any) -> ToolResult:
        asked.append(list(args.group_by))
        if not args.group_by:
            ds = ws.add(
                title="t", columns=["value", "base_value"], rows=[[110, 100]], trust="certified"
            )
        else:
            cut = args.group_by[0]
            ds = ws.add(
                title="t",
                columns=[cut, "value", "base_value"],
                rows=dims[cut][1],
                trust="certified",
            )
        return ToolResult(True, "ok", [ds.id])

    monkeypatch.setattr(explain.QueryMetric, "run", run)
    ctx = types.SimpleNamespace(
        ws=ws,
        layer=types.SimpleNamespace(
            vocabulary=types.SimpleNamespace(
                dimensions=[DimensionEntry(uuid.uuid4(), c, n) for c, (n, _) in dims.items()]
            )
        ),
        changes=[],
        step_id="s1",
    )
    done = asyncio.run(
        explain.ExplainChange().run(
            ctx,  # type: ignore[arg-type]
            explain.ExplainChangeArgs(metric="DOANH_THU", period="2013-08"),
        )
    )
    assert done.ok
    # The calendar cut is never tried; the total is read first.
    assert asked == [[], ["vung"], ["nhom_hang"], ["kenh"]]
    # One member holds all of a change: it explains nothing. The two real cuts are shown.
    shown = [ws.datasets[d].title for d in done.datasets]
    # Ranked by concentration: all of Nhóm hàng's change is Bikes', Vùng's is split.
    assert shown == ["Đóng góp vào thay đổi theo Nhóm hàng", "Đóng góp vào thay đổi theo Vùng"]
    assert ctx.changes == [
        {
            "metric": "DOANH_THU",
            "name": "Doanh thu",
            "period": "2013-08",
            "compare_to": "previous_period",
            "now": 110,
            "then": 100,
            "unit": None,
        }
    ]

    state = engine.State(
        event=types.SimpleNamespace(),  # type: ignore[arg-type]
        question_id=uuid.uuid4(),
        model=types.SimpleNamespace(),  # type: ignore[arg-type]
        ctx=types.SimpleNamespace(  # type: ignore[arg-type]
            question="Vì sao doanh thu tháng 8/2013 giảm?",
            changes=ctx.changes,
            today=datetime.date(2026, 11, 17),
        ),
    )
    assert engine._false_premise(state) == (
        "Doanh thu tháng 8/2013 không giảm mà tăng 10,0% so với tháng 7/2013 (từ 100 lên 110)."
    )
    state.ctx.question = "Vì sao doanh thu tháng 8/2013 tăng?"  # type: ignore[union-attr]
    assert engine._false_premise(state) == ""


def test_a_follow_up_is_read_as_the_whole_question_it_continues() -> None:
    """F4: "Doanh thu theo vùng năm 2013" → "Còn năm 2012?" → "Vùng nào giảm nhiều nhất?"."""
    import datetime
    import uuid

    from services.worker_agent.agentic import engine
    from services.worker_agent.understand.resolver import DimensionEntry, MetricEntry, Vocabulary

    buyers = uuid.uuid4()
    vocab = Vocabulary(
        metrics=[
            MetricEntry(uuid.uuid4(), "DOANH_THU", "Doanh thu", True),
            MetricEntry(buyers, "SO_KHACH_HANG", "Số khách hàng mua qua Internet", True),
        ],
        dimensions=[
            DimensionEntry(uuid.uuid4(), "vung", "Vùng bán hàng"),
            DimensionEntry(uuid.uuid4(), "nhom_hang", "Nhóm sản phẩm"),
            DimensionEntry(uuid.uuid4(), "quoc_gia_khach", "Quốc gia khách hàng"),
        ],
        synonyms={"so khach hang": ("metric", str(buyers))},
    )
    today = datetime.date(2026, 11, 17)

    def reading(period: str) -> dict[str, Any]:
        return {
            "metric": "DOANH_THU",
            "metric_name": "Doanh thu",
            "period": period,
            "group_by": ["vung"],
            "filters": {},
        }

    first = [reading("2013")]
    assert (
        engine._standalone("Còn năm 2012?", first, vocab, today)
        == "Doanh thu theo Vùng bán hàng năm 2012"
    )
    both = [reading("2012"), reading("2013")]
    assert engine._standalone("Vùng nào giảm nhiều nhất?", both, vocab, today) == (
        "Doanh thu theo Vùng bán hàng năm 2013 so với năm 2012: Vùng nào giảm nhiều nhất?"
    )
    # A cut the follow-up names replaces the one before.
    assert engine._standalone("Còn theo nhóm sản phẩm thì sao?", first, vocab, today).startswith(
        "Doanh thu theo Nhóm sản phẩm năm 2013"
    )
    # A quantity of its own, or a metric named whole, is a new question; nothing to follow, too.
    for new in ("Tồn kho năm 2013", "Số khách hàng năm 2013"):
        assert engine._standalone(new, first, vocab, today) == "", new
    assert engine._standalone("Còn năm 2012?", [], vocab, today) == ""


def test_which_member_fell_most_is_said_from_the_comparison() -> None:
    """F4: "Vùng nào giảm nhiều nhất?" came back as three tables and no sentence (18/11)."""
    import types
    import uuid

    from services.worker_agent.agentic import engine

    ws = Workspace()
    ws.add(
        title="t",
        columns=["Vùng bán hàng", "Doanh thu", "Doanh thu (kỳ so sánh)"],
        rows=[["Southwest", 9734390.81, 8513723.41], ["Northeast", 2400000.0, 3000000.0]],
        trust="certified",
        tool="query_metric",
        unit="USD",
    )

    def said(question: str) -> str:
        state = engine.State(
            event=types.SimpleNamespace(),  # type: ignore[arg-type]
            question_id=uuid.uuid4(),
            model=types.SimpleNamespace(),  # type: ignore[arg-type]
            ctx=types.SimpleNamespace(ws=ws, question=question),  # type: ignore[arg-type]
        )
        return engine._most_changed(state)

    assert said("Doanh thu theo vùng năm 2013 so với năm 2012: Vùng nào giảm nhiều nhất?") == (
        "Vùng bán hàng giảm nhiều nhất: Northeast (từ 3.000.000 xuống 2.400.000 USD, 20,0%)."
    )
    assert said("Vùng nào tăng mạnh nhất?").startswith("Vùng bán hàng tăng nhiều nhất: Southwest")
    assert said("Doanh thu theo vùng năm 2013") == ""


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
    # The catalogue rules (AE-07) read a whole SpaceLayer this test does not build; the explore
    # they would feed is faked above, so they have nothing to check here.
    monkeypatch.setattr(
        "services.worker_agent.sqlgen.audit_rules.context_for", lambda *_a, **_k: None
    )

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
    from services.worker_agent import quality_gate

    # DF-08: the one gate decides; a held-back table hands the turn to the governed path, which
    # refuses with QUALITY_BLOCKED, and a failing lower rule is a caveat.
    gates = iter(
        [
            quality_gate.Gate(
                blocked=[quality_gate.Held(table, "Bán hàng", "có kiểm tra chất lượng mức cao")]
            ),
            quality_gate.Gate(
                warnings=[quality_gate.Held(table, "Bán hàng", "có kiểm tra chất lượng chưa đạt")]
            ),
        ]
    )

    async def check(*_a: object, **_k: object) -> quality_gate.Gate:
        return next(gates)

    monkeypatch.setattr(signals.quality_gate, "check", check)
    ctx = types.SimpleNamespace(ws=ws, layer=layer, org_id=uuid.uuid4(), user_id=uuid.uuid4())
    found = asyncio.run(signals.read(ctx))  # type: ignore[arg-type]
    assert found.out_of_time == ["Bán hàng"]
    assert any("đổi cấu trúc" in c for c in found.caveats)
    results = iter(
        [
            [types.SimpleNamespace(member_ref=table, freshness_state="fresh")],
            [],
        ]
    )
    found = asyncio.run(signals.read(ctx))  # type: ignore[arg-type]
    assert not found.out_of_time
    assert found.caveats == ["Bảng Bán hàng có kiểm tra chất lượng chưa đạt."]


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


def test_a_step_the_question_does_not_ask_for_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """New set of 18/11: the review added explain_change to "Doanh thu năm 2012 so với năm 2011",
    and the answer showed the drivers of a change no one asked about. What moved a figure is
    asked with a "why", in any language a pack reads."""
    from services.worker_agent.agentic.tools.explain import ExplainChange

    assert ExplainChange.asked("Vì sao doanh thu tháng 8/2013 giảm?")
    assert ExplainChange.asked("Yếu tố nào làm doanh thu quý 3 giảm?")
    assert ExplainChange.asked("Why did revenue fall in August 2013?")
    assert not ExplainChange.asked("Doanh thu năm 2012 so với năm 2011")
    assert not ExplainChange.asked("Doanh thu và lãi gộp Internet năm 2013")

    w = _step_world(monkeypatch, ["ok"])
    monkeypatch.setattr(type(w.engine.TOOLS["fake"]), "asked", classmethod(lambda _c, _q: False))
    asyncio.run(w.engine.act(w.state))
    assert w.runs == [] and w.state.plan.steps[0].status == "skipped"
    assert p.ready(w.state.plan, 3) == [] and w.state.plan.steps[1].status == "skipped"


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


def test_a_null_written_as_text_leaves_an_optional_query_argument_out() -> None:
    # 16/11/2026: the planner wrote analysis "none" and the stock question was never read.
    from services.worker_agent.agentic.tools.query import QueryMetricArgs

    args = QueryMetricArgs.model_validate(
        {
            "metric": "GIA_TRI_TON_KHO",
            "period": "2014-06",
            "analysis": "none",
            "compare_to": "Null",
            "grain": "",
            "top_n": "none",
        }
    )
    assert (args.analysis, args.compare_to, args.grain, args.top_n) == (None, None, None, None)
    assert args.period == "2014-06"
    # A real value is still held to the schema.
    with pytest.raises(ValueError):
        QueryMetricArgs.model_validate({"metric": "X", "analysis": "average"})
    # The metric is not optional: "none" stays a (wrong) code, not a missing one.
    assert QueryMetricArgs.model_validate({"metric": "none"}).metric == "none"


def test_a_fiscal_cut_a_span_from_the_year_start_and_a_relative_comparison_are_read() -> None:
    """Holdout of 18/11: the question names them, the 4B planner left them out."""
    import datetime
    import types
    import uuid

    from services.worker_agent.agentic.tools.query import (
        QueryMetricArgs,
        _calendar_cut,
        _relative_reading,
    )
    from services.worker_agent.understand.resolver import DimensionEntry
    from services.worker_agent.understand.text import find_period

    fiscal = DimensionEntry(uuid.uuid4(), "nam_tai_chinh", "Năm tài chính")
    region = DimensionEntry(uuid.uuid4(), "khu_vuc", "Khu vực")
    vocab = types.SimpleNamespace(dimensions=[fiscal, region])
    allowed = {fiscal.dimension_id, region.dimension_id}
    assert _calendar_cut("Doanh thu Internet năm tài chính 2013", vocab, allowed) == {  # type: ignore[arg-type]
        "nam_tai_chinh": ["2013"]
    }
    # Not a calendar part, or not allowed for the metric: no cut.
    assert _calendar_cut("Doanh thu khu vực 2013", vocab, allowed) == {}  # type: ignore[arg-type]
    assert _calendar_cut("Doanh thu năm tài chính 2013", vocab, {region.dimension_id}) == {}  # type: ignore[arg-type]

    today = datetime.date(2026, 11, 18)

    def read(question: str, **args: Any) -> QueryMetricArgs:
        ctx = types.SimpleNamespace(question=question, period=find_period(question, today))
        return _relative_reading(ctx, QueryMetricArgs(metric="M", **args), set())  # type: ignore[arg-type]

    ytd = read(
        "Doanh thu Internet luỹ kế từ đầu năm đến hết tháng 9/2012",
        period="2012-09-01/2012-10-01",
        analysis="running_total",
    )
    assert (ytd.period, ytd.analysis) == ("2012-01-01/2012-10-01", None)
    yoy = read("Doanh thu đại lý năm 2012 so với cùng kỳ năm trước", period="2012")
    assert yoy.compare_to == "same_period_last_year" and yoy.period == "2012"
    qoq = read("Doanh thu Internet quý 2/2013 so với quý trước")
    assert qoq.compare_to == "previous_period" and qoq.period == "2013-04-01/2013-07-01"
    plain = read("Doanh thu Internet năm 2013", period="2013")
    assert plain.compare_to is None and plain.period == "2013"


def test_what_the_space_does_not_measure_or_cut_by_is_said_before_any_plan() -> None:
    """Eval and holdout of 18/11: refused or asked back, never read as the nearest metric/cut."""
    import datetime
    import uuid

    from services.worker_agent import clarify_policy
    from services.worker_agent.understand.resolver import (
        DimensionEntry,
        MetricEntry,
        ValueEntry,
        Vocabulary,
    )

    buyers, revenue, stock = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    group = uuid.uuid4()
    layer = SimpleNamespace(
        vocabulary=Vocabulary(
            metrics=[
                MetricEntry(revenue, "DOANH_THU", "Doanh thu", True),
                MetricEntry(buyers, "SO_KHACH_HANG", "Số khách hàng mua qua Internet", True),
                MetricEntry(stock, "GIA_TRI_TON_KHO", "Giá trị tồn kho", True),
            ],
            dimensions=[
                DimensionEntry(group, "nhom_hang", "Nhóm sản phẩm"),
                DimensionEntry(uuid.uuid4(), "ten_dai_ly", "Đại lý"),
                DimensionEntry(uuid.uuid4(), "ngay_dat", "Ngày đặt hàng"),
            ],
            values=[ValueEntry("nhom_hang", "Bikes", "Bikes")],
            synonyms={
                "so khach hang": ("metric", str(buyers)),
                "nhom hang": ("dimension", str(group)),
            },
        ),
    )
    today = datetime.date(2026, 11, 18)
    refused = clarify_policy.out_of_scope("Giá cổ phiếu của công ty hôm nay", layer, today)
    assert refused is not None and "“Giá cổ phiếu của công ty”" in refused
    # Half a metric named: asked back with the closest metrics elsewhere; a greeting is no data
    # question; a metric named is answered.
    for question in ("Khách hàng rời bỏ là bao nhiêu?", "Xin chào", "Doanh thu năm 2013"):
        assert clarify_policy.out_of_scope(question, layer, today) is None, question
    # A table the question names is the tables' to try, not out of the space.
    loans = "Số khoản vay theo mã chi nhánh ngày 30/09/2026"
    assert clarify_policy.out_of_scope(loans, layer, today) is not None
    layer.table_names = ("Khoản vay",)
    assert clarify_policy.out_of_scope(loans, layer, today) is None
    # A column of the tables is the tables' to cut by.
    assert clarify_policy.unknown_cut(loans, layer, today) is not None
    layer.column_names = ("Mã chi nhánh",)
    assert clarify_policy.unknown_cut(loans, layer, today) is None

    asked = clarify_policy.unknown_cut("Doanh thu theo lý do mua hàng năm 2013", layer, today)
    assert asked is not None and "“lý do mua hàng”" in asked.question
    # "lý" of "đại lý", "hàng" of "ngày đặt hàng": single words do not make a cut known.
    assert clarify_policy.unknown_cut("Doanh thu theo ngày giao hàng tháng 12/2013", layer, today)
    # The cut's words are read after any pack's "by": English too.
    asked = clarify_policy.unknown_cut("Revenue by sales reason in 2013", layer, today)
    assert asked is not None and "“sales reason”" in asked.question
    for question in (
        "Doanh thu năm 2013 theo từng nhóm hàng",
        "Doanh thu năm 2013 theo các nhóm hàng",
        "Revenue by Nhóm sản phẩm in 2013",
        "Top 10 sản phẩm theo doanh thu năm 2013",
        "Doanh thu theo tháng năm 2013",
        "Doanh thu theo ngày",
    ):
        assert clarify_policy.unknown_cut(question, layer, today) is None, question


def test_named_members_the_counted_cut_and_an_unasked_breakdown_are_read_from_the_question() -> (
    None
):
    """Holdout of 18/11: "loại Specialty Bike Shop" unfiltered, "khu vực Pacific" filtered on the
    wrong cut, "3 nhóm sản phẩm" cut by sub-category, a total cut by product."""
    import uuid

    from services.worker_agent.agentic.tools.query import (
        _asks_breakdown,
        cut_after_count,
        named_cut,
        named_cuts,
        named_members,
    )
    from services.worker_agent.understand.resolver import (
        DimensionEntry,
        MetricEntry,
        ValueEntry,
        Vocabulary,
    )

    dims = [
        DimensionEntry(uuid.uuid4(), "loai_dai_ly", "Loại đại lý"),
        DimensionEntry(uuid.uuid4(), "ten_dai_ly", "Đại lý"),
        DimensionEntry(uuid.uuid4(), "khu_vuc", "Khu vực"),
        DimensionEntry(uuid.uuid4(), "vung", "Vùng bán hàng"),
        DimensionEntry(uuid.uuid4(), "quoc_gia", "Quốc gia bán"),
        DimensionEntry(uuid.uuid4(), "nhom_hang", "Nhóm sản phẩm"),
        DimensionEntry(uuid.uuid4(), "tieu_nhom", "Tiểu nhóm sản phẩm"),
    ]
    vocab = Vocabulary(
        metrics=[
            MetricEntry(uuid.uuid4(), "DOANH_THU", "Doanh thu", True),
            MetricEntry(uuid.uuid4(), "DOANH_THU_DAI_LY", "Doanh thu đại lý", True),
        ],
        dimensions=dims,
        values=[
            ValueEntry("loai_dai_ly", "Specialty Bike Shop", "Specialty Bike Shop"),
            ValueEntry("khu_vuc", "Pacific", "Pacific"),
            ValueEntry("vung", "France", "France"),
            ValueEntry("quoc_gia", "France", "France"),
            ValueEntry("nhom_hang", "Bikes", "Bikes"),
            ValueEntry("tieu_nhom", "Road Bikes", "Road Bikes"),
        ],
    )
    allowed = {d.dimension_id for d in dims}
    assert named_members("Doanh thu đại lý loại Specialty Bike Shop năm 2013", vocab, allowed) == {
        "loai_dai_ly": ["Specialty Bike Shop"]
    }
    assert named_members("Doanh thu khu vực Pacific năm 2012", vocab, allowed) == {
        "khu_vuc": ["Pacific"]
    }
    # A name two cuts hold says no cut; the longer name wins over the one inside it.
    assert named_members("Doanh thu bán ở France năm 2013", vocab, allowed) == {}
    assert named_members("Doanh thu Road Bikes năm 2013", vocab, allowed) == {
        "tieu_nhom": ["Road Bikes"]
    }
    assert cut_after_count("3 nhóm sản phẩm có doanh thu cao nhất", vocab, allowed) == "nhom_hang"
    assert cut_after_count("Top 5 tiểu nhóm sản phẩm theo doanh thu", vocab, allowed) == "tieu_nhom"
    # A cut is named by each side of "A / B" and by its synonyms too (main and new sets, 18/11).
    state = DimensionEntry(uuid.uuid4(), "tinh_bang", "Bang / tỉnh")
    vocab.dimensions.append(state)
    vocab.synonyms["nhom hang"] = ("dimension", str(dims[5].dimension_id))
    allowed.add(state.dimension_id)
    assert cut_after_count("Top 3 bang có doanh thu cao nhất", vocab, allowed) == "tinh_bang"
    assert named_cut("Doanh thu đại lý năm 2013 theo từng nhóm hàng", vocab, allowed) == "nhom_hang"
    assert named_cut("Doanh thu theo tỉnh năm 2013", vocab, allowed) == "tinh_bang"
    assert named_cut("Doanh thu theo tiểu nhóm sản phẩm", vocab, allowed) == "tieu_nhom"
    # Every cut named anywhere, a longer name holding the words of a shorter one and a metric's
    # name holding its own.
    assert named_cuts("Doanh thu đại lý theo các nhóm sản phẩm năm 2011", vocab, allowed) == {
        "nhom_hang"
    }
    assert named_cuts("Doanh thu đại lý theo loại đại lý và khu vực", vocab, allowed) == {
        "loai_dai_ly",
        "khu_vuc",
    }
    assert not _asks_breakdown("Doanh thu đại lý loại Specialty Bike Shop năm 2013", vocab, None)
    assert _asks_breakdown("Doanh thu đại lý theo khu vực năm 2013", vocab, None)
    assert _asks_breakdown("Cơ cấu doanh thu đại lý khu vực năm 2013", vocab, None)


def test_an_account_named_with_its_gloss_is_read_on_a_metric_that_cuts_by_accounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Holdout of 18/11: "Chi phí lương Salaries thực tế" refused "phí", "Trade Sales" read on a
    metric with no accounts, "Inventory" taken for an account."""
    import asyncio
    import datetime
    import types
    import uuid

    from libs import clock
    from services.worker_agent.agentic.tools.query import (
        QueryMetric,
        QueryMetricArgs,
        named_members,
    )
    from services.worker_agent.understand.resolver import (
        DimensionEntry,
        MetricEntry,
        ValueEntry,
        Vocabulary,
    )

    account = DimensionEntry(uuid.uuid4(), "tai_khoan", "Tài khoản")
    actual = MetricEntry(uuid.uuid4(), "TAI_CHINH_THUC_TE", "Số tài chính thực tế", True)
    budget = MetricEntry(uuid.uuid4(), "TAI_CHINH_NGAN_SACH", "Số tài chính ngân sách", True)
    quota = MetricEntry(uuid.uuid4(), "CHI_TIEU_DOANH_SO", "Chỉ tiêu doanh số", True)
    stock = MetricEntry(uuid.uuid4(), "TON_KHO", "Tồn kho", True)
    vocab = Vocabulary(
        metrics=[actual, budget, quota, stock],
        dimensions=[account],
        values=[
            ValueEntry("tai_khoan", "Trade Sales", "Trade Sales"),
            ValueEntry("tai_khoan", "Inventory", "Inventory"),
        ],
        synonyms={"inventory": ("metric", str(stock.metric_id))},
    )
    everywhere = {account.dimension_id}
    assert named_members("Inventory cuối năm 2013", vocab, everywhere) == {}
    # Principle A: a question that calls the metric by its name asks no other quantity; the
    # words left are how it is said. More than half a name counts with a member of its cut.
    from services.worker_agent.understand.resolver import names_metric

    vocab.synonyms["so thuc te"] = ("metric", str(actual.metric_id))
    assert names_metric("Cho mình xin số tài chính thực tế quý 3", actual, vocab)
    # A filler ("số") may be left out when the words left call nothing else; more than half of
    # the words that are no filler count with a member of a cut the metric is read by.
    assert names_metric("Chi phí lương Salaries thực tế năm 2013", actual, vocab)
    aov = MetricEntry(uuid.uuid4(), "GIA_TRI_DON", "Giá trị đơn bình quân Internet", True)
    assert not names_metric("Giá trị đơn Internet nhóm Bikes", aov, vocab)
    assert names_metric("Giá trị đơn Internet nhóm Bikes", aov, vocab, member_named=True)
    assert not names_metric("Khách hàng rời bỏ năm 2013", actual, vocab, member_named=True)
    # The main set of 18/11: "số khách hàng" is not named by "Tỷ lệ khách hàng rời bỏ"; the
    # longest name held wins ("doanh thu bán sỉ", the reseller's, over "Doanh thu").
    buyers = MetricEntry(uuid.uuid4(), "SO_KHACH_HANG", "Số khách hàng mua qua Internet", True)
    revenue = MetricEntry(uuid.uuid4(), "DOANH_THU", "Doanh thu", True)
    reseller = MetricEntry(uuid.uuid4(), "DOANH_THU_DAI_LY", "Doanh thu đại lý", True)
    sales = Vocabulary(
        metrics=[buyers, revenue, reseller],
        dimensions=[DimensionEntry(uuid.uuid4(), "quoc_gia_khach", "Quốc gia khách hàng")],
        values=[],
        synonyms={
            "so khach hang": ("metric", str(buyers.metric_id)),
            "doanh thu ban si": ("metric", str(reseller.metric_id)),
        },
    )
    assert not names_metric("Tỷ lệ khách hàng rời bỏ năm 2013", buyers, sales)
    assert names_metric("Số khách hàng năm 2013", buyers, sales)
    assert not names_metric("Tổng doanh thu bán sỉ năm 2012", revenue, sales)
    assert names_metric("Tổng doanh thu bán sỉ năm 2012", reseller, sales)
    assert names_metric("Tổng doanh thu năm 2012", revenue, sales)
    layer = types.SimpleNamespace(
        vocabulary=vocab,
        versions={
            actual.metric_id: SimpleNamespace(allowed_dimension_ids=[account.dimension_id]),
            budget.metric_id: SimpleNamespace(allowed_dimension_ids=[account.dimension_id]),
            quota.metric_id: SimpleNamespace(allowed_dimension_ids=[]),
            stock.metric_id: SimpleNamespace(allowed_dimension_ids=[]),
        },
        blocked_metric_ids=set(),
        layer=object(),
    )
    # Two metrics cut by accounts, neither named more: the step is sent back with both.
    ctx = types.SimpleNamespace(
        layer=layer,
        question="Doanh số Trade Sales năm 2011",
        today=datetime.date(2026, 11, 18),
        period=None,
        period_moves={},
    )
    vocab.calendar = clock.DEFAULT
    done = asyncio.run(
        QueryMetric().run(ctx, QueryMetricArgs(metric="CHI_TIEU_DOANH_SO"))  # type: ignore[arg-type]
    )
    assert not done.ok and done.failure == "arguments"
    assert "Số tài chính thực tế" in done.observation and "Tài khoản" in done.observation
    # "thực tế" names one of them: the step is read on it, filtered on the account.
    from services.worker_agent.semantic import compiler as c

    compiled: list[Any] = []

    def stop(request: Any, *_a: Any, **_k: Any) -> Any:
        compiled.append(request)
        raise c.CompileError("NO_JOIN_PATH", "stop here")

    monkeypatch.setattr(c, "compile_metric", stop)
    ctx.question = "Doanh số Trade Sales thực tế năm 2011"
    asyncio.run(QueryMetric().run(ctx, QueryMetricArgs(metric="CHI_TIEU_DOANH_SO")))  # type: ignore[arg-type]
    assert compiled[-1].metric is layer.versions[actual.metric_id]
    assert compiled[-1].filters == {"tai_khoan": ["Trade Sales"]}


def test_a_member_withheld_from_the_index_is_named_by_its_synonym() -> None:
    """ "Doanh thu Internet từ khách hàng nữ": Giới tính is personal data, its values are not
    indexed; the steward's synonym "khách hàng nữ" → F names the member (18/11)."""
    import datetime
    import uuid

    from services.worker_agent.agentic.tools.query import named_members
    from services.worker_agent.understand.resolver import DimensionEntry, Vocabulary, quantity_words

    gender = DimensionEntry(uuid.uuid4(), "gioi_tinh", "Giới tính")
    vocab = Vocabulary(
        metrics=[],
        dimensions=[gender],
        synonyms={"khach hang nu": ("value", "gioi_tinh", "F")},
    )
    asked = "Doanh thu Internet từ khách hàng nữ năm 2013"
    assert named_members(asked, vocab, {gender.dimension_id}) == {"gioi_tinh": ["F"]}
    words = quantity_words(
        asked, vocab, today=datetime.date(2026, 11, 18), filters={"gioi_tinh": ["F"]}
    )
    assert "nu" not in words and "khach" not in words


def test_an_answer_over_readings_that_all_came_back_empty_is_a_refusal() -> None:
    import types
    import uuid

    from services.worker_agent.agentic import engine

    ws = Workspace()
    ws.add(title="t", columns=["value"], rows=[], trust="certified", tool="query_metric")
    state = engine.State(
        event=types.SimpleNamespace(),  # type: ignore[arg-type]
        question_id=uuid.uuid4(),
        model=types.SimpleNamespace(),  # type: ignore[arg-type]
        ctx=types.SimpleNamespace(ws=ws),  # type: ignore[arg-type]
        composition=[{"type": "markdown", "id": "b1", "content": "Không có dữ liệu năm 2013."}],
    )
    assert engine._read_nothing(state)
    ws.add(title="t", columns=["value"], rows=[[5]], trust="certified", tool="query_metric")
    assert not engine._read_nothing(state)


def test_a_reading_that_holds_no_figure_is_not_shown_beside_one_that_does() -> None:
    """ "Số cuộc gọi tổng đài tháng 5/2014": 12/2013 read first (one NULL row), May 2014 next."""
    import types
    import uuid

    from services.worker_agent.agentic import engine

    ws = Workspace()
    empty = ws.add(
        title="t", columns=["Số cuộc gọi"], rows=[[None]], trust="certified", tool="query_metric"
    )
    held = ws.add(
        title="t", columns=["Số cuộc gọi"], rows=[[43349]], trust="certified", tool="query_metric"
    )
    state = engine.State(
        event=types.SimpleNamespace(),  # type: ignore[arg-type]
        question_id=uuid.uuid4(),
        model=types.SimpleNamespace(),  # type: ignore[arg-type]
        ctx=types.SimpleNamespace(ws=ws),  # type: ignore[arg-type]
        composition=[
            {
                "type": "kpi",
                "id": "b1",
                "items": [{"dataset": empty.id, "value": None, "label": "Số cuộc gọi"}],
            },
            {"type": "markdown", "id": "b2", "content": "x"},
        ],
    )
    assert not engine._holds_figures(empty) and engine._holds_figures(held)
    engine._drop_empty_shown(state)
    assert [b["id"] for b in state.composition] == ["b2"]
    assert not engine._read_nothing(state)
    del ws.datasets[held.id]
    state.composition = [
        {
            "type": "kpi",
            "id": "b1",
            "items": [{"dataset": empty.id, "value": None, "label": "Số cuộc gọi"}],
        }
    ]
    assert engine._read_nothing(state)


@pytest.mark.parametrize(
    ("audience", "rounds", "options"),
    [
        ("executive", 0, ["A", "B", "C"]),
        ("analyst", 0, ["A", "B", "C", "D", "E"]),
        ("executive", 1, None),
        ("analyst", 3, None),
    ],
)
def test_ae_10_the_ask_user_tool_asks_within_the_clarify_budget(
    monkeypatch: pytest.MonkeyPatch, audience: str, rounds: int, options: list[str] | None
) -> None:
    # A-3: the tool's Clarify went straight to the turn; now it is held to the audience's options
    # and rounds like the planner's mode=ask (clarify_policy.gate).
    from services.worker_agent.agentic.tools import ToolResult
    from services.worker_agent.agentic.tools.base import Clarify

    asked = ToolResult(True, "đã hỏi lại", clarify=Clarify("Chỉ số nào?", list("ABCDEF")))
    w = _step_world(monkeypatch, [asked])
    w.state.audience, w.state.prior_clarify_rounds = audience, rounds
    asyncio.run(w.engine.act(w.state))
    assert (w.state.clarify.options if w.state.clarify else None) == options


def test_a_cut_the_space_does_not_model_is_answered_as_the_total_and_said() -> None:
    """ASK_ENGINE_SPEC §9: "theo lý do mua hàng" had no option to pick; the total is answered,
    the planner is told, and the caveat names the cut, as BR-81 drops a cut a metric does not
    allow (24/11)."""
    import datetime
    import types
    import uuid

    from services.worker_agent.agentic import engine
    from services.worker_agent.understand.resolver import DimensionEntry, MetricEntry, Vocabulary

    revenue = uuid.uuid4()
    layer = SimpleNamespace(
        vocabulary=Vocabulary(
            metrics=[MetricEntry(revenue, "DOANH_THU", "Doanh thu", True)],
            dimensions=[DimensionEntry(uuid.uuid4(), "nhom_hang", "Nhóm sản phẩm")],
        ),
        table_names=(),
        column_names=(),
    )

    def state(question: str) -> engine.State:
        return engine.State(
            event=types.SimpleNamespace(),  # type: ignore[arg-type]
            question_id=uuid.uuid4(),
            model=types.SimpleNamespace(),  # type: ignore[arg-type]
            ctx=types.SimpleNamespace(  # type: ignore[arg-type]
                question=question, today=datetime.date(2014, 1, 15), layer=layer
            ),
        )

    s = state("Doanh thu theo lý do mua hàng năm 2013")
    assert engine._unknown_cut(s) is None
    assert s.notes == [
        "Dữ liệu chưa có chiều “lý do mua hàng” để chia: số trả lời là số tổng hợp, không chia"
        " theo chiều đó."
    ]
    assert "lý do mua hàng" in s.coverage
    known = state("Doanh thu theo nhóm sản phẩm năm 2013")
    assert engine._unknown_cut(known) is None and known.notes == []
