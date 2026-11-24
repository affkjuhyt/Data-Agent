"""AE-11 (ASK_ENGINE_SPEC §8.2): a conversation's follow-ups inherit what the turn before settled,
on DS-AW's own vocabulary (deploy/docker/semantic/ds-aw.yaml).

Thirty multi-turn cases, each read the way questions.py reads a turn (the comparison taken out,
the rule pass, then `follow_up` against the slots of the turn before), the last turn's reading
compared with what it should be: metric, period, cuts, filters, comparison. The acceptance is
90 % of them right; the misses are printed."""

from __future__ import annotations

import datetime
import pathlib
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
import yaml

from services.worker_agent.understand import slots as sl
from services.worker_agent.understand.comparison import find_comparison
from services.worker_agent.understand.resolver import (
    DimensionEntry,
    MetricEntry,
    Understanding,
    ValueEntry,
    Vocabulary,
    follow_up,
    understand,
)

pytestmark = [pytest.mark.unit]

MODEL = pathlib.Path(__file__).parents[3] / "deploy" / "docker" / "semantic" / "ds-aw.yaml"
TODAY = datetime.date(2014, 12, 31)
# Members of the value index the cases name (the index is built from the warehouse, not the YAML).
VALUES = [
    ValueEntry("nhom_hang", "Bikes", "Bikes"),
    ValueEntry("nhom_hang", "Accessories", "Accessories"),
    ValueEntry("nhom_hang", "Clothing", "Clothing"),
    ValueEntry("nhom_hang", "Components", "Components"),
    ValueEntry("khu_vuc", "North America", "North America"),
    ValueEntry("khu_vuc", "Europe", "Europe"),
    ValueEntry("khu_vuc", "Pacific", "Pacific"),
]


def _vocabulary() -> Vocabulary:
    model = yaml.safe_load(MODEL.read_text(encoding="utf-8"))
    metrics = [
        MetricEntry(uuid.uuid5(uuid.NAMESPACE_URL, m["name"]), m["name"], m["label"], True)
        for m in model["metrics"]
    ]
    dims: dict[str, DimensionEntry] = {}
    for sm in model["semantic_models"]:
        for d in sm.get("dimensions", []):
            dims.setdefault(
                d["name"],
                DimensionEntry(
                    uuid.uuid5(uuid.NAMESPACE_URL, d["name"]),
                    d["name"],
                    d["label"],
                    d.get("type") == "time",
                ),
            )
    by_code = {m.code: m for m in metrics}
    synonyms: dict[str, tuple[Any, ...]] = {}
    from services.worker_agent.understand.text import norm

    for s in model["synonyms"]:
        if "metric" in s and s["metric"] in by_code:
            synonyms[norm(s["phrase"])] = ("metric", str(by_code[s["metric"]].metric_id))
        elif "dimension" in s and s["dimension"] in dims:
            synonyms[norm(s["phrase"])] = ("dimension", str(dims[s["dimension"]].dimension_id))
    return Vocabulary(metrics, list(dims.values()), VALUES, synonyms)


VOCAB = _vocabulary()


def _turn(text: str, slots: sl.ConversationSlots | None) -> tuple[Understanding, str | None]:
    found = find_comparison(text)
    asked = found.rest if found is not None else text
    compare_to = found.compare_to if found is not None else None
    u = understand(asked, VOCAB, today=TODAY)
    if slots is not None and (u.decision == "unknown" or u.period is None):
        u = follow_up(
            u, slots.chips, VOCAB, question=asked, slots=slots, compare_to=compare_to, today=TODAY
        )
    return u, compare_to


def _figures(dimension: str, values: dict[str, float]) -> list[dict[str, Any]]:
    return [
        {"key": f"F{n}", "value": str(v), "dimensions": {dimension: member}}
        for n, (member, v) in enumerate(values.items(), start=1)
    ]


BY_CATEGORY = _figures(
    "nhom_hang", {"Accessories": 700_000, "Bikes": 28_000_000, "Clothing": 1_000_000}
)
BY_REGION = _figures("khu_vuc", {"Europe": 8e6, "North America": 2.1e7, "Pacific": 6e6})
Y2013 = "2013-01-01/2014-01-01"
Y2012 = "2012-01-01/2013-01-01"


@dataclass
class Case:
    name: str
    turns: list[tuple[str, list[dict[str, Any]] | None]]
    metric: str | None
    period: str | None
    group_by: list[str] = field(default_factory=list)
    filters: dict[str, list[str]] = field(default_factory=dict)
    compare_to: str | None = None
    decision: str = "proceed"


CASES = [
    Case("period alone", [("Doanh thu năm 2013", None), ("Còn năm 2012 thì sao?", None)],
         "DOANH_THU", Y2012),
    Case("period alone keeps the cut",
         [("Doanh thu theo nhóm sản phẩm năm 2013", None), ("Còn năm 2012?", None)],
         "DOANH_THU", Y2012, ["nhom_hang"]),
    Case("a new cut", [("Doanh thu Internet năm 2013", None), ("Còn theo khu vực thì sao?", None)],
         "DOANH_THU_INTERNET", Y2013, ["khu_vuc"]),
    Case("a cut replaces the cut",
         [("Doanh thu theo khu vực năm 2013", None), ("Còn theo nhóm sản phẩm thì sao?", None)],
         "DOANH_THU", Y2013, ["nhom_hang"]),
    Case("same period last year",
         [("Doanh thu năm 2013", None), ("So với cùng kỳ năm trước thì sao?", None)],
         "DOANH_THU", Y2013, compare_to="same_period_last_year"),
    Case("previous month",
         [("Doanh thu Internet tháng 3/2013", None), ("So với tháng trước thì sao?", None)],
         "DOANH_THU_INTERNET", "2013-03-01/2013-04-01", compare_to="previous_period"),
    Case("comparison keeps the filter",
         [("Doanh thu Europe năm 2013", None), ("So với cùng kỳ năm trước?", None)],
         "DOANH_THU", Y2013, filters={"khu_vuc": ["Europe"]}, compare_to="same_period_last_year"),
    Case("a ranking", [("Doanh thu năm 2013", None), ("Nhóm sản phẩm nào cao nhất?", None)],
         "DOANH_THU", Y2013, ["nhom_hang"]),
    Case("the subject, another period",
         [("Doanh thu theo nhóm sản phẩm năm 2013", BY_CATEGORY),
          ("Còn nhóm sản phẩm đó năm 2012 thì sao?", None)],
         "DOANH_THU", Y2012, filters={"nhom_hang": ["Bikes"]}),
    Case("the subject, a new cut",
         [("Doanh thu theo nhóm sản phẩm năm 2013", BY_CATEGORY),
          ("Nhóm sản phẩm đó theo khu vực", None)],
         "DOANH_THU", Y2013, ["khu_vuc"], {"nhom_hang": ["Bikes"]}),
    Case("the subject by a synonym",
         [("Doanh thu theo nhóm sản phẩm năm 2013", BY_CATEGORY),
          ("Còn nhóm hàng đó tháng 3/2013?", None)],
         "DOANH_THU", "2013-03-01/2013-04-01", filters={"nhom_hang": ["Bikes"]}),
    Case("the subject of a region",
         [("Doanh thu theo khu vực năm 2013", BY_REGION), ("Còn khu vực đó năm 2012?", None)],
         "DOANH_THU", Y2012, filters={"khu_vuc": ["North America"]}),
    Case("the subject under a new metric",
         [("Doanh thu theo nhóm sản phẩm năm 2013", BY_CATEGORY),
          ("Còn số đơn hàng của nhóm sản phẩm đó?", None)],
         "SO_DON_INTERNET", Y2013, filters={"nhom_hang": ["Bikes"]}),
    Case("a new metric that continues keeps the period",
         [("Doanh thu năm 2013", None), ("Còn số đơn hàng thì sao?", None)],
         "SO_DON_INTERNET", Y2013),
    Case("a new metric of its own period",
         [("Doanh thu năm 2013", None), ("Còn số đơn hàng năm 2012?", None)],
         "SO_DON_INTERNET", Y2012),
    Case("a new metric of a channel",
         [("Lãi gộp Internet năm 2013", None), ("Còn lãi gộp đại lý thì sao?", None)],
         "LAI_GOP_DAI_LY", Y2013),
    Case("a member in place of a member",
         [("Doanh thu Europe năm 2013", None), ("Còn Pacific thì sao?", None)],
         "DOANH_THU", Y2013, filters={"khu_vuc": ["Pacific"]}),
    Case("a filter kept under a new cut",
         [("Doanh thu Europe năm 2013", None), ("Còn theo nhóm sản phẩm?", None)],
         "DOANH_THU", Y2013, ["nhom_hang"], {"khu_vuc": ["Europe"]}),
    Case("a filter kept under a new period",
         [("Doanh thu Europe năm 2013", None), ("Còn năm 2012?", None)],
         "DOANH_THU", Y2012, filters={"khu_vuc": ["Europe"]}),
    Case("a drill-down",
         [("Doanh thu theo khu vực năm 2013", None),
          ("Đi sâu Europe theo nhóm sản phẩm", None)],
         "DOANH_THU", Y2013, ["nhom_hang"], {"khu_vuc": ["Europe"]}),
    Case("three turns",
         [("Doanh thu năm 2013", None), ("Còn theo nhóm sản phẩm?", None), ("Còn năm 2012?", None)],
         "DOANH_THU", Y2012, ["nhom_hang"]),
    Case("three turns, a subject at the end",
         [("Doanh thu năm 2013", None), ("Theo khu vực thì sao?", BY_REGION),
          ("Còn khu vực đó năm 2012 thì sao?", None)],
         "DOANH_THU", Y2012, filters={"khu_vuc": ["North America"]}),
    Case("a quarter", [("Biên lợi nhuận gộp Internet năm 2013", None),
                       ("Còn quý 1 năm 2014 thì sao?", None)],
         "BIEN_GOP_INTERNET", "2014-01-01/2014-04-01"),
    Case("a month", [("Tồn kho tháng 6/2013", None), ("Còn tháng 7/2013?", None)],
         "TON_KHO", "2013-07-01/2013-08-01"),
    Case("a customer cut",
         [("Số khách hàng mua qua Internet năm 2012", None), ("Theo quốc gia thì sao?", None)],
         "SO_KHACH_HANG_INTERNET", Y2012, ["quoc_gia_khach"]),
    Case("a reseller comparison",
         [("Doanh thu đại lý năm 2013", None), ("So với cùng kỳ năm trước", None)],
         "DOANH_THU_DAI_LY", Y2013, compare_to="same_period_last_year"),
    Case("a new question starts over",
         [("Doanh thu năm 2013", None), ("Số đơn hàng theo khu vực", None)],
         "SO_DON_INTERNET", None, ["khu_vuc"]),
    Case("thanks is no follow-up", [("Doanh thu năm 2013", None), ("cảm ơn nhé", None)],
         None, None, decision="unknown"),
    Case("a quantity no metric holds",
         [("Doanh thu năm 2013", None), ("Số nhân viên theo khu vực", None)],
         None, None, decision="unknown"),
    # "that category" with no category shown before points at nothing: not answered on a guess.
    Case("no subject without a cut before",
         [("Doanh thu năm 2013", None), ("Còn nhóm sản phẩm đó thì sao?", None)],
         None, None, decision="unknown"),
]  # fmt: skip


def _run(case: Case) -> tuple[dict[str, Any], dict[str, Any]]:
    slots: sl.ConversationSlots | None = None
    u: Understanding | None = None
    compare_to = None
    labels = {(v.dimension_code, v.value_code): v.label for v in VALUES}
    for text, figures in case.turns:
        u, compare_to = _turn(text, slots)
        chips = [c.__dict__ for c in u.chips]
        if compare_to:
            chips.append({"kind": "compare", "value": compare_to})
        slots = sl.from_chips(chips, figures=figures, labels=labels)
    assert u is not None
    got = {
        "decision": u.decision,
        "metric": u.metric.code if u.metric else None,
        "period": f"{u.period.start.isoformat()}/{u.period.end.isoformat()}" if u.period else None,
        "group_by": sorted(u.group_by),
        "filters": {k: sorted(v) for k, v in sorted(u.filters.items())},
        "compare_to": compare_to,
    }
    want = {
        "decision": case.decision,
        "metric": case.metric,
        "period": case.period,
        "group_by": sorted(case.group_by),
        "filters": {k: sorted(v) for k, v in sorted(case.filters.items())},
        "compare_to": case.compare_to,
    }
    if case.decision != "proceed":
        got = {
            "decision": got["decision"],
            "inherited": any(c.source == "inherited" for c in u.chips),
        }
        want = {"decision": case.decision, "inherited": False}
    return got, want


def test_there_are_thirty_cases() -> None:
    assert len(CASES) == 30 and len({c.name for c in CASES}) == 30


@pytest.mark.req("AC-01-04-01")
def test_ninety_percent_of_the_multi_turn_cases_inherit_right() -> None:
    misses = []
    for case in CASES:
        got, want = _run(case)
        if got != want:
            misses.append(f"{case.name}: got {got}, want {want}")
    rate = 1 - len(misses) / len(CASES)
    print(f"multi-turn inheritance: {len(CASES) - len(misses)}/{len(CASES)}")
    print("\n".join(misses))
    assert rate >= 0.9, "\n".join(misses)


def test_the_subject_is_the_top_member_of_the_cut() -> None:
    s = sl.from_chips(
        [{"kind": "metric", "value": "DOANH_THU", "label": "Doanh thu"},
         {"kind": "dimension", "value": "nhom_hang", "label": "Nhóm sản phẩm"}],
        figures=BY_CATEGORY,
    )  # fmt: skip
    assert s.subject == sl.Subject("nhom_hang", "Bikes", "Bikes", "Nhóm sản phẩm")
    assert s.last_figures == ["F1", "F2", "F3"] and s.source == "metrics"
    # Figures cut by two dimensions name no single top member.
    mixed = [{"key": "F1", "value": "9", "dimensions": {"nhom_hang": "Bikes", "khu_vuc": "X"}}]
    assert sl.from_chips(s.chips, figures=mixed).subject is None


def test_a_governed_turn_reads_as_the_agents_reading() -> None:
    s = sl.from_chips(
        [{"kind": "metric", "value": "DOANH_THU", "label": "Doanh thu"},
         {"kind": "filter", "value": "khu_vuc=Europe", "label": "Europe"},
         {"kind": "period", "value": Y2013, "label": "năm 2013"}]
    )  # fmt: skip
    assert sl.as_reading(s) == {
        "metric": "DOANH_THU",
        "metric_name": "Doanh thu",
        "group_by": [],
        "filters": {"khu_vuc": ["Europe"]},
        "period": Y2013,
    }
    assert sl.from_reading(sl.as_reading(s) or {}).metric == "DOANH_THU"
    assert "subject" not in sl.render(s) and "Doanh thu (DOANH_THU)" in sl.render(s)


def test_do_meaning_because_is_not_a_subject() -> None:
    s = sl.from_chips(
        [{"kind": "metric", "value": "DOANH_THU", "label": "Doanh thu"},
         {"kind": "dimension", "value": "nhom_hang", "label": "Nhóm sản phẩm"}],
        figures=BY_CATEGORY,
    )  # fmt: skip
    assert not sl.names_subject("Doanh thu giảm do đâu?", s)
    assert sl.names_subject("Còn nhóm sản phẩm đó?", s)
    assert sl.names_subject("what about that nhóm hàng", s, "nhóm hàng")
