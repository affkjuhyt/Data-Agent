"""worker-agent executes an agent run from its outbox event (ADR-37 stand-in until Temporal): the
config version the run names, the goal planned against the Data Space's semantic layer, the query
in place, each step recorded, the run's story told, and the api's pause and cancel honoured."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from services.worker_agent import hitl, resolutions, runs
from services.worker_agent.events import OutboxTableSource
from services.worker_agent.main import run_once

from .conftest import Seed
from .test_answers import (
    Bank,
    build,
    warehouse,  # noqa: F401  (the fixture `credit` reads)
    watch,
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
    autonomy: str = "suggest_only",
    checkpoints: tuple[str, ...] = (),
    is_test: bool = True,
    kind: str = "analysis",
) -> uuid.UUID:
    seed = bank.seed
    agent_id = seed.row(
        "agent.agent_def",
        org_id=bank.org,
        data_space_id=bank.space,
        code=f"a-{uuid.uuid4().hex[:6]}",
        name="Theo dõi nợ xấu",
        kind=kind,
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
        autonomy=autonomy,
        checkpoints=json.dumps(list(checkpoints)),
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
        trigger_kind="test" if is_test else "manual",
        initiated_by=bank.user,
        is_test=is_test,
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
    status, stop, _, narrative, error, _, _, detail = run_row(seed, run)
    assert (status, stop) == ("failed", "error")
    assert "chỉ số" in error and narrative[-1]["text"] == error
    # R0-11: a goal read as no metric is an intent mismatch, routed to restating it.
    assert detail == {
        "error_class": "INTENT_MISMATCH",
        "route": "clarify",
        "code": "UNRESOLVED_ENTITY",
    }


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


async def test_a_goal_without_a_period_reads_the_last_day_and_says_so(
    seed: Seed,
    credit: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A balance read over all history would mix August into Miền Bắc (200 / 2500 = 8%).
    bank = build(seed, credit, monkeypatch)
    run = agent_run(bank, goal="Tỷ lệ nợ xấu theo vùng")
    await drain()
    status, _, _, narrative, *_ = run_row(seed, run)
    assert status == "succeeded"
    answer = narrative[-1]["text"]
    assert "8,00%" not in answer and answer.count("10,00%") == 2
    assert "ngày mới nhất có dữ liệu" in answer


async def decide(bank: Bank, decision: str) -> None:
    """A person decides the run's checkpoint in Cần bạn: the inbox records it and emits
    hitl.resolved (api inbox routes), which the run's resume follows."""
    seed = bank.seed
    request, run, kind = seed.one(  # type: ignore[misc]
        "SELECT id, source_ref, kind FROM inbox.hitl_request WHERE org_id = %s AND status = 'open'",
        bank.org,
    )
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE inbox.hitl_request SET status = %s, resolved_at = now(), resolved_by = %s"
            " WHERE id = %s",
            (decision, str(bank.user), str(request)),
        )
    seed.row(
        "inbox.outbox",
        org_id=bank.org,
        aggregate_type="hitl_request",
        aggregate_id=request,
        event_type="hitl.resolved",
        payload=json.dumps(
            {
                "hitl_request_id": str(request),
                "kind": kind,
                "status": decision,
                "decision": "approve" if decision == "approved" else "reject",
                "source_service": "agent",
                "source_ref": str(run),
                "resolved_by": str(bank.user),
            }
        ),
    )
    source = OutboxTableSource(schema="inbox", event_types=frozenset(resolutions.HANDLERS))
    while await run_once(source, resolutions.HANDLERS, batch=50):
        pass


async def drain_with_inbox() -> None:
    """The run and the HITL request it asks for, both from agent.outbox."""
    handlers = {**runs.HANDLERS, **hitl.HANDLERS}
    source = OutboxTableSource(schema="agent", event_types=frozenset(handlers))
    while await run_once(source, handlers, batch=50):
        pass


def notifications(seed: Seed, org: uuid.UUID) -> list[tuple[Any, ...]]:
    with seed.conn.cursor() as cur:
        cur.execute(
            "SELECT user_id, kind, title, body, link FROM notify.notification WHERE org_id = %s",
            (str(org),),
        )
        return list(cur.fetchall())


NOTIFY = (("query.semantic", "read"), ("notify.internal", "write_derived"))


@pytest.mark.req("AC-13-02-06")
async def test_a_write_waits_at_its_checkpoint_and_runs_once_approved(
    seed: Seed,
    credit: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, credit, monkeypatch)
    run = agent_run(
        bank,
        tools=NOTIFY,
        autonomy="propose_approve",
        checkpoints=("before_write_derived",),
        is_test=False,
    )
    await drain_with_inbox()

    status, _, _, narrative, *_ = run_row(seed, run)
    assert status == "waiting_human"
    assert [(s[1], s[3]) for s in steps(seed, run)] == [
        ("plan", "ok"),
        ("query", "ok"),
        ("checkpoint", "ok"),
    ]
    assert narrative[-1]["state"] == "now" and "duyệt" in narrative[-1]["text"]
    # The request is in Cần bạn, for the agent's owner, pointing at the run.
    assert seed.one(
        "SELECT kind, source_service, source_ref, assignee_id FROM inbox.hitl_request"
        " WHERE org_id = %s",
        bank.org,
    ) == ("checkpoint", "agent", run, bank.user)
    assert notifications(seed, bank.org) == []

    await decide(bank, "approved")
    await drain_with_inbox()

    status, stop, *_ = run_row(seed, run)
    assert (status, stop) == ("succeeded", "completed")
    assert [(s[1], s[2], s[3]) for s in steps(seed, run)][-1] == (
        "tool_call",
        "notify.internal",
        "ok",
    )
    [(user, kind, title, body, link)] = notifications(seed, bank.org)
    assert (user, kind) == (bank.user, "report") and "10,00%" in body
    assert link == f"/agents/run/{run}" and "Theo dõi nợ xấu" in title
    assert seed.one(
        "SELECT outcome, resolved_at IS NOT NULL FROM agent.agent_checkpoint_hit WHERE run_id = %s",
        run,
    ) == ("approved", True)


async def test_a_rejected_write_is_skipped_and_the_run_still_ends(
    seed: Seed,
    credit: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, credit, monkeypatch)
    run = agent_run(
        bank,
        tools=NOTIFY,
        autonomy="propose_approve",
        checkpoints=("before_write_derived",),
        is_test=False,
    )
    await drain_with_inbox()
    await decide(bank, "rejected")
    await drain_with_inbox()
    status, stop, *_ = run_row(seed, run)
    assert (status, stop) == ("succeeded", "completed")
    assert steps(seed, run)[-1][1:4] == ("tool_call", "notify.internal", "skipped")
    assert notifications(seed, bank.org) == []


@pytest.mark.req("AC-13-04-01")
async def test_a_test_run_simulates_the_write_without_asking_or_sending(
    seed: Seed,
    credit: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, credit, monkeypatch)
    run = agent_run(bank, tools=NOTIFY, checkpoints=("before_write_derived",))
    await drain_with_inbox()
    assert run_row(seed, run)[:2] == ("succeeded", "completed")
    assert steps(seed, run)[-1][1:4] == ("tool_call", "notify.internal", "simulated")
    assert notifications(seed, bank.org) == []
    assert seed.one("SELECT count(*) FROM inbox.hitl_request WHERE org_id = %s", bank.org) == 0


async def test_an_agent_that_acts_then_reports_writes_without_waiting(
    seed: Seed,
    credit: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, credit, monkeypatch)
    run = agent_run(bank, tools=NOTIFY, autonomy="act_then_report", is_test=False)
    await drain_with_inbox()
    assert run_row(seed, run)[:2] == ("succeeded", "completed")
    assert len(notifications(seed, bank.org)) == 1


def timers(seed: Seed, run: uuid.UUID) -> list[tuple[Any, ...]]:
    """The run's timers still to fire: kind, and how far ahead (minutes, rounded)."""
    with seed.conn.cursor() as cur:
        cur.execute(
            "SELECT payload->>'timer', round(extract(epoch FROM"
            " ((payload->>'not_before')::timestamptz - now())) / 60)"
            " FROM agent.outbox WHERE aggregate_id = %s AND event_type = 'run.timer'"
            " AND published_at IS NULL ORDER BY (payload->>'not_before')::timestamptz",
            (str(run),),
        )
        return [(kind, int(minutes)) for kind, minutes in cur.fetchall()]


def elapse(seed: Seed, run: uuid.UUID) -> None:
    """Time passes until the run's next timer is due."""
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE agent.outbox SET payload = jsonb_set(payload, '{not_before}',"
            " to_jsonb((now() - interval '1 second')::text)) WHERE id = ("
            " SELECT id FROM agent.outbox WHERE aggregate_id = %s AND event_type = 'run.timer'"
            " AND published_at IS NULL ORDER BY (payload->>'not_before')::timestamptz LIMIT 1)",
            (str(run),),
        )


@pytest.mark.req("AC-13-06-05")
async def test_an_unanswered_checkpoint_is_reminded_then_times_out(
    seed: Seed,
    credit: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, credit, monkeypatch)
    run = agent_run(
        bank,
        tools=NOTIFY,
        autonomy="propose_approve",
        checkpoints=("before_write_derived",),
        is_test=False,
    )
    await drain_with_inbox()
    day = 24 * 60
    # Timers not yet due stay in the outbox: nothing fired on the first drain.
    assert timers(seed, run) == [("reminder", day), ("reminder", 3 * day), ("timeout", 7 * day)]

    elapse(seed, run)
    await drain_with_inbox()
    assert run_row(seed, run)[0] == "waiting_human"
    [reminder] = notifications(seed, bank.org)
    assert reminder[1] == "hitl" and "Nhắc" in reminder[2]

    for _ in range(2):  # the second reminder, then the seventh day
        elapse(seed, run)
        await drain_with_inbox()
    status, stop, _, narrative, error, *_ = run_row(seed, run)
    assert (status, stop) == ("cancelled", "waiting_timeout")
    assert "7 ngày" in error and narrative[-1]["text"] == error
    assert seed.one("SELECT outcome FROM agent.agent_checkpoint_hit WHERE run_id = %s", run) == (
        "expired"
    )
    # Two reminders, then the owner is told the run ended; nothing was sent.
    assert [n[1] for n in notifications(seed, bank.org)].count("hitl") == 2
    assert not [n for n in notifications(seed, bank.org) if n[1] == "report"]


async def test_a_checkpoint_decided_in_time_cancels_nothing(
    seed: Seed,
    credit: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, credit, monkeypatch)
    run = agent_run(
        bank,
        tools=NOTIFY,
        autonomy="propose_approve",
        checkpoints=("before_write_derived",),
        is_test=False,
    )
    await drain_with_inbox()
    await decide(bank, "approved")
    await drain_with_inbox()
    for _ in range(3):
        elapse(seed, run)
        await drain_with_inbox()
    assert run_row(seed, run)[:2] == ("succeeded", "completed")
    assert [n[1] for n in notifications(seed, bank.org)] == ["report"]


def failing(times: int, real: Any) -> Any:
    """run_query that cannot reach the source `times` times, then reaches it."""
    calls = {"n": 0}

    async def run_query(**kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] <= times:
            return runs.QueryOutcome(
                query_run_id=uuid.uuid4(),
                status="failed",
                error="ConnectionRefusedError: connection",
            )
        return await real(**kwargs)

    return run_query


@pytest.mark.req("AC-13-06-04")
async def test_a_passing_failure_is_retried_after_5_then_15_minutes(
    seed: Seed,
    credit: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, credit, monkeypatch)
    monkeypatch.setattr(runs, "run_query", failing(2, runs.run_query))
    run = agent_run(bank)
    await drain()
    assert run_row(seed, run)[0] == "running" and timers(seed, run) == [("retry", 5)]
    elapse(seed, run)
    await drain()
    assert timers(seed, run) == [("retry", 15)]
    elapse(seed, run)
    await drain()
    assert run_row(seed, run)[:2] == ("succeeded", "completed")
    assert [(s[1], s[3]) for s in steps(seed, run)] == [
        ("plan", "ok"),
        ("query", "retried"),
        ("query", "retried"),
        ("query", "ok"),
    ]


@pytest.mark.req("AC-13-06-04")
async def test_after_three_retries_the_run_fails_and_the_owner_is_told(
    seed: Seed,
    credit: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, credit, monkeypatch)
    monkeypatch.setattr(runs, "run_query", failing(9, runs.run_query))
    run = agent_run(bank, is_test=False)
    await drain()
    for _ in range(3):
        elapse(seed, run)
        await drain()
    status, stop, *_, detail = run_row(seed, run)
    assert (status, stop) == ("failed", "error") and timers(seed, run) == []
    # The same class four times in a row: past what its route allows, so the run stops (R0-11).
    assert detail["error_class"] == "SOURCE_UNAVAILABLE" and detail["route"] == "retry"
    queried = [s for s in steps(seed, run) if s[1] == "query"]
    assert {s[6]["error_class"] for s in queried} == {"SOURCE_UNAVAILABLE"}
    assert [s[3] for s in steps(seed, run) if s[1] == "query"] == [
        "retried",
        "retried",
        "retried",
        "error",
    ]
    [(user, kind, title, *_)] = notifications(seed, bank.org)
    assert (user, kind) == (bank.user, "alert") and "không chạy được" in title


async def test_a_wrong_query_is_not_retried(
    seed: Seed,
    credit: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, credit, monkeypatch)

    async def wrong(**kwargs: Any) -> Any:
        return runs.QueryOutcome(
            query_run_id=uuid.uuid4(),
            status="failed",
            error="local query failed (ProgrammingError)",
        )

    monkeypatch.setattr(runs, "run_query", wrong)
    run = agent_run(bank)
    await drain()
    assert run_row(seed, run)[:2] == ("failed", "error") and timers(seed, run) == []
    # A failure that comes back the same way every time is refused at once, not retried.
    assert run_row(seed, run)[7]["error_class"] == "PLAN_LOGIC"


async def test_a_run_judges_its_answer_against_the_metrics_threshold(
    seed: Seed,
    credit: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, credit, monkeypatch)
    watch(bank, {"direction": "above", "value": 12, "early": 8})
    run = agent_run(bank)
    await drain()
    status, _, _, narrative, *_ = run_row(seed, run)
    assert status == "succeeded"
    assert "cảnh báo sớm (từ 8,00%): Miền Bắc (10,00%); Miền Nam (10,00%)" in narrative[-1]["text"]


REPORT = """Báo cáo nợ xấu tháng 9/2026:
- Tỷ lệ nợ xấu theo vùng tháng 9/2026
- Dư nợ theo vùng tháng 9/2026
- Tỷ lệ nợ xấu tháng 9/2026"""


async def test_a_goal_of_several_questions_runs_one_step_each_with_its_chart(
    seed: Seed,
    credit: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, credit, monkeypatch)
    run = agent_run(bank, goal=REPORT)
    await drain()
    status, stop, current, narrative, *_ = run_row(seed, run)
    assert (status, stop) == ("succeeded", "completed")
    recorded = steps(seed, run)
    assert [(s[1], s[3]) for s in recorded] == [("plan", "ok")] + [("query", "ok")] * 3
    assert current == 4
    # Each cut is drawn; the single number has nothing to compare, so no chart.
    charts = [s[6].get("chart_spec") for s in recorded[1:]]
    assert [c and (c["type"], c["x"]) for c in charts] == [("bar", "vung"), ("bar", "vung"), None]
    # The plan names every question, and the run's answer gathers them in order.
    plan = recorded[0][6]
    assert [q["text"] for q in plan["questions"]] == [
        "Tỷ lệ nợ xấu theo vùng tháng 9/2026",
        "Dư nợ theo vùng tháng 9/2026",
        "Tỷ lệ nợ xấu tháng 9/2026",
    ]
    answer = narrative[-1]["text"]
    assert answer.index("1. Tỷ lệ nợ xấu") < answer.index("2. Dư nợ") < answer.index("3. Tỷ lệ")
    assert (
        seed.one(
            "SELECT jsonb_array_length(steps->'steps') FROM qa.plan WHERE agent_run_id = %s", run
        )
        == 3
    )


async def test_a_question_the_space_cannot_read_stops_the_plan_and_names_it(
    seed: Seed,
    credit: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, credit, monkeypatch)
    run = agent_run(bank, goal="Tỷ lệ nợ xấu theo vùng tháng 9/2026\nLợi nhuận theo quý")
    await drain()
    status, stop, _, _, error, *_ = run_row(seed, run)
    assert (status, stop) == ("failed", "error")
    assert "Lợi nhuận theo quý" in error and steps(seed, run) == []


async def test_a_paused_report_carries_on_with_its_next_question(
    seed: Seed,
    credit: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, credit, monkeypatch)
    run = agent_run(bank, goal=REPORT)
    real = runs.run_query
    calls = {"n": 0}

    async def pause_after_first(**kwargs: Any) -> Any:
        calls["n"] += 1
        out = await real(**kwargs)
        if calls["n"] == 1:
            with seed.conn.cursor() as cur:
                cur.execute(
                    "UPDATE agent.agent_run SET status = 'paused' WHERE id = %s", (str(run),)
                )
        return out

    monkeypatch.setattr(runs, "run_query", pause_after_first)
    await drain()
    assert run_row(seed, run)[0] == "paused"
    assert [s[1] for s in steps(seed, run)] == ["plan", "query"]

    with seed.conn.cursor() as cur:
        cur.execute("UPDATE agent.agent_run SET status = 'running' WHERE id = %s", (str(run),))
    created(bank, run, status="running", previous="paused")
    await drain()
    assert run_row(seed, run)[:2] == ("succeeded", "completed")
    assert [s[1] for s in steps(seed, run)] == ["plan", "query", "query", "query"]
    assert calls["n"] == 3


async def test_a_goal_of_too_many_questions_is_refused_before_any_is_read(
    seed: Seed,
    credit: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Each question may cost a model call: the plan is capped before the first one.
    bank = build(seed, credit, monkeypatch)
    calls = {"n": 0}
    real = runs._plan_step

    async def counted(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        return await real(*args, **kwargs)

    monkeypatch.setattr(runs, "_plan_step", counted)
    goal = "\n".join(["Tỷ lệ nợ xấu theo vùng tháng 9/2026"] * (runs.MAX_QUESTIONS + 1))
    run = agent_run(bank, goal=goal)
    await drain()
    status, stop, *_, error, _, _, detail = run_row(seed, run)
    assert (status, stop, calls["n"]) == ("failed", "budget_steps", 0)
    assert str(runs.MAX_QUESTIONS) in error


async def test_planning_stops_once_its_model_calls_reach_the_token_limit(
    seed: Seed,
    credit: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, credit, monkeypatch)
    real = runs._plan_step
    calls = {"n": 0}

    async def costly(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        planned = await real(*args, **kwargs)
        planned.tokens = 600  # as if the planner model read it
        return planned

    monkeypatch.setattr(runs, "_plan_step", costly)
    run = agent_run(bank, goal=REPORT, limits={"max_tokens": 1000})
    await drain()
    status, stop, *_ = run_row(seed, run)
    assert (status, stop) == ("failed", "budget_tokens") and calls["n"] == 2
    assert steps(seed, run) == []
