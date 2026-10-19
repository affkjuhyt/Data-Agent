"""worker-agent answers a recorded question on the V3 database (UC-01-01, G1 frame)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

import pytest

from services.worker_agent.events import OutboxTableSource
from services.worker_agent.main import run_once
from services.worker_agent.questions import HANDLERS

from .conftest import Seed

pytestmark = [pytest.mark.integration]


@dataclass
class Asked:
    org: uuid.UUID
    space: uuid.UUID
    question: uuid.UUID
    outbox: uuid.UUID
    users: tuple[uuid.UUID, uuid.UUID]


def ask(seed: Seed, text: str = "Giá trần E5RON92 kỳ 02/07/2026 là bao nhiêu?") -> Asked:
    org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="Tổ chức thử")
    u1 = seed.row("org.app_user", org_id=org, external_id="kc-a", username="a", display_name="A")
    u2 = seed.row("org.app_user", org_id=org, external_id="kc-b", username="b", display_name="B")
    space = seed.row("org.data_space", org_id=org, code="DS-XANG", name="Xăng dầu", steward_id=u1)
    conv = seed.row("qa.conversation", org_id=org, data_space_id=space, user_id=u1)
    question = seed.row("qa.question", org_id=org, conversation_id=conv, seq=1, text=text)
    outbox = seed.row(
        "qa.outbox",
        org_id=org,
        aggregate_type="question",
        aggregate_id=question,
        event_type="question.received",
        payload=json.dumps({"question_id": str(question), "user_id": str(u1)}),
    )
    return Asked(org, space, question, outbox, (u1, u2))


def source() -> OutboxTableSource:
    return OutboxTableSource(schema="qa", event_types=frozenset(HANDLERS))


def repeat(seed: Seed, asked: Asked, text: str) -> Asked:
    conv = seed.one("SELECT conversation_id FROM qa.question WHERE id = %s", asked.question)
    seq = seed.one("SELECT coalesce(max(seq), 0) + 1 FROM qa.question WHERE conversation_id = %s", conv)
    question = seed.row(
        "qa.question", org_id=asked.org, conversation_id=conv, seq=seq, text=text
    )
    outbox = seed.row(
        "qa.outbox",
        org_id=asked.org,
        aggregate_type="question",
        aggregate_id=question,
        event_type="question.received",
        payload=json.dumps({"question_id": str(question), "user_id": str(asked.users[0])}),
    )
    return Asked(asked.org, asked.space, question, outbox, asked.users)


async def drain() -> int:
    total = 0
    while handled := await run_once(source(), HANDLERS, batch=50):
        total += handled
    return total


async def test_a_question_naming_no_known_metric_ends_in_a_coded_refusal(seed: Seed) -> None:
    # The Data Space has no semantic layer at all: nothing in the question can be placed.
    asked = ask(seed)
    assert await drain() >= 1
    assert seed.one("SELECT status FROM qa.question WHERE id = %s", asked.question) == "refused"
    assert seed.one("SELECT answered_at IS NOT NULL FROM qa.question WHERE id = %s", asked.question)
    kind, scope, text = seed.one(
        "SELECT kind, product_scope, text FROM qa.answer WHERE question_id = %s", asked.question
    )
    assert (kind, scope) == ("refusal", "not_applicable")
    assert "Chưa nhận ra chỉ số" in text
    version, steps = seed.one(
        "SELECT version, steps FROM qa.plan WHERE question_id = %s", asked.question
    )
    # Plan IR v2 (tab 5 G3.1): the period was read, no step could be planned.
    assert version == 1 and steps["version"] == 2 and steps["steps"] == []
    assert seed.one("SELECT published_at IS NOT NULL FROM qa.outbox WHERE id = %s", asked.outbox)


@pytest.mark.req("AC-07-09-02")
async def test_unmapped_phrase_accumulates_review_evidence(seed: Seed) -> None:
    text = "Thuật ngữ ngân hàng chưa ánh xạ"
    first = ask(seed, text)
    for _ in range(5):
        repeat(seed, first, text)
    assert await drain() >= 6
    count, samples = seed.one(
        "SELECT ask_count, sample_questions FROM context.unmapped_phrase"
        " WHERE org_id = %s AND data_space_id = %s AND phrase_norm = %s",
        first.org,
        first.space,
        "thuat ngan hang chua",
    )
    assert count == 6 and text in samples and len(samples) == 1


async def test_redelivered_event_changes_nothing(seed: Seed) -> None:
    asked = ask(seed)
    await drain()
    # The event comes back (the ack was lost after the answer committed).
    with seed.conn.cursor() as cur:
        cur.execute("UPDATE qa.outbox SET published_at = NULL WHERE id = %s", (str(asked.outbox),))
    await drain()
    assert seed.one("SELECT count(*) FROM qa.answer WHERE question_id = %s", asked.question) == 1
    assert seed.one("SELECT count(*) FROM qa.plan WHERE question_id = %s", asked.question) == 1


async def test_questions_of_several_organisations_are_each_answered_in_their_own(
    seed: Seed,
) -> None:
    first, second = ask(seed), ask(seed, "Lãi suất 12 tháng tuần này?")
    await drain()
    for asked in (first, second):
        org_of_answer = seed.one(
            "SELECT org_id FROM qa.answer WHERE question_id = %s", asked.question
        )
        assert str(org_of_answer) == str(asked.org)
