"""The semantic review path and the context evaluation run (UC-07-01/02/03/06), end to end on the
V3 database and a PostgreSQL source: an eval run scores real answers, a metric draft opens a
metric_change proposal with its preview and impact, and an accepted proposal is what makes the
agent use the new version or synonym."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from services.worker_agent import evaluation, proposals, semantic_review
from services.worker_agent.events import OutboxTableSource
from services.worker_agent.main import run_once

from .conftest import Seed
from .test_answers import Bank, answer_of, build, drain, warehouse  # noqa: F401  (fixture)

pytestmark = [pytest.mark.integration]


async def drain_all() -> None:
    for schema, handlers in (
        ("eval", evaluation.HANDLERS),
        ("context", semantic_review.HANDLERS),
        ("inbox", {proposals.DECIDED: proposals.decided}),
    ):
        source = OutboxTableSource(schema=schema, event_types=frozenset(handlers))
        while await run_once(source, handlers, batch=50):
            pass


def eval_set(bank: Bank, items: list[tuple[dict[str, Any], dict[str, Any]]]) -> uuid.UUID:
    s = bank.seed.row(
        "eval.eval_set",
        org_id=bank.org,
        data_space_id=bank.space,
        subject_type="context",
        name="Bộ câu hỏi chuẩn",
    )
    for asked, expected in items:
        bank.seed.row(
            "eval.eval_item",
            org_id=bank.org,
            eval_set_id=s,
            input=json.dumps(asked),
            expected=json.dumps(expected),
        )
    return s


ITEMS = [
    ({"question": "Dư nợ tháng 9/2026"}, {"metric": "DU_NO", "value": 3000}),
    ({"question": "Giá vàng hôm nay là bao nhiêu?"}, {"behavior": "refuse"}),
    ({"question": "Tỷ lệ nợ xấu tháng 9/2026"}, {"metric": "NPL_RATIO", "value": 99}),
]


@pytest.mark.req("AC-07-06-01")
async def test_an_eval_run_scores_real_answers(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    s = eval_set(bank, ITEMS)
    run = seed.row(
        "eval.eval_run",
        org_id=bank.org,
        eval_set_id=s,
        subject_version="ctx",
        trigger="manual",
        status="running",
    )
    seed.row(
        "eval.outbox",
        org_id=bank.org,
        aggregate_type="eval_run",
        aggregate_id=run,
        event_type="eval_run.requested",
        payload=json.dumps(
            {
                "eval_run_id": str(run),
                "eval_set_id": str(s),
                "data_space_id": str(bank.space),
                "requested_by": str(bank.user),
            }
        ),
    )
    await drain_all()
    status, passed, failed, rate = seed.one(
        "SELECT status, passed, failed, pass_rate::float FROM eval.eval_run WHERE id = %s", run
    )
    assert (status, passed, failed) == ("completed", 2, 1)
    assert round(rate, 4) == 0.6667
    causes = [
        r[0]
        for r in seed.all(
            "SELECT cause FROM eval.eval_result WHERE eval_run_id = %s AND NOT passed", run
        )
    ]
    assert len(causes) == 1 and "instead of 99" in causes[0]


def decide(bank: Bank, proposal: uuid.UUID, status: str, reviewer: uuid.UUID) -> None:
    with bank.seed.conn.cursor() as cur:
        cur.execute("UPDATE inbox.proposal SET status = %s WHERE id = %s", (status, str(proposal)))
    bank.seed.row(
        "inbox.outbox",
        org_id=bank.org,
        aggregate_type="proposal",
        aggregate_id=proposal,
        event_type="proposal.decided",
        payload=json.dumps(
            {"proposal_id": str(proposal), "status": status, "reviewer_id": str(reviewer)}
        ),
    )


@pytest.mark.req("AC-07-01-01", "AC-07-03-01")
async def test_a_metric_draft_is_previewed_proposed_and_certified_on_acceptance(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    eval_set(bank, ITEMS[:1])
    du_no = seed.one(
        "SELECT id FROM context.metric WHERE code = 'DU_NO' AND data_space_id = %s", bank.space
    )
    draft = seed.row(
        "context.metric_version",
        org_id=bank.org,
        metric_id=du_no,
        version=2,
        display_name="Dư nợ (tỷ)",
        expression="du_no / 1000",
        unit="nghìn tỷ",
        period_semantics="end_of_period",
        status="testing",
        filters=json.dumps([{"dimension": "san_pham", "op": "!=", "values": ["NOI_BO"]}]),
    )
    seed.row(
        "context.outbox",
        org_id=bank.org,
        aggregate_type="metric_version",
        aggregate_id=draft,
        event_type="metric_version.preview_requested",
        payload=json.dumps(
            {
                "metric_id": str(du_no),
                "version_id": str(draft),
                "data_space_id": str(bank.space),
                "requested_by": str(bank.user),
            }
        ),
    )
    await drain_all()
    assert (
        seed.one("SELECT status FROM context.metric_version WHERE id = %s", draft)
        == "pending_review"
    )
    pid, kind, diff, eval_run = seed.one(
        "SELECT id, kind, diff, eval_run_id FROM inbox.proposal WHERE target_ref = %s", draft
    )
    assert kind == "metric_change"
    assert diff["to_expression"] == "du_no / 1000" and diff["from_expression"] == "du_no"
    assert diff["preview"]["in_force"] and diff["preview"]["draft"]
    # The standard set ran with the draft in place: "Dư nợ" now reads 5,5 (8000 incl. August /1000…)
    assert eval_run is not None and diff["impact"]["items"] == 1
    assert seed.one("SELECT proposal_id FROM context.metric_version WHERE id = %s", draft) == pid

    reviewer = seed.row(
        "org.app_user", org_id=bank.org, external_id="kc-r", username="r", display_name="R"
    )
    decide(bank, pid, "accepted", reviewer)
    await drain_all()
    rows = dict(
        seed.all("SELECT version, status FROM context.metric_version WHERE metric_id = %s", du_no)
    )
    assert rows == {1: "superseded", 2: "certified"}
    assert seed.one("SELECT current_version FROM context.metric WHERE id = %s", du_no) == 2
    assert seed.one("SELECT status FROM inbox.proposal WHERE id = %s", pid) == "applied"


async def test_a_draft_that_does_not_compile_is_marked_broken(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    du_no = seed.one(
        "SELECT id FROM context.metric WHERE code = 'DU_NO' AND data_space_id = %s", bank.space
    )
    draft = seed.row(
        "context.metric_version",
        org_id=bank.org,
        metric_id=du_no,
        version=2,
        display_name="Sai",
        expression="khong_co_do_do",
        period_semantics="end_of_period",
        status="testing",
    )
    seed.row(
        "context.outbox",
        org_id=bank.org,
        aggregate_type="metric_version",
        aggregate_id=draft,
        event_type="metric_version.preview_requested",
        payload=json.dumps(
            {
                "metric_id": str(du_no),
                "version_id": str(draft),
                "data_space_id": str(bank.space),
                "requested_by": str(bank.user),
            }
        ),
    )
    await drain_all()
    assert seed.one("SELECT status FROM context.metric_version WHERE id = %s", draft) == "broken"
    assert seed.one("SELECT count(*) FROM inbox.proposal WHERE target_ref = %s", draft) == 0


@pytest.mark.req("AC-07-02-01")
async def test_an_accepted_synonym_is_what_the_agent_understands(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    du_no = seed.one(
        "SELECT id FROM context.metric WHERE code = 'DU_NO' AND data_space_id = %s", bank.space
    )
    synonym = seed.row(
        "context.synonym",
        org_id=bank.org,
        data_space_id=bank.space,
        phrase="tổng cho vay",
        phrase_norm="tong cho vay",
        target_kind="metric",
        target_id=du_no,
        status="pending_review",
        created_by=bank.user,
    )
    seed.row(
        "context.outbox",
        org_id=bank.org,
        aggregate_type="synonym",
        aggregate_id=synonym,
        event_type="synonym.created",
        payload=json.dumps({"synonym_id": str(synonym), "data_space_id": str(bank.space)}),
    )
    await drain_all()
    pid = seed.one(
        "SELECT id FROM inbox.proposal WHERE target_ref = %s AND kind = 'synonym'", synonym
    )
    reviewer = seed.row(
        "org.app_user", org_id=bank.org, external_id="kc-r", username="r", display_name="R"
    )
    decide(bank, pid, "accepted", reviewer)
    await drain_all()
    assert seed.one("SELECT status FROM context.synonym WHERE id = %s", synonym) == "active"
    q = bank.ask("Tổng cho vay tháng 9/2026")
    await drain()
    status, kind, text, *_ = answer_of(seed, q)
    assert (status, kind) == ("answered", "data") and "3.000" in text
