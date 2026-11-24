"""An investigation opened from an alert runs the plan it was opened with (AL-11, ANALYTICS_SPEC
F-20 B.4): the first question of a conversation put in that workspace goes to the agent with the
plan built from the alert, not from the workspace's params, and nothing else does."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import qa
from services.worker_agent import router
from services.worker_agent.agentic import investigation
from services.worker_agent.events import Event

from .conftest import Seed
from .test_answers import Bank, build, warehouse  # noqa: F401  (fixture)

pytestmark = [pytest.mark.integration, pytest.mark.req("AL-11")]


def opened(bank: Bank, *, created_from: str = "alert", space: uuid.UUID | None = None) -> uuid.UUID:
    """A workspace opened from an alert of NPL_RATIO split by region, period 2026-09; its params
    hold a plan an editor rewrote, which must not run."""
    monitor = bank.seed.row(
        "monitor.monitor",
        org_id=bank.org,
        data_space_id=bank.space,
        owner_id=bank.user,
        metric_id=bank.npl,
        scope_filter=json.dumps({"split": "vung"}),
        rule_kind="threshold",
        rule=json.dumps({"direction": "above", "value": 5}),
        schedule_cron="0 7 * * *",
    )
    alert = bank.seed.row(
        "monitor.alert",
        org_id=bank.org,
        monitor_id=monitor,
        period_label="2026-09",
        summary="Tỷ lệ nợ xấu kỳ 2026-09",
        dedup_key="2026-09",
    )
    tampered = {"plan": {"goal": "x", "steps": [{"id": "s1", "tool": "explore_sql"}]}}
    return bank.seed.row(
        "workspace.workspace",
        org_id=bank.org,
        data_space_id=space or bank.space,
        owner_id=bank.user,
        title="Điều tra",
        params=json.dumps({"alert_id": str(alert), "investigation": tampered}),
        created_from=created_from,
        created_from_ref=alert,
    )


def ask_in(bank: Bank, workspace: uuid.UUID | None, *, seq: int = 1) -> uuid.UUID:
    conversation = bank.seed.row(
        "qa.conversation",
        org_id=bank.org,
        data_space_id=bank.space,
        user_id=bank.user,
        workspace_id=workspace,
    )
    return bank.ask(
        "Vì sao Tỷ lệ nợ xấu kỳ 2026-09 thay đổi so với kỳ trước?",
        conversation=conversation,
        seq=seq,
    )


async def prepared(bank: Bank, question_id: uuid.UUID) -> Any:
    async with scoped_session("svc_qa", bank.org, bank.user) as session:
        question = await session.get(qa.Question, question_id)
        assert question is not None
        conversation = await session.get(qa.Conversation, question.conversation_id)
        assert conversation is not None
        return await investigation.prepared(bank.org, conversation, question)


def event(bank: Bank, question_id: uuid.UUID) -> Event:
    return Event(
        event_id=uuid.uuid4(),
        org_id=bank.org,
        event_type="question.received",
        aggregate_type="question",
        aggregate_id=question_id,
        payload={"question_id": str(question_id), "user_id": str(bank.user)},
    )


async def test_the_investigations_first_question_runs_the_plan_built_from_its_alert(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    monkeypatch.setenv("ASK_ENGINE", "router")
    question = ask_in(bank, opened(bank))
    plan = await prepared(bank, question)
    assert plan is not None and plan.mode == "analyze"
    first, second = plan.steps
    assert (first.tool, second.tool, second.depends_on) == (
        "query_metric",
        "explain_change",
        ["s1"],
    )
    assert first.args == {
        "metric": "NPL_RATIO",
        "period": "2026-09",
        "compare_to": "previous_period",
        "group_by": ["vung"],
    }
    assert "Tỷ lệ nợ xấu" in first.instruction
    # The agent gets it, whatever the rules would make of the words.
    route = await router.decide(event(bank, question), question)
    assert route == router.Route("agentic", "INVESTIGATION")


async def test_any_other_question_is_planned_as_before(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    monkeypatch.setenv("ASK_ENGINE", "router")
    workspace = opened(bank)
    other_space = seed.row(
        "org.data_space", org_id=bank.org, code="DS-X", name="Khác", steward_id=bank.user
    )
    later = ask_in(bank, workspace, seq=2)
    for question in (
        later,
        ask_in(bank, None),
        ask_in(bank, opened(bank, created_from="blank")),
        ask_in(bank, opened(bank, space=other_space)),
    ):
        assert await prepared(bank, question) is None
    assert (await router.decide(event(bank, later), later)).reason != "INVESTIGATION"
    # The rollback policy keeps every question on the governed path, investigations too.
    monkeypatch.setenv("ASK_ENGINE", "governed")
    first = ask_in(bank, workspace)
    assert (await router.decide(event(bank, first), first)).path == "governed"
