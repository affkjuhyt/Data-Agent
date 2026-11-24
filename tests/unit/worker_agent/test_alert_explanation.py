"""F-20: the model explains an alert the rules raised, with the alert's own figures only
(invariant 11), and an investigation opened from an alert runs its prepared plan, not the
planner's (AL-11)."""

from __future__ import annotations

import asyncio
import types
import uuid
from typing import Any

import pytest

from libs.contracts.v1.investigation import investigation
from services.worker_agent import monitor_runs
from services.worker_agent.agentic import engine
from services.worker_agent.agentic import plan as p
from services.worker_agent.agentic.workspace import Workspace
from services.worker_agent.llm import ModelUnavailable, Reply

pytestmark = [pytest.mark.unit, pytest.mark.req("AL-10")]


class Writer:
    """A model that writes the given drafts in turn and records what it was asked."""

    def __init__(self, *drafts: str) -> None:
        self.drafts = list(drafts)
        self.asked: list[tuple[str, str]] = []

    async def json_chat(
        self, role: str, system: str, user: str, output: type[Any], **_: Any
    ) -> Reply[Any]:
        self.asked.append((system, user))
        if not self.drafts:
            raise ModelUnavailable("no draft")
        return Reply(output(text=self.drafts.pop(0)), "fake", 10, 1)


def monitor(rule_kind: str, rule: dict[str, Any], scope: dict[str, Any] | None = None) -> Any:
    return types.SimpleNamespace(rule_kind=rule_kind, rule=rule, scope_filter=scope or {})


THRESHOLD = monitor("threshold", {"direction": "above", "value": 5})
FIRED = [
    {"member": None, "value": "10.5", "compared_to": "5", "change": "5.5", "query_run_id": "x"}
]


def explain(model: Writer, row: Any = THRESHOLD, figures: Any = FIRED) -> str | None:
    return asyncio.run(monitor_runs.explain(model, row, "Tỷ lệ nợ xấu", "2026-09", figures))  # type: ignore[arg-type]


def test_the_paragraph_is_written_from_the_alerts_own_figures() -> None:
    model = Writer("Tỷ lệ nợ xấu kỳ 2026-09 là {{F2}}, vượt ngưỡng {{F1}} một khoảng {{F3}}.")
    text = explain(model)
    assert text == "Tỷ lệ nợ xấu kỳ 2026-09 là 10,5, vượt ngưỡng 5 một khoảng +5,5."
    [(system, user)] = model.asked
    assert system == monitor_runs.EXPLAIN_SYSTEM
    # The model is shown the figures as tags with their values, nothing else to write a number from.
    assert '"value": "10,5"' in user and '"value": "5"' in user


def test_a_number_the_alert_does_not_hold_is_not_kept() -> None:
    # Twice a number of its own: no sentence survives the check, no explanation.
    model = Writer("Nợ xấu lên 12,7.", "Nợ xấu lên 12,7.")
    assert explain(model) is None
    # A sentence with one is dropped, the others stay, and the text says one was.
    model = Writer(
        "Nợ xấu kỳ 2026-09 là {{F2}}. Cao nhất 7 năm.", "Nợ xấu là {{F2}}. Cao nhất 7 năm."
    )
    assert explain(model) == "Nợ xấu là 10,5. Đã bỏ 1 câu có số không truy được nguồn."


def test_a_cause_is_not_the_models_to_say() -> None:
    model = Writer("Nợ xấu là {{F2}}. Điều này dẫn đến rủi ro tín dụng tăng.")
    assert explain(model) == "Nợ xấu là 10,5."


def test_a_split_alert_shows_the_count_and_the_rule_not_its_members() -> None:
    split = monitor("threshold", {"direction": "above", "value": 5}, {"split": "vung"})
    figures = [
        {"member": "Bắc", "value": "10", "compared_to": "5", "change": "5"},
        {"member": "Nam", "value": "9", "compared_to": "5", "change": "4"},
    ]
    shown = monitor_runs.explained_figures(split, "Nợ xấu", "2026-09", figures)  # type: ignore[arg-type]
    assert [(f.label, str(f.value), f.unit) for f in shown] == [
        ("ngưỡng của luật", "5", None),
        ("số thành viên vượt luật", "2", "thành viên"),
    ]
    model = Writer("{{F2}} vùng vượt ngưỡng {{F1}}.")
    assert explain(model, split, figures) == "2 thành viên vùng vượt ngưỡng 5."
    assert "Bắc" not in model.asked[0][1] and '"10"' not in model.asked[0][1]


def test_a_change_alert_names_the_period_before_and_the_change_in_percent() -> None:
    change = monitor("change_vs_previous", {"direction": "up", "value": 5})
    figures = [{"member": None, "value": "3000", "compared_to": "500", "change": "500"}]
    shown = monitor_runs.explained_figures(change, "Dư nợ", "2026-09", figures)  # type: ignore[arg-type]
    assert [(f.label, f.unit, f.signed) for f in shown] == [
        ("mức của luật", "%", False),
        ("Dư nợ kỳ 2026-09", None, False),
        ("Dư nợ kỳ trước", None, False),
        ("mức thay đổi so với kỳ trước", "%", True),
    ]


def test_no_model_answer_no_explanation() -> None:
    assert explain(Writer()) is None


# ------------------------------------------------------------------- AL-11 prepared plan


def _state(prepared: p.Plan | None) -> engine.State:
    return engine.State(
        event=types.SimpleNamespace(),  # type: ignore[arg-type]
        question_id=uuid.uuid4(),
        model=types.SimpleNamespace(),  # type: ignore[arg-type]
        ctx=types.SimpleNamespace(ws=Workspace(), question="Vì sao Nợ xấu kỳ 2026-09 thay đổi?"),  # type: ignore[arg-type]
        prepared=prepared,
    )


def _prepared() -> p.Plan:
    made = investigation(
        metric_code="npl_ratio", metric_name="Nợ xấu", period="2026-09", split="vung"
    )
    return p.Plan.model_validate(made["plan"])


@pytest.mark.req("AL-11")
def test_an_investigation_runs_its_prepared_plan_without_the_planner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_planner(*_: Any) -> p.Plan:
        raise AssertionError("the planner was asked")

    async def quiet(*_: Any, **__: Any) -> None:
        return None

    monkeypatch.setattr(engine, "_ask_plan", no_planner)
    monkeypatch.setattr(engine, "publish", quiet)
    state = _state(_prepared())
    asyncio.run(engine.plan(state))
    assert state.plan is not None and not state.handed_back
    assert [(s.tool, s.depends_on) for s in state.plan.steps] == [
        ("query_metric", []),
        ("explain_change", ["s1"]),
    ]
    assert state.plan.steps[0].args["group_by"] == ["vung"]


@pytest.mark.req("AL-11")
def test_a_prepared_plan_that_no_longer_fits_goes_to_the_planner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planned = p.Plan(goal="planner's", mode="answer")

    async def planner(*_: Any) -> p.Plan:
        return planned

    async def quiet(*_: Any, **__: Any) -> None:
        return None

    monkeypatch.setattr(engine, "_ask_plan", planner)
    monkeypatch.setattr(engine, "_planner_system", lambda s: "")
    monkeypatch.setattr(engine, "_situation", lambda s: "")
    monkeypatch.setattr(engine, "publish", quiet)
    for name in ("_out_of_scope", "_missing_period", "_same_named", "_unknown_cut"):
        monkeypatch.setattr(engine, name, lambda s: None)
    broken = _prepared()
    broken.steps[0].tool = "drop_table"
    state = _state(broken)
    asyncio.run(engine.plan(state))
    assert state.plan is not None and state.plan.goal == "planner's"
