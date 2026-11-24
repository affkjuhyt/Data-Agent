"""AE-13 (ASK_ENGINE_SPEC §10): a document's figure checked against the data's, deterministically:
tolerance, units and scale, period alignment, members, authority; and what the answer carries."""

from __future__ import annotations

import datetime
import decimal
import types
from typing import Any

import pytest

from libs.contracts.v1 import trust as answer_trust
from libs.contracts.v1.trust import TrustCode
from services.worker_agent.agentic import reconciled
from services.worker_agent.agentic.workspace import Workspace
from services.worker_agent.documents import reconcile as r

pytestmark = [pytest.mark.unit, pytest.mark.req("AE-13")]

TODAY = datetime.date(2026, 11, 24)
Y2024 = r.Period(datetime.date(2024, 1, 1), datetime.date(2025, 1, 1), "2024")


def _data(value: str, **over: Any) -> r.SourceFigure:
    return r.SourceFigure(
        **{
            "figure_id": "d1:0",
            "label": "Dư nợ",
            "value": decimal.Decimal(value),
            "unit": "VND",
            "trust_code": TrustCode.GOVERNED,
            "authority": "certified_metric",
            "period": Y2024,
            "members": ("Hà Nội",),
            **over,
        }
    )


def _doc(value: str, **over: Any) -> r.SourceFigure:
    return r.SourceFigure(
        **{
            "figure_id": "d2",
            "label": "Báo cáo dư nợ: Hà Nội - Năm 2024 > Thực hiện",
            "value": decimal.Decimal(value),
            "unit": "VND",
            "trust_code": TrustCode.DOCUMENT,
            "authority": "document",
            "period": Y2024,
            "context": "Hà Nội Năm 2024 > Thực hiện Bảng 3. Dư nợ theo chi nhánh (triệu đồng)",
            "cite": 1,
            **over,
        }
    )


def test_within_the_tolerance_the_two_agree() -> None:
    rec = r.reconcile([_data("1300000000")], [_doc("1303000000")])
    assert rec is not None and rec.verdict == "agree"
    assert rec.residual_abs == 3_000_000 and rec.residual_pct == pytest.approx(0.2308, abs=1e-4)
    note = rec.note()
    assert note["verdict"] == "agree" and note["residual"]["tolerance_pct"] == 0.5
    assert [s["authority"] for s in note["sources"]] == ["certified_metric", "document"]
    assert note["identities"][0]["holds"] is True


def test_outside_it_a_certified_metric_outranks_the_document() -> None:
    rec = r.reconcile([_data("1300000000")], [_doc("1250000000")])
    assert rec is not None and (rec.verdict, rec.winner) == ("authority", rec.data)
    assert rec.residual_pct == pytest.approx(3.8462, abs=1e-4)
    assert rec.note()["sources"][0]["wins"] is True


def test_sql_the_model_wrote_against_a_document_is_a_conflict() -> None:
    explored = _data("1300000000", authority="none", trust_code=TrustCode.EXPLORATORY)
    rec = r.reconcile([explored], [_doc("1250000000")])
    assert rec is not None and rec.verdict == "conflict" and rec.winner is None
    # The exact tolerance is still an agreement; a custom one is honoured.
    assert r.reconcile([explored], [_doc("1306500000")]).verdict == "agree"  # type: ignore[union-attr]
    assert (
        r.reconcile([explored], [_doc("1250000000")], tolerance_pct=5).verdict  # type: ignore[union-attr]
        == "agree"
    )


def test_currencies_and_a_percentage_against_an_amount_are_not_comparable() -> None:
    usd = r.reconcile([_data("1300000000")], [_doc("1300000000", unit="USD")])
    assert usd is not None and usd.verdict == "not_comparable" and "tỷ giá" in usd.open[0]
    pct = r.reconcile([_data("12.5", unit="%")], [_doc("12.5", unit="percent")])
    assert pct is not None and pct.verdict == "agree"
    mixed = r.reconcile([_data("12.5", unit="%")], [_doc("12.5", unit="VND")])
    assert mixed is not None and mixed.verdict == "not_comparable"


def test_a_cell_without_a_unit_is_read_in_the_datas_and_a_scale_gap_is_not_a_conflict() -> None:
    explored = _data("1300000000", authority="none", trust_code=TrustCode.EXPLORATORY)
    same = r.reconcile([explored], [_doc("1300000000", unit=None)])
    assert same is not None and same.verdict == "agree" and same.assumptions
    # 1.300 (millions not stated) against 1.300.000.000: a scale, not a disagreement.
    scaled = r.reconcile([explored], [_doc("1300", unit=None)])
    assert scaled is not None and scaled.verdict == "not_comparable"
    assert "bậc nghìn" in scaled.open[0]
    # A count word: the table's word is not a unit.
    trips = r.reconcile([_data("411", unit="chuyến", members=())], [_doc("411", unit="lượt")])
    assert trips is not None and trips.verdict == "agree"


def test_periods_must_cover_the_same_span() -> None:
    q1 = r.Period(datetime.date(2024, 1, 1), datetime.date(2024, 4, 1), "quý 1/2024")
    other = r.reconcile([_data("1300000000")], [_doc("1300000000", period=q1)])
    assert other is not None and other.verdict == "not_comparable"
    assert "kỳ khác nhau" in other.open[0]
    none = r.reconcile([_data("1300000000")], [_doc("1300000000", period=None)])
    assert none is not None and none.verdict == "not_comparable"
    assert "không ghi kỳ" in none.open[0]


def test_the_period_of_a_cells_labels() -> None:
    assert r.period_of("Năm 2024 > Thực hiện", TODAY) == r.Period(
        datetime.date(2024, 1, 1),
        datetime.date(2025, 1, 1),
        r.period_of("Năm 2024", TODAY).label,  # type: ignore[union-attr]
    )
    q = r.period_of("2024-Q1", TODAY)
    assert q is not None and (q.start, q.end) == (
        datetime.date(2024, 1, 1),
        datetime.date(2024, 4, 1),
    )
    assert r.period_of("Chi nhánh", TODAY) is None
    fy = r.period_of("FY2024", TODAY)
    assert fy is not None and fy.fiscal and fy.start == datetime.date(2024, 1, 1)
    june = r.period_of("FY2024", TODAY, fiscal_year_end_month=6)
    assert june is not None and (june.start, june.end) == (
        datetime.date(2023, 7, 1),
        datetime.date(2024, 7, 1),
    )
    # A fiscal year read as the calendar year is said; one ending in June is another span.
    fiscal = r.reconcile([_data("1300000000")], [_doc("1300000000", period=fy)])
    assert fiscal is not None and fiscal.verdict == "agree" and fiscal.assumptions
    shifted = r.reconcile(
        [_data("1300000000")], [_doc("1300000000", period=june)], fiscal_year_end_month=6
    )
    assert shifted is not None and shifted.verdict == "not_comparable"


def test_a_member_the_document_does_not_name_is_not_compared() -> None:
    rec = r.reconcile([_data("420000000", members=("Đà Nẵng",))], [_doc("1300000000")])
    assert rec is not None and rec.verdict == "not_comparable"
    assert "Đà Nẵng" in rec.open[0]
    # Of several rows, the one the document names is compared.
    rows = [_data("420000000", figure_id="d1:1", members=("Đà Nẵng",)), _data("1300000000")]
    best = r.reconcile(rows, [_doc("1300000000")])
    assert best is not None and best.verdict == "agree" and best.data.figure_id == "d1:0"


def test_an_amendment_replaces_the_original() -> None:
    explored = _data("1300000000", authority="none", trust_code=TrustCode.EXPLORATORY)
    original = _doc("1250000000", figure_id="d2", authority="original")
    amended = _doc("1300000000", figure_id="d3", authority="amendment")
    rec = r.reconcile([explored], [original, amended])
    assert rec is not None and rec.verdict == "agree" and rec.document.figure_id == "d3"
    assert any("bản sửa đổi" in rule for rule in rec.rules)


def test_nothing_to_reconcile_without_both_sides() -> None:
    assert r.reconcile([], [_doc("1")]) is None
    assert r.reconcile([_data("1")], []) is None


def test_a_conflict_refuses_with_source_conflict_but_keeps_its_figures() -> None:
    figures = [
        {"key": "b1-0", "trust": "SQL thăm dò, chưa qua chỉ số chuẩn", "display_state": "shown"},
        {"key": "b1-1", "trust": "Theo tài liệu", "display_state": "shown"},
    ]
    explored = _data("1300000000", authority="none", trust_code=TrustCode.EXPLORATORY)
    conflict = r.reconcile([explored], [_doc("1250000000")])
    assert conflict is not None
    code, envelope = answer_trust.trust(
        "agentic", "data", figures=figures, reconciliation=conflict.note()
    )
    assert code == TrustCode.INSUFFICIENT and envelope["trust_code"] == "INSUFFICIENT"
    assert envelope["refusal"] == {"code": "SOURCE_CONFLICT", "next_action": "contact_steward"}
    assert envelope["reconciliation"]["verdict"] == "conflict"
    assert set(envelope["figures"].values()) == {"EXPLORATORY", "DOCUMENT"}
    agreed = r.reconcile([explored], [_doc("1300000000")])
    code, envelope = answer_trust.trust(
        "agentic",
        "data",
        figures=figures,
        reconciliation=agreed.note(),  # type: ignore[union-attr]
    )
    assert code == TrustCode.DOCUMENT and "refusal" not in envelope


# ---------------------------------------------------------------- the agent's workspace


def _ctx(ws: Workspace, question: str = "Dư nợ Hà Nội năm 2024 có khớp báo cáo không?") -> Any:
    return types.SimpleNamespace(
        ws=ws,
        question=question,
        today=TODAY,
        as_of=None,
        period=None,
        layer=types.SimpleNamespace(vocabulary=types.SimpleNamespace(values=[])),
    )


def _cell(ws: Workspace, value: int) -> None:
    ws.add(
        title="Bảng 3",
        columns=["hàng", "cột", "giá trị"],
        rows=[["Hà Nội", "Năm 2024 > Thực hiện", decimal.Decimal(value)]],
        trust="document",
        tool="search_documents",
        unit="VND",
        cell={
            "row": "Hà Nội",
            "column": "Năm 2024 > Thực hiện",
            "caption": "Bảng 3. Dư nợ theo chi nhánh (triệu đồng)",
            "document": "Báo cáo dư nợ",
            "cite": 1,
        },
    )


def test_the_workspace_figures_are_read_with_their_period_and_members() -> None:
    ws = Workspace()
    ws.add(
        title="Dư nợ theo chi nhánh, 2024",
        columns=["Chi nhánh", "Dư nợ"],
        rows=[["Đà Nẵng", "420000000"], ["Hà Nội", "1250000000"]],
        trust="certified",
        tool="query_metric",
        unit="VND",
        reading={"metric": "DU_NO", "metric_name": "Dư nợ", "period": "2024", "filters": {}},
    )
    _cell(ws, 1_300_000_000)
    rec = reconciled.check(_ctx(ws))
    assert rec is not None and rec.verdict == "authority"
    assert rec.data.members == ("Hà Nội",) and rec.data.period == r.Period(
        datetime.date(2024, 1, 1),
        datetime.date(2025, 1, 1),
        rec.data.period.label,  # type: ignore[union-attr]
    )
    made = reconciled.blocks(_ctx(ws), rec, 3)
    assert [x["type"] for x in made] == ["kpi", "reconciliation"]
    kpi = made[0]
    assert [i["value"] for i in kpi["items"]] == [1250000000.0, 1300000000.0]
    assert [i["trust_label"] for i in kpi["items"]] == ["Theo chỉ số chuẩn", "Theo tài liệu"]
    assert made[1]["note"]["verdict"] == "authority" and "[1]" in made[1]["content"]
    assert "1.300.000.000 đồng" in made[1]["content"]


def test_sql_the_model_wrote_is_read_for_the_questions_period_and_a_conflict_warns() -> None:
    ws = Workspace()
    ws.add(
        title="Dư nợ Hà Nội 2024",
        columns=["du_no"],
        rows=[[1_250_000_000]],
        trust="exploratory",
        tool="explore_sql",
        unit="VND",
    )
    _cell(ws, 1_300_000_000)
    ctx = _ctx(ws)
    ctx.period = types.SimpleNamespace(
        start=datetime.date(2024, 1, 1), end=datetime.date(2025, 1, 1), label="năm 2024"
    )
    rec = reconciled.check(ctx)
    assert rec is not None and rec.verdict == "conflict"
    made = reconciled.blocks(ctx, rec, 1)
    assert [x["type"] for x in made] == ["callout", "kpi", "reconciliation"]
    assert made[0]["tone"] == "warning" and "người phụ trách" in made[0]["content"]
    assert "conflict" in reconciled.prompt(rec) or "không chọn số" in reconciled.prompt(rec)


def test_a_turn_without_a_cell_reconciles_nothing() -> None:
    ws = Workspace()
    ws.add(title="t", columns=["v"], rows=[[1]], trust="certified", tool="query_metric")
    assert reconciled.check(_ctx(ws)) is None
