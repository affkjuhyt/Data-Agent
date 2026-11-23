"""AL-03: the chart validator (answer/chart_check.py): ten codes, a blocked chart drawn as its
table with a callout, and a proposal only where a declaration is the cause."""

from __future__ import annotations

from typing import Any

import pytest

from services.worker_agent.agentic import blocks as b
from services.worker_agent.agentic.workspace import Workspace
from services.worker_agent.answer import chart_check as cc
from services.worker_agent.answer.narrate import Figure, chart_spec_of

pytestmark = [pytest.mark.unit, pytest.mark.req("AL-03")]

REGIONS = ["Bắc", "Trung", "Nam"]
MONTHS = ["2013-01", "2013-02", "2013-03"]


def _chart(
    values: list[list[float | None]] | None = None,
    *,
    chart_type: str = "bar",
    x_values: list[Any] | None = None,
    x_kind: str = "category",
    labels: list[str] | None = None,
    additivity: str | None = "full",
    units: list[str | None] | None = None,
    **kw: Any,
) -> cc.Chart:
    values = values or [[10.0, 20.0, 30.0]]
    labels = labels or [f"Doanh thu {i}" for i in range(len(values))]
    units = units or [None] * len(values)
    return cc.Chart(
        chart_type=chart_type,
        x_key="x",
        x_label=kw.pop("x_label", "Vùng"),
        x_kind=x_kind,
        x_values=x_values or REGIONS,
        series=[
            cc.Series(
                key=label,
                label=label,
                values=v,
                additivity=additivity,
                unit=u,
                measure=kw.get("measure"),
            )
            for label, v, u in zip(labels, values, units, strict=True)
        ],
        **{k: v for k, v in kw.items() if k != "measure"},
    )


def _codes(chart: cc.Chart, **kw: Any) -> dict[str, str]:
    return {i.code: i.severity for i in cc.check(chart, **kw)}


def test_a_clean_chart_has_no_issue() -> None:
    assert cc.check(_chart()) == []
    assert (
        cc.check(_chart(chart_type="line", x_values=MONTHS, x_kind="time"), time_order=b.time_order)
        == []
    )


@pytest.mark.parametrize("values", [[[5.0, 5.0, 5.0]], [[0.0, None, 0.0]]])
def test_constant_measure_blocks(values: list[list[float | None]]) -> None:
    assert _codes(_chart(values))["CONSTANT_MEASURE"] == "block"


def test_two_equal_members_are_a_comparison_not_a_constant() -> None:
    two = _chart([[10.0, 10.0]], x_values=["Miền Bắc", "Miền Nam"])
    assert "CONSTANT_MEASURE" not in _codes(two)


@pytest.mark.parametrize(
    ("labels", "values"),
    [
        (["Doanh thu", "doanh_thu"], [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]),
        (["Doanh thu", "Doanh số"], [[1.0, 2.0, 3.0], [1.0, 2.0, 3.0]]),
    ],
)
def test_duplicate_columns_block(labels: list[str], values: list[list[float | None]]) -> None:
    assert _codes(_chart(values, labels=labels))["DUPLICATE_COLUMNS"] == "block"


@pytest.mark.parametrize(
    ("x_values", "asked"), [(MONTHS, "quarter"), (["2012", "2013", "2014"], "tháng")]
)
def test_grain_mismatch_blocks(x_values: list[str], asked: str) -> None:
    chart = _chart(x_values=x_values, x_kind="time", chart_type="line", requested_grain=asked)
    assert _codes(chart)["GRAIN_MISMATCH"] == "block"


def test_the_asked_grain_passes() -> None:
    chart = _chart(x_values=MONTHS, x_kind="time", chart_type="line", requested_grain="month")
    assert "GRAIN_MISMATCH" not in _codes(chart)


@pytest.mark.parametrize("kw", [{"chart_type": "pie"}, {"stack": "percent"}])
def test_a_share_of_a_measure_that_does_not_add_up_blocks(kw: dict[str, Any]) -> None:
    measure = {"metric_id": "m1", "measure_code": "customers", "declared": "full"}
    issues = cc.check(_chart(additivity="none", measure=measure, **kw))
    [share] = [i for i in issues if i.code == "NON_ADDITIVE_SHARE"]
    assert share.severity == "block"
    # Declared additive, not additive by its SQL: the declaration is filed for the steward.
    assert share.proposal == {
        "kind": "measure_additivity",
        "metric_id": "m1",
        "measure_code": "customers",
        "from": "full",
        "to": "none",
    }


def test_a_measure_already_declared_not_additive_files_nothing() -> None:
    measure = {"metric_id": "m1", "measure_code": "rate", "declared": "none"}
    [share] = cc.check(_chart(chart_type="pie", additivity="none", measure=measure))
    assert share.code == "NON_ADDITIVE_SHARE" and share.proposal is None
    # An additive measure is drawn as a share.
    assert cc.check(_chart(chart_type="pie", additivity="full")) == []


@pytest.mark.parametrize(
    "chart",
    [
        _chart(
            [[1.0, 2.0, 3.0], [2.0, 3.0, 4.0]], stack="normal", additivity="none", chart_type="bar"
        ),
        _chart(
            [[1.0, 2.0, 3.0]],
            stack="normal",
            additivity="semi",
            chart_type="area",
            x_values=MONTHS,
            x_kind="time",
        ),
    ],
)
def test_stacking_what_does_not_add_up_blocks(chart: cc.Chart) -> None:
    assert _codes(chart)["NON_ADDITIVE_SUM"] == "block"


@pytest.mark.parametrize("kw", [{"axis_labels_visible": False}, {"x_label": " "}])
def test_a_hidden_axis_warns(kw: dict[str, Any]) -> None:
    assert _codes(_chart(**kw)) == {"HIDDEN_AXIS": "warn"}


@pytest.mark.parametrize("values", [[[10.0, -2.0, 5.0]], [[-1.0, -2.0, -3.0]]])
def test_a_pie_of_negative_values_blocks(values: list[list[float | None]]) -> None:
    assert _codes(_chart(values, chart_type="pie"))["PIE_NEGATIVE"] == "block"


def test_too_many_categories_warns_on_a_pie_and_blocks_a_bar() -> None:
    nine = [f"N{i}" for i in range(9)]
    pie = _chart([[float(i + 1) for i in range(9)]], chart_type="pie", x_values=nine)
    assert _codes(pie)["TOO_MANY_CATEGORIES"] == "warn"
    many = [f"N{i}" for i in range(41)]
    bar = _chart([[float(i + 1) for i in range(41)]], x_values=many)
    assert _codes(bar)["TOO_MANY_CATEGORIES"] == "block"
    # A long time series is a line, not a crowd of categories.
    days = [f"2013-01-{i:02d}" for i in range(1, 32)] + [f"2013-02-{i:02d}" for i in range(1, 28)]
    line = _chart(
        [[float(i + 1) for i in range(len(days))]], chart_type="line", x_values=days, x_kind="time"
    )
    assert "TOO_MANY_CATEGORIES" not in _codes(line)


@pytest.mark.parametrize(
    ("labels", "units"),
    [(["value"], [None]), (["Doanh thu", "Biên lợi nhuận"], ["tỷ đồng", "%"])],
)
def test_an_ambiguous_label_warns(labels: list[str], units: list[str | None]) -> None:
    values = [[1.0, 2.0, 3.0], [4.0, 5.0, 7.0]][: len(labels)]
    assert _codes(_chart(values, labels=labels, units=units))["AMBIGUOUS_LABEL"] == "warn"


@pytest.mark.parametrize(
    "x_values", [["2013-03", "2013-01", "2013-02"], ["2013-01", "khác", "2013-03"]]
)
def test_a_time_axis_out_of_order_blocks(x_values: list[str]) -> None:
    chart = _chart(x_values=x_values, x_kind="time", chart_type="line")
    assert _codes(chart, time_order=b.time_order)["NO_TIME_ORDER"] == "block"


def test_every_code_is_covered() -> None:
    assert set(cc.CODES) == {
        "CONSTANT_MEASURE",
        "DUPLICATE_COLUMNS",
        "GRAIN_MISMATCH",
        "NON_ADDITIVE_SHARE",
        "NON_ADDITIVE_SUM",
        "HIDDEN_AXIS",
        "PIE_NEGATIVE",
        "TOO_MANY_CATEGORIES",
        "AMBIGUOUS_LABEL",
        "NO_TIME_ORDER",
    }


def _ws(rows: list[list[Any]], **reading: Any) -> Workspace:
    ws = Workspace()
    ws.add(
        title="Khách hàng theo vùng",
        columns=["Vùng", "Khách hàng"],
        rows=rows,
        trust="certified",
        reading={"metric_id": "m1", **reading},
    )
    return ws


def test_a_blocked_chart_is_drawn_as_its_table_with_the_reason() -> None:
    ws = _ws(
        [["Bắc", 10], ["Trung", 20], ["Nam", 30]],
        additivity="none",
        measure_code="customers",
        declared="full",
    )
    ds = next(iter(ws.datasets))
    block = b.materialize(
        b.BlockDraft(type="chart", dataset=ds, chart_type="pie", x="Vùng", y=["Khách hàng"]),
        ws,
        "b1",
    )
    assert block["type"] == "table" and block["row_count"] == 3
    blocked = block["chart_blocked"]
    assert blocked["reason"].startswith("Không vẽ biểu đồ:")
    assert blocked["issues"][0]["code"] == "NON_ADDITIVE_SHARE"
    assert blocked["issues"][0]["proposal"]["measure_code"] == "customers"


def test_an_additive_chart_is_drawn_and_off_turns_the_check_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _ws([["Bắc", 10], ["Trung", 20], ["Nam", 30]], additivity="full")
    ds = next(iter(ws.datasets))
    draft = b.BlockDraft(type="chart", dataset=ds, chart_type="pie", x="Vùng", y=["Khách hàng"])
    assert b.materialize(draft, ws, "b1")["type"] == "chart"
    blocked = _ws([["Bắc", 10], ["Trung", 20], ["Nam", 30]], additivity="none")
    bds = next(iter(blocked.datasets))
    monkeypatch.setenv("CHART_CHECK", "off")
    off = b.BlockDraft(type="chart", dataset=bds, chart_type="pie", x="Vùng", y=["Khách hàng"])
    assert b.materialize(off, blocked, "b1")["type"] == "chart"


def _figure(i: int, region: str, value: float) -> Figure:
    import decimal

    return Figure(
        key=f"F{i}",
        label=region,
        value=decimal.Decimal(str(value)),
        unit=None,
        decimals=0,
        row=i,
        column="value",
        dimensions={"region": region},
    )


def test_the_governed_chart_is_not_drawn_when_blocked_and_says_why() -> None:
    figures = [_figure(i, r, 7.0) for i, r in enumerate(REGIONS)]
    found: list[dict[str, Any]] = []
    spec = chart_spec_of(
        figures,
        group_by=["region"],
        dimension_names={"region": "Vùng"},
        time_dimensions=set(),
        metric_name="Khách hàng",
        unit=None,
        issues=found,
    )
    assert spec is None
    assert [i["code"] for i in found] == ["CONSTANT_MEASURE"]
    assert cc.described(found).startswith("Không vẽ biểu đồ:")
    moving = [_figure(i, r, float(i + 1)) for i, r in enumerate(REGIONS)]
    assert (
        chart_spec_of(
            moving,
            group_by=["region"],
            dimension_names={"region": "Vùng"},
            time_dimensions=set(),
            metric_name="Khách hàng",
            unit=None,
        )
        is not None
    )
