"""The nightly scheduler (services/worker_agent/nightly_evals.py): it requests one run per active
context eval set across organisations, answered as the Data Space's steward, skips a set whose
nightly run already started, and the worker's eval handler completes what it requested."""

from __future__ import annotations

import datetime
import json
import uuid

import pytest

from services.worker_agent.eval_runs import run_eval
from services.worker_agent.events import Event
from services.worker_agent.nightly_evals import request_all

from .test_questions import ask

pytestmark = [pytest.mark.integration]

NOW = datetime.datetime(2026, 10, 20, 19, 0, tzinfo=datetime.UTC)


def in_use(seed, asked) -> None:
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE org.data_space SET status = 'active' WHERE id = %s", (str(asked.space),)
        )


def eval_set(seed, asked, *, status: str = "active", subject_type: str = "context") -> uuid.UUID:
    subject = (
        {"data_space_id": asked.space}
        if subject_type == "context"
        else {"subject_id": uuid.uuid4()}
    )
    set_id = seed.row(
        "eval.eval_set",
        org_id=asked.org,
        subject_type=subject_type,
        **subject,
        name=f"Nightly {status} {subject_type}",
        status=status,
        created_by=asked.users[0],
    )
    seed.row(
        "eval.eval_item",
        org_id=asked.org,
        eval_set_id=set_id,
        category="refusal",
        input=json.dumps({"question": "Câu hỏi đánh giá hằng đêm"}),
        expected=json.dumps({"kind": "refusal"}),
        created_by=asked.users[0],
    )
    return set_id


def runs_of(seed, set_id: uuid.UUID) -> list[tuple]:
    with seed.conn.cursor() as cur:
        cur.execute(
            "SELECT id, trigger, subject_version, item_count, status FROM eval.eval_run"
            " WHERE eval_set_id = %s",
            (str(set_id),),
        )
        return cur.fetchall()


async def test_one_run_is_requested_per_active_context_set_and_answered(seed) -> None:
    asked = ask(seed, "Câu hỏi đánh giá không có chỉ số đã biết")
    in_use(seed, asked)
    active = eval_set(seed, asked)
    archived = eval_set(seed, asked, status="archived")
    agent_set = eval_set(seed, asked, subject_type="skill")

    requested = await request_all(NOW)

    (run,) = runs_of(seed, active)
    run_id, trigger, version, count, status = run
    assert str(run_id) in {str(r) for r in requested}
    # 19:00 UTC on the 20th is 02:00 on the 21st in Hà Nội: the label is the bank's day.
    assert (trigger, version, count, status) == (
        "nightly",
        "context@nightly-2026-10-21",
        1,
        "running",
    )
    assert runs_of(seed, archived) == [] and runs_of(seed, agent_set) == []
    # A Data Space still being set up has no nightly run.
    draft = ask(seed, "Câu hỏi đánh giá không có chỉ số đã biết")
    draft_set = eval_set(seed, draft)
    await request_all(NOW)
    assert runs_of(seed, draft_set) == []
    payload = seed.one(
        "SELECT payload FROM eval.outbox WHERE aggregate_id = %s AND event_type = 'eval_run.requested'",
        run_id,
    )
    assert payload["requested_by"] == str(asked.users[0]) and payload["trigger"] == "nightly"

    await run_eval(
        Event(
            uuid.uuid4(),
            asked.org,
            "eval_run.requested",
            "eval_run",
            uuid.UUID(str(run_id)),
            payload,
        )
    )
    assert seed.one("SELECT status FROM eval.eval_run WHERE id = %s", run_id) == "completed"


async def test_a_set_whose_nightly_run_started_is_not_run_twice(seed) -> None:
    asked = ask(seed, "Câu hỏi đánh giá không có chỉ số đã biết")
    in_use(seed, asked)
    active = eval_set(seed, asked)
    await request_all(NOW)
    await request_all(NOW + datetime.timedelta(hours=1))
    assert len(runs_of(seed, active)) == 1
    # The next night it is due again.
    await request_all(NOW + datetime.timedelta(days=1))
    assert len(runs_of(seed, active)) == 2
