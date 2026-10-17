"""Reviews applied in another module (UC-07-13 data products, UC-07-05 source authority, UC-05-05
document validity): the module's event opens a proposal, a person's decision applies it."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from services.worker_agent import evaluation, proposals, reviews
from services.worker_agent.events import OutboxTableSource
from services.worker_agent.main import run_once

from .conftest import Seed
from .test_answers import Bank, build, warehouse  # noqa: F401  (fixture)

pytestmark = [pytest.mark.integration]


async def drain_all() -> None:
    for schema, handlers in (
        ("eval", evaluation.HANDLERS),
        ("context", {reviews.PRODUCT: reviews.product_submitted}),
        ("conflict", {reviews.AUTHORITY: reviews.authority_drafted}),
        ("document", {reviews.VALIDITY: reviews.validity_proposed}),
        ("inbox", {proposals.DECIDED: proposals.decided}),
    ):
        source = OutboxTableSource(schema=schema, event_types=frozenset(handlers))
        while await run_once(source, handlers, batch=50):
            pass


def decide(seed: Seed, org: uuid.UUID, proposal: Any, status: str, reviewer: uuid.UUID) -> None:
    with seed.conn.cursor() as cur:
        cur.execute("UPDATE inbox.proposal SET status = %s WHERE id = %s", (status, str(proposal)))
    seed.row(
        "inbox.outbox",
        org_id=org,
        aggregate_type="proposal",
        aggregate_id=proposal,
        event_type="proposal.decided",
        payload=json.dumps(
            {"proposal_id": str(proposal), "status": status, "reviewer_id": str(reviewer)}
        ),
    )


def person(seed: Seed, org: uuid.UUID, name: str) -> uuid.UUID:
    return seed.row(
        "org.app_user", org_id=org, external_id=f"kc-{name}", username=name, display_name=name
    )


def product(bank: Bank, expected: int) -> uuid.UUID:
    seed = bank.seed
    owner, steward = person(seed, bank.org, "po"), person(seed, bank.org, "ps")
    row = seed.row(
        "context.data_product",
        org_id=bank.org,
        data_space_id=bank.space,
        code="du_no_td",
        name="Dư nợ tín dụng",
        description="Dư nợ",
        period_grain="month",
        period_from="2024-01-01",
        owner_id=owner,
        steward_id=steward,
        freshness_statement="Hằng ngày",
        scope_entities=json.dumps([]),
        sample_questions=json.dumps(
            [{"text": "Dư nợ tháng 9/2026", "expected": expected} for _ in range(10)]
        ),
        status="draft",
        created_by=bank.user,
        updated_by=bank.user,
    )
    du_no = seed.one(
        "SELECT id FROM context.metric WHERE code = 'DU_NO' AND data_space_id = %s", bank.space
    )
    seed.row(
        "context.data_product_member",
        org_id=bank.org,
        data_product_id=row,
        member_kind="metric",
        member_ref=du_no,
    )
    seed.row(
        "context.data_product_member",
        org_id=bank.org,
        data_product_id=row,
        member_kind="dataset",
        member_ref=bank.loans,
        role="primary",
        sla_kind="max_age",
        sla_max_age="P1D",
        freshness_state="fresh",
    )
    seed.row(
        "context.outbox",
        org_id=bank.org,
        aggregate_type="data_product",
        aggregate_id=row,
        event_type="data_product.submitted",
        payload=json.dumps(
            {
                "data_product_id": str(row),
                "data_space_id": str(bank.space),
                "min_pass_rate": 0.95,
                "min_items": 10,
                "assignee_id": str(owner),
            }
        ),
    )
    return row


@pytest.mark.req("AC-07-13-01")
async def test_a_product_at_the_bar_is_proposed_then_activated_by_its_reviewer(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    pid_product = product(bank, 3000)
    await drain_all()
    status, proposal, eval_set = seed.one(
        "SELECT status, proposal_id, eval_set_id FROM context.data_product WHERE id = %s",
        pid_product,
    )
    assert status == "pending_review" and proposal is not None and eval_set is not None
    assert seed.one("SELECT passed FROM eval.eval_run WHERE trigger_ref = %s", pid_product) == 10
    reviewer = person(seed, bank.org, "rv")
    decide(seed, bank.org, proposal, "accepted", reviewer)
    await drain_all()
    status, activated_by = seed.one(
        "SELECT status, activated_by FROM context.data_product WHERE id = %s", pid_product
    )
    assert (status, activated_by) == ("active", reviewer)


async def test_a_product_below_the_bar_stays_a_draft(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    pid_product = product(bank, 1234)
    await drain_all()
    status, proposal = seed.one(
        "SELECT status, proposal_id FROM context.data_product WHERE id = %s", pid_product
    )
    assert (status, proposal) == ("draft", None)
    assert seed.one("SELECT passed FROM eval.eval_run WHERE trigger_ref = %s", pid_product) == 0


@pytest.mark.req("AC-07-05-01")
async def test_an_approved_authority_ranking_supersedes_the_active_one(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    a, b = uuid.uuid4(), uuid.uuid4()
    old, draft = uuid.uuid4(), uuid.uuid4()
    with seed.conn.cursor() as cur:
        cur.execute(
            "INSERT INTO conflict.source_authority (id, org_id, data_space_id, data_kind, ranking,"
            " status, approved_by, approved_at, created_by) VALUES"
            " (%s, %s, %s, 'balance', %s::uuid[], 'active', %s, now(), %s),"
            " (%s, %s, %s, 'balance', %s::uuid[], 'draft', NULL, NULL, %s)",
            (
                str(old),
                str(bank.org),
                str(bank.space),
                [str(a), str(b)],
                str(bank.user),
                str(bank.user),
                str(draft),
                str(bank.org),
                str(bank.space),
                [str(b), str(a)],
                str(bank.user),
            ),
        )
    seed.row(
        "conflict.outbox",
        org_id=bank.org,
        aggregate_type="source_authority",
        aggregate_id=draft,
        event_type="source_authority.drafted",
        payload=json.dumps(
            {
                "source_authority_id": str(draft),
                "data_space_id": str(bank.space),
                "drafted_by": str(bank.user),
                "active_id": str(old),
            }
        ),
    )
    await drain_all()
    proposal = seed.one("SELECT id FROM inbox.proposal WHERE target_ref = %s", draft)
    decide(seed, bank.org, proposal, "accepted", person(seed, bank.org, "rv"))
    await drain_all()
    assert dict(
        seed.all(
            "SELECT id::text, status FROM conflict.source_authority WHERE data_space_id = %s",
            bank.space,
        )
    ) == {str(old): "superseded", str(draft): "active"}


@pytest.mark.req("AC-05-05-01")
async def test_an_accepted_supersession_ends_the_old_document(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    collection = seed.row(
        "document.collection",
        org_id=bank.org,
        data_space_id=bank.space,
        code="vb",
        name="Văn bản",
        purpose="tra cứu",
        qdrant_collection="doc_x",
        embed_model="bge-m3",
    )

    def doc(no: str, start: str) -> uuid.UUID:
        return seed.row(
            "document.document",
            org_id=bank.org,
            collection_id=collection,
            sha256=uuid.uuid4().hex,
            media="pdf",
            file_name=f"{no}.pdf",
            object_key="k",
            size_bytes=1,
            doc_no=no,
            status="ready",
            effective_from=start,
        )

    old, new = doc("10/QĐ-NH", "2025-01-01"), doc("123/QĐ-NH", "2026-04-01")
    seed.row(
        "document.outbox",
        org_id=bank.org,
        aggregate_type="document",
        aggregate_id=new,
        event_type="document.validity_proposed",
        payload=json.dumps(
            {
                "document_id": str(new),
                "data_space_id": str(bank.space),
                "supersedes_id": str(old),
                "effective_from": "2026-04-01",
                "proposed_by": str(bank.user),
                "detected": True,
                "excerpt": "thay thế Quyết định số 10/QĐ-NH",
            }
        ),
    )
    await drain_all()
    proposal, title, diff = seed.one(
        "SELECT id, title, diff FROM inbox.proposal WHERE target_ref = %s", new
    )
    assert "thay thế" in title and diff["excerpt"]
    decide(seed, bank.org, proposal, "accepted", person(seed, bank.org, "rv"))
    await drain_all()
    assert str(seed.one("SELECT supersedes_id FROM document.document WHERE id = %s", new)) == str(
        old
    )
    assert (
        str(seed.one("SELECT effective_to FROM document.document WHERE id = %s", old))
        == "2026-03-31"
    )
