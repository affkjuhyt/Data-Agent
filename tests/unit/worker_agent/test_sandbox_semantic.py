"""AL-06: what the analysis code computes is checked for meaning: ten cases refused with the
right rule, and what is right let through."""

from __future__ import annotations

from typing import Any

import pytest

from services.worker_agent.agentic.tools.output_check import OutputColumn, after_run, before_run
from services.worker_agent.agentic.workspace import Workspace

pytestmark = [pytest.mark.unit, pytest.mark.req("AL-06")]


def _ws() -> dict[str, Any]:
    ws = Workspace()
    # d1: a conversion rate by region (a ratio: its values do not add up).
    ws.add(
        title="Tỷ lệ chuyển đổi theo vùng",
        columns=["Vùng", "Tỷ lệ"],
        rows=[["Bắc", 0.1], ["Nam", 0.2]],
        trust="certified",
        unit="%",
        reading={"metric_name": "Tỷ lệ chuyển đổi", "additivity": "none"},
    )
    # d2: revenue by region, in billions (adds up).
    ws.add(
        title="Doanh thu theo vùng",
        columns=["Vùng", "Doanh thu"],
        rows=[["Bắc", 10], ["Nam", 20]],
        trust="certified",
        unit="tỷ đồng",
        reading={"metric_name": "Doanh thu", "additivity": "full"},
    )
    # d3: orders by region, a count.
    ws.add(
        title="Số đơn theo vùng",
        columns=["Vùng", "Số đơn"],
        rows=[["Bắc", 100], ["Nam", 300]],
        trust="certified",
        unit="đơn",
        reading={"metric_name": "Số đơn", "additivity": "full"},
    )
    return ws.datasets


REFUSED_BEFORE = [
    ("result = d1['Tỷ lệ'].sum()", "NON_ADDITIVE"),
    ("result = d1.groupby('Vùng')['Tỷ lệ'].sum()", "NON_ADDITIVE"),
    ("t = d1.sort_values('Vùng')\nresult = t['Tỷ lệ'].cumsum()", "NON_ADDITIVE"),
    ("x = d1['Tỷ lệ']\nresult = x / x.sum()", "NON_ADDITIVE"),
    ("result = d1.groupby('Vùng').agg('sum')", "NON_ADDITIVE"),
    ("result = sql('SELECT SUM(\"Tỷ lệ\") AS s FROM d1')", "NON_ADDITIVE"),
    ("result = d2['Doanh thu'] + d3['Số đơn']", "UNIT_MISMATCH"),
    ("a = d2['Doanh thu']\nb = d3['Số đơn']\nresult = a - b", "UNIT_MISMATCH"),
]


@pytest.mark.parametrize(("code", "rule"), REFUSED_BEFORE)
def test_what_the_code_would_compute_wrong_is_refused_before_it_runs(code: str, rule: str) -> None:
    found = before_run(code, _ws())
    assert [m.code for m in found] == [rule], found
    assert found[0].detail  # a reason the model can act on


REFUSED_AFTER = [
    (
        [OutputColumn(name="Tổng", op="sum", derived_from=["d2.Doanh thu", "d3.Số đơn"])],
        ["Tổng"],
        [[110]],
        "UNIT_MISMATCH",
    ),
    (
        [OutputColumn(name="Tỷ trọng", op="share", derived_from=["d1.Tỷ lệ"])],
        ["Tỷ trọng"],
        [[0.5]],
        "NON_ADDITIVE",
    ),
    (
        [OutputColumn(name="Tăng trưởng", op="ratio")],
        ["Vùng", "Giá trị"],
        [["Bắc", 1]],
        "CONTRACT_MISMATCH",
    ),
    ([OutputColumn(name="Trung bình", op="mean")], ["Trung bình"], [["cao"]], "CONTRACT_MISMATCH"),
]


@pytest.mark.parametrize(("outputs", "columns", "rows", "rule"), REFUSED_AFTER)
def test_a_result_that_breaks_what_was_declared_is_refused(
    outputs: list[OutputColumn], columns: list[str], rows: list[list[Any]], rule: str
) -> None:
    found = after_run(outputs, columns, rows, _ws())
    assert [m.code for m in found] == [rule], found


@pytest.mark.parametrize(
    "code",
    [
        # A ratio averaged, revenue summed, revenue per order across units (a ratio may be).
        "result = d1['Tỷ lệ'].mean()",
        "result = d2.groupby('Vùng')['Doanh thu'].sum()",
        "m = d2.merge(d3, on='Vùng')\nresult = m['Doanh thu'] / m['Số đơn']",
        "result = sql('SELECT AVG(\"Tỷ lệ\") AS tb FROM d1')",
        "result = sql('SELECT SUM(\"Doanh thu\") AS s FROM d2')",
        # A dataset the model computed has no declared additivity: not refused for it.
        "result = d9['x'].sum()",
    ],
)
def test_what_is_right_goes_through(code: str) -> None:
    assert before_run(code, _ws()) == []


def test_a_declared_result_that_holds_goes_through() -> None:
    outputs = [
        OutputColumn(name="Doanh thu/đơn", op="ratio", derived_from=["d2.Doanh thu", "d3.Số đơn"]),
        OutputColumn(name="Tổng", op="sum", unit="tỷ đồng", derived_from=["d2.Doanh thu"]),
    ]
    assert after_run(outputs, ["Doanh thu/đơn", "Tổng"], [[0.1, 30]], _ws()) == []


def _ctx() -> Any:
    import datetime
    import types
    import uuid

    from services.worker_agent.agentic.tools.base import ToolContext

    ws = Workspace()
    for ds in _ws().values():
        ws.add(
            title=ds.title,
            columns=ds.columns,
            rows=ds.rows,
            trust=ds.trust,
            unit=ds.unit,
            reading=ds.reading,
        )
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


async def test_the_analysis_tool_refuses_before_running_and_checks_what_it_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.worker_agent.agentic.tools.analyze import Analyze, AnalyzeArgs

    monkeypatch.setenv("AGENT_PYTHON_SANDBOX", "local")
    ctx = _ctx()
    refused = await Analyze().run(
        ctx, AnalyzeArgs(code="result = d1['Tỷ lệ'].sum()", inputs=["d1"])
    )
    assert not refused.ok and refused.failure == "arguments"
    assert refused.observation.startswith("CONTRACT_MISMATCH: NON_ADDITIVE")
    declared = await Analyze().run(
        ctx,
        AnalyzeArgs(
            code="result = d2[['Vùng']]",
            inputs=["d2"],
            outputs=[OutputColumn(name="Doanh thu", op="sum", derived_from=["d2.Doanh thu"])],
        ),
    )
    assert not declared.ok and "CONTRACT_MISMATCH" in declared.observation
    fine = await Analyze().run(
        ctx,
        AnalyzeArgs(
            code="result = d2.groupby('Vùng', as_index=False)['Doanh thu'].sum()",
            inputs=["d2"],
            outputs=[OutputColumn(name="Doanh thu", op="sum", derived_from=["d2.Doanh thu"])],
        ),
    )
    assert fine.ok, fine.observation
