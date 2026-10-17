"""worker-agent executes an agent run from its outbox event (ADR-37 stand-in until Temporal): the
config version the run names, the goal planned against the Data Space's semantic layer, the query
in place, each step recorded, the run's story told, and the api's pause and cancel honoured."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from services.worker_agent import runs
from services.worker_agent.events import OutboxTableSource
from services.worker_agent.main import run_once

from .conftest import Seed
from .test_answers import (
    Bank,
    build,
    warehouse,  # noqa: F401  (the fixture `credit` reads)
)

pytestmark = [pytest.mark.integration]


@pytest.fixture
def credit(warehouse: dict[str, Any]) -> dict[str, Any]:  # noqa: F811
    """The credit warehouse of test_answers: a run's goal is the question asked there."""
    return warehouse


def agent_run(
    bank: Bank,
    *,
    goal: str = "Tỷ lệ nợ xấu theo vùng tháng 9/2026",
    tools: tuple[tuple[str, str], ...] = (("query.semantic", "read"),),
    limits: dict[str, Any] | None = None,
    status: str = "queued",
) -> uuid.UUID:
    seed = bank.seed
    agent_id = seed.row(
        "agent.agent_def",
        org_id=bank.org,
        data_space_id=bank.space,
        code=f"a-{uuid.uuid4().hex[:6]}",
        name="Theo dõi nợ xấu",
        kind="analysis",
        owner_user_id=bank.user,
        account_user_id=bank.user,
        current_config_version=1,
    )
    config = seed.row(
        "agent.agent_config",
        org_id=bank.org,
        agent_id=agent_id,
        version=1,
        goal=goal,
        autonomy="suggest_only",
        limits=json.dumps(limits or {}),
    )
    for tool, level in tools:
        seed.row(
            "agent.agent_tool",
            org_id=bank.org,
            agent_config_id=config,
            tool=tool,
            permission_level=level,
            requires_checkpoint=level == "send_external",
        )
    run = seed.row(
        "agent.agent_run",
        org_id=bank.org,
        agent_id=agent_id,
        config_version=1,
        data_space_id=bank.space,
        trigger_kind="test",
        initiated_by=bank.user,
        is_test=True,
        status=status,
        **(
            {"stop_reason": "user_cancelled", "finished_at": "2026-10-17T00:00:00Z"}
            if status == "cancelled"
            else {}
        ),
    )
    created(bank, run, agent_id, status=status)
    return run


def created(
    bank: Bank,
    run: uuid.UUID,
    agent_id: uuid.UUID | None = None,
    *,
    status: str = "queued",
    previous: str | None = None,
) -> None:
    """The event the api writes with the run (routes.create_test_run, rules.resume_status)."""
    bank.seed.row(
        "agent.outbox",
        org_id=bank.org,
        aggregate_type="agent_run",
        aggregate_id=run,
        event_type="run.status_changed",
        payload=json.dumps(
            {
                "run_id": str(run),
                "agent_id": str(agent_id),
                "is_test": True,
                "previous_status": previous,
                "status": status,
            }
        ),
    )


async def drain() -> None:
    source = OutboxTableSource(schema="agent", event_types=frozenset(runs.HANDLERS))
    while await run_once(source, runs.HANDLERS, batch=50):
        pass


def run_row(seed: Seed, run: uuid.UUID) -> tuple[Any, ...]:
    return seed.one(  # type: ignore[return-value]
        "SELECT status, stop_reason, current_step, narrative, error, started_at IS NOT NULL,"
        " finished_at IS NOT NULL, stop_detail FROM agent.agent_run WHERE id = %s",
        run,
    )


def steps(seed: Seed, run: uuid.UUID) -> list[tuple[Any, ...]]:
    with seed.conn.cursor() as cur:
        cur.execute(
            "SELECT seq, kind, name, status, summary, input, output, call_fingerprint IS NOT NULL"
            " FROM agent.agent_step WHERE run_id = %s ORDER BY seq",
            (str(run),),
        )
        return list(cur.fetchall())


@pytest.mark.req("AC-13-04-01")
async def test_a_test_run_plans_its_goal_queries_in_place_and_tells_the_story(
    seed: Seed,
    credit: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, credit, monkeypatch)
    run = agent_run(bank)
    await drain()

    status, stop, current, narrative, error, started, finished, _ = run_row(seed, run)
    assert (status, stop, error, started, finished) == ("succeeded", "completed", None, True, True)
    recorded = steps(seed, run)
    assert [(s[0], s[1], s[2], s[3]) for s in recorded] == [
        (1, "plan", "plan", "ok"),
        (2, "query", "query.semantic", "ok"),
    ]
    query = recorded[1]
    assert "SELECT" in query[5]["sql"] and query[7] is True
    # Miền Bắc 10%, Miền Nam 10%: the same numbers the Ask screen gives for this question.
    assert "10,00%" in query[4] and query[6]["row_count"] == 2
    assert current == 2
    assert [e["state"] for e in narrative] == ["done", "done", "done"]
    assert "10,00%" in narrative[-1]["text"]
    # The query is the run's, and its plan is a qa.plan of the run (not of a question).
    assert (
        seed.one(
            "SELECT count(*) FROM query.query_run WHERE caller_kind = 'agent_run' AND caller_id = %s",
            run,
        )
        == 1
    )
    assert seed.one("SELECT count(*) FROM qa.plan WHERE agent_run_id = %s", run) == 1
    # Each transition is told to the rest of the system, ending in succeeded.
    assert (
        seed.one(
            "SELECT payload->>'status' FROM agent.outbox WHERE aggregate_id = %s"
            " ORDER BY created_at DESC, id DESC LIMIT 1",
            run,
        )
        == "succeeded"
    )


async def test_the_same_event_twice_runs_once(
    seed: Seed,
    credit: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, credit, monkeypatch)
    run = agent_run(bank)
    created(bank, run)
    await drain()
    assert run_row(seed, run)[0] == "succeeded" and len(steps(seed, run)) == 2


@pytest.mark.req("AC-13-06-03")
async def test_a_run_cancelled_before_it_started_does_nothing(
    seed: Seed,
    credit: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, credit, monkeypatch)
    run = agent_run(bank, status="cancelled")
    created(bank, run, status="queued")  # an event that raced the cancel
    await drain()
    assert run_row(seed, run)[:2] == ("cancelled", "user_cancelled") and steps(seed, run) == []


@pytest.mark.req("AC-13-06-03")
async def test_a_pause_between_steps_holds_the_run_and_a_resume_finishes_it(
    seed: Seed,
    credit: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, credit, monkeypatch)
    run = agent_run(bank)
    real = runs._plan_step

    async def plan_then_pause(*args: Any, **kwargs: Any) -> Any:
        planned = await real(*args, **kwargs)
        # The person pauses from the run screen while the plan is being written.
        with seed.conn.cursor() as cur:
            cur.execute("UPDATE agent.agent_run SET status = 'paused' WHERE id = %s", (str(run),))
        return planned

    monkeypatch.setattr(runs, "_plan_step", plan_then_pause)
    await drain()
    assert run_row(seed, run)[0] == "paused"
    assert [s[1] for s in steps(seed, run)] == ["plan"]

    monkeypatch.setattr(runs, "_plan_step", real)
    with seed.conn.cursor() as cur:
        cur.execute("UPDATE agent.agent_run SET status = 'running' WHERE id = %s", (str(run),))
    created(bank, run, status="running", previous="paused")
    await drain()
    assert run_row(seed, run)[:2] == ("succeeded", "completed")
    # The plan is not made twice: the run goes on from where it stopped.
    assert [s[1] for s in steps(seed, run)] == ["plan", "query"]


@pytest.mark.req("AC-13-02-10")
async def test_a_run_over_its_step_budget_stops_with_the_reason(
    seed: Seed,
    credit: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, credit, monkeypatch)
    run = agent_run(bank, limits={"max_steps": 1})
    await drain()
    status, stop, *_, detail = run_row(seed, run)
    assert (status, stop) == ("failed", "budget_steps")
    assert detail == {"used": 1, "limit": 1, "budget": "max_steps"}
    assert [s[1] for s in steps(seed, run)] == ["plan"]


async def test_a_goal_that_names_no_metric_fails_and_says_why(
    seed: Seed,
    credit: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, credit, monkeypatch)
    run = agent_run(bank, goal="Tìm nguyên nhân biến động lớn và giải thích")
    await drain()
    status, stop, _, narrative, error, *_ = run_row(seed, run)
    assert (status, stop) == ("failed", "error")
    assert "chỉ số" in error and narrative[-1]["text"] == error


async def test_a_run_without_the_query_tool_is_refused_before_it_queries(
    seed: Seed,
    credit: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, credit, monkeypatch)
    run = agent_run(bank, tools=(("doc.search", "read"),))
    await drain()
    status, stop, *_ = run_row(seed, run)
    assert (status, stop) == ("failed", "error")
    assert [(s[1], s[3]) for s in steps(seed, run)] == [("plan", "ok"), ("query", "skipped")]
    assert seed.one("SELECT count(*) FROM query.query_run WHERE caller_id = %s", run) == 0


async def test_a_goal_cut_by_a_dimension_the_space_lacks_names_it_in_the_goals_words(
    seed: Seed,
    credit: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, credit, monkeypatch)
    run = agent_run(bank, goal="Tỷ lệ nợ xấu theo khu vực tháng 9/2026")
    await drain()
    status, stop, _, _, error, *_ = run_row(seed, run)
    assert (status, stop) == ("failed", "error")
    assert "“khu vực”" in error and "khu vuc" not in error
