"""The semantic review path and the context evaluation run (UC-07-01/02/03/06), end to end on the
V3 database and a PostgreSQL source: an eval run scores real answers, a metric draft opens a
metric_change proposal with its preview and the standard set's run (metric_changes.py owns the
version; test_metric_changes.py covers its impact and breaks), and an accepted proposal is what makes the
agent use the new version or synonym."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from services.worker_agent import eval_runs, metric_changes, proposals, semantic_review
from services.worker_agent.events import OutboxTableSource
from services.worker_agent.main import run_once

from .conftest import Seed
from .test_answers import Bank, answer_of, build, drain, warehouse  # noqa: F401  (fixture)

pytestmark = [pytest.mark.integration]


async def drain_all() -> None:
    for schema, handlers in (
        ("eval", eval_runs.HANDLERS),
        ("context", {**metric_changes.HANDLERS, **semantic_review.HANDLERS}),
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
    # The cause names what missed, not the value: the answer is its asker's alone to read.
    assert causes == ["the answer's value is not what the item expects"]


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
    # Its author, as the api records it: a version no one wrote is never certified (maker-checker,
    # metric_changes._author).
    seed.row(
        "context.outbox",
        org_id=bank.org,
        aggregate_type="metric_version",
        aggregate_id=draft,
        event_type="metric_version.created",
        payload=json.dumps({"version_id": str(draft), "created_by": str(bank.user)}),
        published_at="2026-10-01T00:00:00Z",
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
    assert diff["changes"]["expression"] == {"before": "du_no", "after": "du_no / 1000"}
    assert diff["preview"] and all(
        "new" in month and "current" in month for month in diff["preview"]
    )
    # The standard set ran with the draft in place (semantic_review.evaluate_overlay), under the
    # eval run the proposal names.
    assert eval_run is not None and diff["eval"]["items"] == 1
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


async def test_a_relationship_a_scheduled_scan_inferred_reaches_the_stewards_review(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A scan with nobody behind it (a schedule) leaves created_by empty. The handler used to drop
    # such a relationship, so it stayed pending_review for good and no metric could join on it.
    bank = build(seed, warehouse, monkeypatch)
    loans = seed.one("SELECT id FROM catalog.dataset WHERE id = %s", bank.loans)
    relationship = seed.row(
        "context.relationship",
        org_id=bank.org,
        data_space_id=bank.space,
        from_dataset_id=loans,
        from_columns="{branch_code}",
        to_dataset_id=loans,
        to_columns="{loan_id}",
        cardinality="many_to_one",
        origin="inferred",
        status="pending_review",
    )
    seed.row(
        "context.outbox",
        org_id=bank.org,
        aggregate_type="relationship",
        aggregate_id=relationship,
        event_type="relationship.created",
        payload=json.dumps(
            {"relationship_id": str(relationship), "data_space_id": str(bank.space)}
        ),
    )
    await drain_all()
    # build() makes bank.user the Data Space's steward.
    pid = seed.one(
        "SELECT id FROM inbox.proposal WHERE target_ref = %s AND kind = 'relationship'",
        relationship,
    )
    assert seed.one("SELECT proposed_by FROM inbox.proposal WHERE id = %s", pid) == bank.user
    # Its standard set has run: it waits for a reviewer, which is what the review page decides.
    assert seed.one("SELECT status FROM inbox.proposal WHERE id = %s", pid) == "pending_review"
    table = seed.one("SELECT table_name FROM catalog.dataset WHERE id = %s", loans)
    assert seed.one("SELECT title FROM inbox.proposal WHERE id = %s", pid) == (
        f"Quan hệ {table}(branch_code) → {table}(loan_id)"
    )
    # And it reaches Cần bạn: an approve_proposal request for whoever holds approve, the steward
    # who proposed it excluded.
    assert (
        seed.one(
            "SELECT assignee_capability || ' ' || (context->'excluded_approver_ids'->>0)"
            " FROM inbox.hitl_request WHERE kind = 'approve_proposal' AND source_ref = %s",
            pid,
        )
        == f"approve {bank.user}"
    )


@pytest.mark.parametrize(
    ("decision", "after", "ends"),
    [("accepted", "rejected", "applied"), ("rejected", "active", "rejected")],
)
async def test_retiring_an_active_relationship_is_a_proposal_someone_else_decides(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    decision: str,
    after: str,
    ends: str,
) -> None:
    # POST /v1/relationships/{id}:retire emits relationship.retire_requested; accepted, the join
    # stops being active; rejected, it stays.
    bank = build(seed, warehouse, monkeypatch)
    relationship = seed.row(
        "context.relationship",
        org_id=bank.org,
        data_space_id=bank.space,
        from_dataset_id=bank.loans,
        from_columns="{ship_date_key}",
        to_dataset_id=bank.loans,
        to_columns="{loan_id}",
        cardinality="many_to_one",
        origin="declared",
        status="active",
        created_by=bank.user,
        updated_by=bank.user,
    )
    seed.row(
        "context.outbox",
        org_id=bank.org,
        aggregate_type="relationship",
        aggregate_id=relationship,
        event_type="relationship.retire_requested",
        payload=json.dumps(
            {
                "relationship_id": str(relationship),
                "data_space_id": str(bank.space),
                "requested_by": str(bank.user),
                "reason": "Vai ngày giao hàng",
            }
        ),
    )
    await drain_all()
    with seed.conn.cursor() as cur:
        cur.execute(
            "SELECT id, title, status, diff FROM inbox.proposal WHERE target_ref = %s",
            (str(relationship),),
        )
        pid, title, status, diff = cur.fetchone()
    table = seed.one("SELECT table_name FROM catalog.dataset WHERE id = %s", bank.loans)
    assert title == f"Gỡ quan hệ {table}(ship_date_key) → {table}(loan_id)"
    assert (status, diff["action"], diff["reason"]) == (
        "pending_review",
        "retire",
        "Vai ngày giao hàng",
    )
    assert (
        seed.one(
            "SELECT count(*) FROM inbox.hitl_request WHERE kind = 'approve_proposal' AND source_ref = %s",
            pid,
        )
        == 1
    )
    reviewer = seed.row(
        "org.app_user", org_id=bank.org, external_id="kc-rv", username="rv", display_name="RV"
    )
    decide(bank, pid, decision, reviewer)
    await drain_all()
    assert seed.one("SELECT status FROM context.relationship WHERE id = %s", relationship) == after
    assert seed.one("SELECT status FROM inbox.proposal WHERE id = %s", pid) == ends
