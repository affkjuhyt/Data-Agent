"""The data-prep agent for real (UC-06-01 step 2): the model proposes the steps from the user's
words, the worker holds them to the rules every step meets, applies them, and answers in the
pipeline's conversation."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from services.worker_agent import prep, questions
from services.worker_agent.events import OutboxTableSource
from services.worker_agent.main import run_once

from .conftest import Seed
from .test_answers import Bank, FakeGateway, build, warehouse  # noqa: F401  (fixture)
from .test_pipelines import DROP_INTERNAL, SELECT, pipeline

pytestmark = [pytest.mark.integration]


async def drain() -> None:
    source = OutboxTableSource(schema="pipeline", event_types=frozenset(prep.HANDLERS))
    while await run_once(source, prep.HANDLERS, batch=10):
        pass


def say(bank: Bank, pid: uuid.UUID, text: str) -> None:
    bank.seed.row(
        "pipeline.outbox",
        org_id=bank.org,
        aggregate_type="pipeline",
        aggregate_id=pid,
        event_type=prep.MESSAGE,
        payload=json.dumps({"pipeline_id": str(pid), "text": text, "requested_by": str(bank.user)}),
    )


def answers(seed: Seed, pid: uuid.UUID) -> list[dict[str, Any]]:
    return [
        r[0]
        for r in seed.all(
            "SELECT payload FROM pipeline.outbox WHERE aggregate_id = %s AND event_type = %s"
            " ORDER BY created_at",
            pid,
            prep.ANSWERED,
        )
    ]


def steps_of(seed: Seed, pid: uuid.UUID) -> list[dict[str, Any]]:
    return seed.one("SELECT steps FROM pipeline.pipeline WHERE id = %s", pid)


def model(monkeypatch: pytest.MonkeyPatch, content: dict[str, Any] | None) -> None:
    monkeypatch.setattr(questions, "_gateway", FakeGateway(content) if content else None)
    monkeypatch.setattr(questions, "_gateway_read", True)


@pytest.mark.req("AC-06-01-01")
async def test_the_agent_adds_the_steps_it_was_asked_for(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    pid = pipeline(bank, [SELECT])
    model(
        monkeypatch,
        {
            "reply": "Đã thêm bước bỏ cho vay nội bộ.",
            "question": None,
            "steps": [SELECT, DROP_INTERNAL],
        },
    )
    say(bank, pid, "Bỏ các khoản cho vay nội bộ")
    await drain()
    steps = steps_of(seed, pid)
    assert [s["kind"] for s in steps] == ["compute", "filter"]
    assert [s["is_new"] for s in steps] == [False, True]
    assert answers(seed, pid)[-1]["text"] == "Đã thêm bước bỏ cho vay nội bộ."
    assert answers(seed, pid)[-1]["changed"] == [2]


async def test_an_ambiguous_request_is_asked_back_and_changes_nothing(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    pid = pipeline(bank, [SELECT])
    model(
        monkeypatch,
        {"reply": "", "question": "Bỏ theo mã sản phẩm hay theo chi nhánh?", "steps": [SELECT]},
    )
    say(bank, pid, "Bỏ mấy dòng lạ đi")
    await drain()
    assert [s["kind"] for s in steps_of(seed, pid)] == ["compute"]
    last = answers(seed, pid)[-1]
    assert last["asks"] is True and last["text"].startswith("Bỏ theo mã sản phẩm")


@pytest.mark.parametrize(
    "bad",
    [
        {**DROP_INTERNAL, "drop_condition": None},  # BR-23: a filter says what it drops
        {**DROP_INTERNAL, "code": "DELETE FROM loan"},  # read-only
        {**DROP_INTERNAL, "code": "SELECT * FROM branch"},  # not t, not an input
        {**DROP_INTERNAL, "code": "SELECT * FROM t WHERE khong_co_cot = 1"},  # does not compile
    ],
)
async def test_a_proposal_that_breaks_a_rule_is_not_applied(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    bad: dict[str, Any],
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    pid = pipeline(bank, [SELECT])
    model(monkeypatch, {"reply": "Xong.", "question": None, "steps": [SELECT, bad]})
    say(bank, pid, "Bỏ cho vay nội bộ")
    await drain()
    assert [s["kind"] for s in steps_of(seed, pid)] == ["compute"]
    last = answers(seed, pid)[-1]
    assert last["rejected"] is True and "chưa áp dụng" in last["text"]


async def test_without_a_model_the_agent_says_so(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    pid = pipeline(bank, [SELECT])
    model(monkeypatch, None)
    say(bank, pid, "Bỏ cho vay nội bộ")
    await drain()
    assert steps_of(seed, pid) == [SELECT]
    assert "Chưa nối được mô hình" in answers(seed, pid)[-1]["text"]
