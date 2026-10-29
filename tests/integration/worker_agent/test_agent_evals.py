"""An agent version's eval run is the gate before it is deployed (agent_evals.py, R1-12): made
from agent_config.eval_requested, scored through the QA handler, and only a run at the set's bar
moves the config to `passed`."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from services.worker_agent import agent_evals
from services.worker_agent.events import OutboxTableSource
from services.worker_agent.main import run_once

from .conftest import Seed
from .test_questions import Asked, ask

pytestmark = [pytest.mark.integration]


def eval_set(
    seed: Seed, asked: Asked, *, space: uuid.UUID, expected: str, items: int = 5, **extra: Any
) -> uuid.UUID:
    """A set every item of which expects `expected`: the unknown questions are refused, so
    "refusal" passes and "data" fails."""
    found = seed.row(
        "eval.eval_set",
        org_id=asked.org,
        data_space_id=space,
        name="Bộ thử",
        pass_threshold=0.9,
        min_items=items,
        created_by=asked.users[0],
        **{"subject_type": "context", **extra},
    )
    for number in range(items):
        seed.row(
            "eval.eval_item",
            org_id=asked.org,
            eval_set_id=found,
            category="refusal",
            input=json.dumps({"question": f"Câu hỏi đánh giá {number}"}),
            expected=json.dumps({"kind": expected}),
            created_by=asked.users[0],
        )
    return found


def agent_asking_for_eval(seed: Seed, asked: Asked) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """An agent whose version 1 asks for its eval run, as POST /v1/agents/{id}/eval-runs leaves
    it: the run id reserved on the config, the config testing, the event in agent.outbox."""
    owner = asked.users[0]
    agent_id = seed.row(
        "agent.agent_def",
        org_id=asked.org,
        data_space_id=asked.space,
        code=f"a-{uuid.uuid4().hex[:6]}",
        name="Agent thử",
        kind="analysis",
        owner_user_id=owner,
        account_user_id=uuid.uuid4(),
        current_config_version=1,
    )
    run_id = uuid.uuid4()
    config = seed.row(
        "agent.agent_config",
        org_id=asked.org,
        agent_id=agent_id,
        version=1,
        goal="g",
        status="testing",
        eval_run_id=run_id,
    )
    seed.row(
        "agent.outbox",
        org_id=asked.org,
        aggregate_type="agent_config",
        aggregate_id=config,
        event_type=agent_evals.HANDLER,
        payload=json.dumps(
            {
                "agent_id": str(agent_id),
                "config_version": 1,
                "eval_run_id": str(run_id),
                "requested_by": str(owner),
                "previous_status": "draft",
                "status": "testing",
            }
        ),
    )
    return agent_id, config, run_id


async def drain() -> None:
    source = OutboxTableSource(schema="agent", event_types=frozenset(agent_evals.HANDLERS))
    while await run_once(source, agent_evals.HANDLERS, batch=10):
        pass


def told(seed: Seed, asked: Asked, run_id: uuid.UUID) -> list[tuple[Any, ...]]:
    return seed.all(
        "SELECT user_id, kind, title FROM notify.notification WHERE source_ref = %s", run_id
    )


async def test_a_version_at_the_bar_passes_and_may_be_deployed(seed: Seed) -> None:
    asked = ask(seed, "Câu hỏi đánh giá không có chỉ số đã biết")
    used = eval_set(seed, asked, space=asked.space, expected="refusal")
    _, config, run_id = agent_asking_for_eval(seed, asked)
    await drain()
    assert seed.one(
        "SELECT eval_set_id, trigger, trigger_ref, status, item_count, passed"
        " FROM eval.eval_run WHERE id = %s",
        run_id,
    ) == (used, "deploy", config, "completed", 5, 5)
    assert seed.one("SELECT status FROM agent.agent_config WHERE id = %s", config) == "passed"
    assert told(seed, asked, run_id) == [
        (asked.users[0], "task_done", "Phiên bản 1 của agent đạt bộ đánh giá")
    ]


async def test_a_version_below_the_bar_stays_testing_and_says_why(seed: Seed) -> None:
    asked = ask(seed, "Câu hỏi đánh giá không có chỉ số đã biết")
    eval_set(seed, asked, space=asked.space, expected="data")
    _, config, run_id = agent_asking_for_eval(seed, asked)
    await drain()
    assert seed.one("SELECT status, passed FROM eval.eval_run WHERE id = %s", run_id) == (
        "completed",
        0,
    )
    assert seed.one("SELECT status FROM agent.agent_config WHERE id = %s", config) == "testing"
    [(user, kind, title)] = told(seed, asked, run_id)
    assert (user, kind) == (asked.users[0], "alert") and "chưa đạt" in title


async def test_too_few_items_is_below_the_bar_whatever_they_score(seed: Seed) -> None:
    asked = ask(seed, "Câu hỏi đánh giá không có chỉ số đã biết")
    small = eval_set(seed, asked, space=asked.space, expected="refusal")
    with seed.conn.cursor() as cur:
        cur.execute("UPDATE eval.eval_set SET min_items = 100 WHERE id = %s", (str(small),))
    _, config, _ = agent_asking_for_eval(seed, asked)
    await drain()
    assert seed.one("SELECT status FROM agent.agent_config WHERE id = %s", config) == "testing"


async def test_the_agents_own_set_wins_and_another_spaces_set_is_never_used(
    seed: Seed,
) -> None:
    asked = ask(seed, "Câu hỏi đánh giá không có chỉ số đã biết")
    elsewhere = seed.row(
        "org.data_space", org_id=asked.org, code="DS-KHAC", name="Khác", steward_id=asked.users[0]
    )
    eval_set(seed, asked, space=elsewhere, expected="refusal")
    eval_set(seed, asked, space=asked.space, expected="data")
    agent_id, _, run_id = agent_asking_for_eval(seed, asked)
    own = eval_set(
        seed,
        asked,
        space=asked.space,
        expected="refusal",
        subject_type="agent",
        subject_id=agent_id,
    )
    await drain()
    assert seed.one("SELECT eval_set_id FROM eval.eval_run WHERE id = %s", run_id) == own


async def test_a_space_without_a_set_tells_the_owner_and_makes_no_run(seed: Seed) -> None:
    asked = ask(seed, "Câu hỏi đánh giá không có chỉ số đã biết")
    elsewhere = seed.row(
        "org.data_space", org_id=asked.org, code="DS-KHAC", name="Khác", steward_id=asked.users[0]
    )
    eval_set(seed, asked, space=elsewhere, expected="refusal")
    _, config, run_id = agent_asking_for_eval(seed, asked)
    await drain()
    assert seed.one("SELECT count(*) FROM eval.eval_run WHERE id = %s", run_id) == 0
    assert seed.one("SELECT status FROM agent.agent_config WHERE id = %s", config) == "testing"
    [(_, kind, title)] = told(seed, asked, run_id)
    assert kind == "alert" and "Chưa đánh giá được" in title
