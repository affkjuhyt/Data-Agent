"""Verified queries (R1-08): a question a steward approved an SQL for is answered by it, VERIFIED,
before any SQL is written; a liked exploratory answer is proposed as one, and only the checker's
run sends it to the steward."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from services.worker_agent import liked, semantic_review
from services.worker_agent.events import OutboxTableSource
from services.worker_agent.main import run_once
from services.worker_agent.sqlgen.verified import TRUST_VERIFIED

from .conftest import Seed
from .test_answers import Bank, answer_of, build, drain
from .test_sql import GOOD, QUESTION, Script, asked, catalogue
from .test_sql import credit as credit
from .test_sql import warehouse as warehouse

pytestmark = [pytest.mark.integration]

APPROVED = (
    "SELECT branch_code AS ma_chi_nhanh, COUNT(*) AS so_khoan_vay FROM bank.loan"
    " WHERE as_of_date >= :period_start AND as_of_date < :period_end GROUP BY branch_code"
)


def example(bank: Bank, *, status: str = "active", question: str | None = None) -> uuid.UUID:
    return bank.seed.row(
        "context.example_query",
        org_id=bank.org,
        data_space_id=bank.space,
        question=question or "Số khoản vay theo mã chi nhánh tháng 8/2026",
        sql=APPROVED,
        status=status,
        created_by=bank.user,
    )


async def test_an_approved_query_answers_its_question_verified_without_the_model(
    seed: Seed,
    credit: dict[str, Any],  # noqa: F811  (the fixture)
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, credit, monkeypatch)
    approved = example(bank)
    script = Script([])
    # Asked for September: the approved query was written for August, with the period bound.
    monkeypatch.setattr("services.worker_agent.questions._gateway", script)
    monkeypatch.setattr("services.worker_agent.questions._gateway_read", True)
    catalogue(bank)
    q = bank.ask("Số khoản vay theo mã chi nhánh tháng 9/2026")
    await drain()
    status, kind, text, figures, _, _ = answer_of(seed, q)
    assert (status, kind) == ("answered", "data"), text
    assert script.asked == []
    assert all(f["trust"] == TRUST_VERIFIED for f in figures)
    assert {f["dimensions"]["ma_chi_nhanh"] for f in figures} >= {"HN1", "SG1"}
    assert "Đã kiểm" in text
    plan = seed.one("SELECT steps FROM qa.plan WHERE question_id = %s", q)
    assert plan["path"] == "verified_query"
    assert plan["steps"][0]["args"]["example_query_id"] == str(approved)
    ran = seed.one(
        "SELECT sql_executed FROM query.query_run WHERE caller_kind = 'question' AND caller_id = %s",
        q,
    )
    assert "2026-09-01" in ran and "2026-10-01" in ran


async def test_an_example_not_yet_approved_is_not_used(
    seed: Seed,
    credit: dict[str, Any],  # noqa: F811  (the fixture)
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, credit, monkeypatch)
    example(bank, status="pending_review", question=QUESTION)
    q = asked(bank, Script([GOOD[0], GOOD[1]] * 3), monkeypatch)
    await drain()
    _, kind, _, figures, _, _ = answer_of(seed, q)
    assert kind == "data"
    assert all(f["trust"] == "Khám phá (truy vấn do mô hình viết)" for f in figures)


async def test_a_liked_exploratory_answer_is_proposed_and_checked_before_review(
    seed: Seed,
    credit: dict[str, Any],  # noqa: F811  (the fixture)
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, credit, monkeypatch)
    q = asked(bank, Script([GOOD[0], GOOD[1]] * 3), monkeypatch)
    await drain()
    answer = answer_of(seed, q)[5]
    # What POST /v1/answers/{id}/feedback rating=up leaves for an exploratory answer.
    seed.row(
        "qa.outbox",
        org_id=bank.org,
        aggregate_type="feedback",
        aggregate_id=uuid.uuid4(),
        event_type=liked.HANDLER,
        payload=json.dumps(
            {
                "answer_id": str(answer),
                "question_id": str(q),
                "data_space_id": str(bank.space),
                "user_id": str(bank.user),
            }
        ),
    )
    qa_source = OutboxTableSource(schema="qa", event_types=frozenset(liked.HANDLERS))
    while await run_once(qa_source, liked.HANDLERS, batch=10):
        pass
    proposed = seed.all(
        "SELECT question, origin, status FROM context.example_query WHERE data_space_id = %s",
        bank.space,
    )
    assert proposed == [(QUESTION, "feedback", "draft")]
    # The checker runs it on the warehouse it reads, so it goes to the steward.
    context_source = OutboxTableSource(
        schema="context", event_types=frozenset({semantic_review.EXAMPLE_CREATED})
    )
    while await run_once(context_source, semantic_review.HANDLERS, batch=10):
        pass
    assert (
        seed.one("SELECT status FROM context.example_query WHERE data_space_id = %s", bank.space)
        == "pending_review"
    )
    # Liked again: the same question is not proposed twice.
    seed.row(
        "qa.outbox",
        org_id=bank.org,
        aggregate_type="feedback",
        aggregate_id=uuid.uuid4(),
        event_type=liked.HANDLER,
        payload=json.dumps(
            {
                "answer_id": str(answer),
                "question_id": str(q),
                "data_space_id": str(bank.space),
                "user_id": str(bank.user),
            }
        ),
    )
    while await run_once(qa_source, liked.HANDLERS, batch=10):
        pass
    assert (
        seed.one("SELECT count(*) FROM context.example_query WHERE data_space_id = %s", bank.space)
        == 1
    )
