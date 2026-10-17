"""Hỏi tiếp và đào sâu (UC-01-04) and "vì sao" (UC-03-01) on the bank of test_answers.py: a
question in the same conversation is read against the one before it."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from . import test_answers
from .conftest import Seed
from .test_answers import Bank, answer_of, build, drain

pytestmark = [pytest.mark.integration]

# The bank's source schema, a module fixture of test_answers.py.
warehouse = test_answers.warehouse


class Conversation:
    def __init__(self, bank: Bank) -> None:
        self.bank = bank
        self.id = bank.seed.row(
            "qa.conversation", org_id=bank.org, data_space_id=bank.space, user_id=bank.user
        )
        self.seq = 0

    def ask(self, text: str) -> uuid.UUID:
        self.seq += 1
        question = self.bank.seed.row(
            "qa.question", org_id=self.bank.org, conversation_id=self.id, seq=self.seq, text=text
        )
        self.bank.seed.row(
            "qa.outbox",
            org_id=self.bank.org,
            aggregate_type="question",
            aggregate_id=question,
            event_type="question.received",
            payload=json.dumps({"question_id": str(question), "user_id": str(self.bank.user)}),
        )
        return question


def plan_of(seed: Seed, question: uuid.UUID) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return seed.one(  # type: ignore[return-value]
        "SELECT chips, steps->'steps' FROM qa.plan WHERE question_id = %s "
        "ORDER BY version DESC LIMIT 1",
        question,
    )


@pytest.mark.req("AC-01-04-01")
async def test_a_follow_up_keeps_metric_and_period_and_cuts_by_region(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    talk = Conversation(build(seed, warehouse, monkeypatch))
    talk.ask("Dư nợ tháng 9/2026")
    await drain()
    q = talk.ask("còn theo vùng thì sao?")
    await drain()
    status, kind, text, figures, _, _ = answer_of(seed, q)
    assert (status, kind) == ("answered", "data"), text
    chips, _ = plan_of(seed, q)
    assert [(c["kind"], c["source"]) for c in chips] == [
        ("metric", "inherited"),
        ("period", "inherited"),
        ("dimension", "default"),
    ]
    rows = [f for f in figures if "expr" not in f["source"]]
    assert [(f["dimensions"]["vung"], float(f["value"])) for f in rows] == [
        ("Miền Bắc", 2000.0),
        ("Miền Nam", 1000.0),
    ]


@pytest.mark.req("AC-01-04-02")
async def test_a_bar_drilled_into_filters_its_member_and_cuts_by_the_next_dimension(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    talk = Conversation(build(seed, warehouse, monkeypatch))
    first = talk.ask("Dư nợ theo vùng tháng 9/2026")
    await drain()
    # The chart offers the metric's other allowed cuts for a picked bar.
    spec = seed.one("SELECT chart_spec FROM qa.answer WHERE question_id = %s", first)
    assert spec["drill"] == [{"code": "san_pham", "name": "sản phẩm"}]
    q = talk.ask("Đi sâu Miền Bắc theo sản phẩm")
    await drain()
    status, _, text, figures, _, _ = answer_of(seed, q)
    assert status == "answered", text
    chips, _ = plan_of(seed, q)
    assert {(c["kind"], c["value"]) for c in chips} >= {
        ("filter", "vung=Miền Bắc"),
        ("dimension", "san_pham"),
    }
    assert ("dimension", "vung") not in {(c["kind"], c["value"]) for c in chips}
    rows = [f for f in figures if "expr" not in f["source"]]
    # Miền Bắc in September, internal loans excluded: all of it is product TD.
    assert [(f["dimensions"]["san_pham"], float(f["value"])) for f in rows] == [("TD", 2000.0)]


@pytest.mark.req("AC-01-04-04")
async def test_a_cut_the_metric_does_not_allow_is_refused_with_the_ones_it_does(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    entity = seed.one(
        "SELECT id FROM context.semantic_entity WHERE data_space_id = %s AND code = 'khoan_vay'",
        bank.space,
    )
    seed.row(
        "context.semantic_dimension",
        org_id=bank.org,
        data_space_id=bank.space,
        entity_id=entity,
        code="nhom_no",
        name="nhóm nợ",
        expression="group_code",
    )
    q = Conversation(bank).ask("Dư nợ theo nhóm nợ tháng 9/2026")
    await drain()
    status, kind, text, _, _, _ = answer_of(seed, q)
    assert (status, kind) == ("refused", "refusal")
    assert text == "Dư nợ không chia được theo nhóm nợ. Có thể chia theo: sản phẩm, vùng."
    assert seed.one("SELECT count(*) FROM query.query_run WHERE caller_id = %s", q) == 0


@pytest.mark.req("AC-03-01-01")
async def test_a_why_question_is_answered_and_proposes_the_decomposition(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    q = Conversation(build(seed, warehouse, monkeypatch)).ask(
        "Vì sao tỷ lệ nợ xấu tháng 9/2026 tăng?"
    )
    await drain()
    status, kind, text, _, _, _ = answer_of(seed, q)
    assert (status, kind) == ("answered", "data"), text
    _, steps = plan_of(seed, q)
    proposal = steps[1]
    assert (proposal["kind"], proposal["checkpoint"]) == ("skill", "confirm")
    # No cut asked: the metric's first allowed one, alphabetically ("sản phẩm" < "vùng").
    assert proposal["args"] == {
        "category": "decomposition",
        "params": {
            "metric": "NPL_RATIO",
            "dimension": "san_pham",
            "period": "2026-09",
            "base_period": "2026-08",
        },
    }
