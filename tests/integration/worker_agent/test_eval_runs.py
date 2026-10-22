"""Real eval execution: 120 items go through the production QA handler."""

from __future__ import annotations

import json
import uuid

import pytest

from services.worker_agent import proposals
from services.worker_agent.eval_runs import data_product_submitted, run_eval
from services.worker_agent.events import Event

from .test_questions import ask

pytestmark = [pytest.mark.integration]


@pytest.mark.req("AC-07-06-01", "AC-07-06-06")
async def test_context_eval_runs_120_items_and_records_three_scores(seed) -> None:
    first = ask(seed, "Câu hỏi đánh giá không có chỉ số đã biết")
    eval_set = seed.row(
        "eval.eval_set",
        org_id=first.org,
        data_space_id=first.space,
        subject_type="context",
        name="Context refusal gate",
        pass_threshold=0.95,
        min_items=100,
        created_by=first.users[0],
    )
    item_ids = []
    for number in range(120):
        item_ids.append(
            seed.row(
                "eval.eval_item",
                org_id=first.org,
                eval_set_id=eval_set,
                category="refusal",
                input=json.dumps({"question": f"Câu hỏi đánh giá {number}"}),
                expected=json.dumps({"kind": "refusal"}),
                created_by=first.users[0],
            )
        )
    run_id = seed.row(
        "eval.eval_run",
        org_id=first.org,
        eval_set_id=eval_set,
        subject_version="context@draft",
        trigger="nightly",
        item_count=120,
        status="running",
    )
    await run_eval(
        Event(
            uuid.uuid4(),
            first.org,
            "eval_run.requested",
            "eval_run",
            run_id,
            {
                "eval_run_id": str(run_id),
                "data_space_id": str(first.space),
                "requested_by": str(first.users[0]),
                "trigger": "nightly",
            },
        )
    )
    count, passed, failed, refusals, rate, status = seed.one(
        "SELECT item_count, passed, failed, correct_refusals, pass_rate, status"
        " FROM eval.eval_run WHERE id = %s",
        run_id,
    )
    result_count, scored = seed.one(
        "SELECT count(*), count(*) FILTER (WHERE understanding_ok AND value_ok AND behavior_ok)"
        " FROM eval.eval_result WHERE eval_run_id = %s",
        run_id,
    )
    assert (count, passed, failed, refusals, float(rate), status) == (
        120,
        120,
        0,
        120,
        1.0,
        "completed",
    )
    assert (result_count, scored) == (120, 120)
    metric = seed.one(
        "SELECT payload FROM eval.outbox WHERE aggregate_id = %s AND event_type = 'eval.metric_recorded'",
        run_id,
    )
    assert metric["metric_name"] == "da_eval_pass_ratio" and metric["gate_passed"] is True


@pytest.mark.req("AC-07-06-05")
async def test_eval_drop_notifies_steward_when_open_space_falls_below_ninety_percent(seed) -> None:
    first = ask(seed, "Câu hỏi đánh giá bị tụt chất lượng")
    with seed.conn.cursor() as cur:
        cur.execute("UPDATE org.data_space SET status = 'active' WHERE id = %s", (first.space,))
    eval_set = seed.row(
        "eval.eval_set",
        org_id=first.org,
        data_space_id=first.space,
        subject_type="context",
        name="Drop gate",
        pass_threshold=0.90,
        min_items=1,
    )
    item = seed.row(
        "eval.eval_item",
        org_id=first.org,
        eval_set_id=eval_set,
        category="correct_result",
        input=json.dumps({"question": "Một chỉ số không tồn tại"}),
        expected=json.dumps({"kind": "data"}),
    )
    baseline = seed.row(
        "eval.eval_run",
        org_id=first.org,
        eval_set_id=eval_set,
        subject_version="context@v1",
        trigger="nightly",
        item_count=1,
        passed=1,
        pass_rate=0.91,
        status="completed",
    )
    seed.row(
        "eval.eval_result",
        org_id=first.org,
        eval_run_id=baseline,
        eval_item_id=item,
        understanding_ok=True,
        value_ok=True,
        behavior_ok=True,
        passed=True,
    )
    run_id = seed.row(
        "eval.eval_run",
        org_id=first.org,
        eval_set_id=eval_set,
        subject_version="context@v2",
        trigger="nightly",
        item_count=1,
        baseline_run_id=baseline,
        status="running",
    )
    await run_eval(
        Event(
            uuid.uuid4(),
            first.org,
            "eval_run.requested",
            "eval_run",
            run_id,
            {
                "eval_run_id": str(run_id),
                "data_space_id": str(first.space),
                "requested_by": str(first.users[0]),
            },
        )
    )
    notified = seed.one(
        "SELECT count(*) FROM notify.notification WHERE source_ref = %s AND user_id = %s",
        run_id,
        first.users[0],
    )
    assert notified == 1


async def test_submitted_data_product_runs_eval_and_opens_proposal(seed) -> None:
    first = ask(seed, "Câu hỏi mẫu của sản phẩm")
    eval_set = seed.row(
        "eval.eval_set",
        org_id=first.org,
        data_space_id=first.space,
        subject_type="context",
        name="Product samples",
        min_items=10,
    )
    for number in range(10):
        seed.row(
            "eval.eval_item",
            org_id=first.org,
            eval_set_id=eval_set,
            category="refusal",
            input=json.dumps({"question": f"Câu hỏi mẫu {number}"}),
            expected=json.dumps({"kind": "refusal"}),
        )
    product = seed.row(
        "context.data_product",
        org_id=first.org,
        data_space_id=first.space,
        code="pilot_product",
        name="Pilot product",
        description="Phạm vi pilot",
        period_grain="month",
        period_from="2026-01-01",
        owner_id=first.users[1],
        steward_id=first.users[0],
        freshness_statement="Cập nhật hằng ngày",
        eval_set_id=eval_set,
        sample_questions=json.dumps([{"text": f"Câu hỏi mẫu {i}"} for i in range(10)]),
        status="draft",
    )
    dataset = seed.row(
        "catalog.dataset",
        org_id=first.org,
        data_space_id=first.space,
        origin="derived",
        schema_name="silver",
        table_name="pilot_data",
        history_kind="snapshot",
        business_date_col="as_of",
        enabled=True,
        quality_state="ok",
    )
    metric = seed.row(
        "context.metric",
        org_id=first.org,
        data_space_id=first.space,
        code="PILOT_METRIC",
        owner_id=first.users[1],
        current_version=1,
        status="active",
    )
    seed.row(
        "context.metric_version",
        org_id=first.org,
        metric_id=metric,
        version=1,
        display_name="Pilot metric",
        expression="sum(value)",
        period_semantics="sum_over_period",
        status="certified",
        certified_by=first.users[0],
        certified_at="2026-01-01T00:00:00+00:00",
        valid_from="2026-01-01T00:00:00+00:00",
    )
    seed.row(
        "context.data_product_member",
        org_id=first.org,
        data_product_id=product,
        member_kind="metric",
        member_ref=metric,
        role="primary",
    )
    seed.row(
        "context.data_product_member",
        org_id=first.org,
        data_product_id=product,
        member_kind="dataset",
        member_ref=dataset,
        role="primary",
        sla_kind="schedule",
        sla_cron="0 0 * * *",
    )
    run_id = uuid.uuid4()
    await data_product_submitted(
        Event(
            uuid.uuid4(),
            first.org,
            "data_product.submitted",
            "data_product",
            product,
            {
                "data_product_id": str(product),
                "data_space_id": str(first.space),
                "eval_set_id": str(eval_set),
                "eval_run_id": str(run_id),
                "requested_by": str(first.users[0]),
                "version": 1,
                "min_items": 10,
                "min_pass_rate": 0.95,
            },
        )
    )
    status = seed.one("SELECT status FROM context.data_product WHERE id = %s", product)
    run_status, rate = seed.one("SELECT status, pass_rate FROM eval.eval_run WHERE id = %s", run_id)
    proposal = seed.one("SELECT id FROM inbox.proposal WHERE eval_run_id = %s", run_id)
    assert status == "pending_review" and run_status == "completed" and float(rate) == 1.0
    assert proposal is not None
    await proposals.decided(
        Event(
            uuid.uuid4(),
            first.org,
            "proposal.decided",
            "proposal",
            proposal,
            {
                "proposal_id": str(proposal),
                "status": "accepted",
                "reviewer_id": str(first.users[1]),
            },
        )
    )
    assert seed.one("SELECT status FROM context.data_product WHERE id = %s", product) == "active"
    assert seed.one("SELECT status FROM inbox.proposal WHERE id = %s", proposal) == "applied"


def _sourced_claim(seed, org, space, version) -> None:
    """BR-161: a version goes to review only with a claim that has a source of its own."""
    link = seed.row(
        "context.knowledge_source_link",
        org_id=org,
        page_version_id=version,
        source_kind="glossary_term",
        source_ref=uuid.uuid4(),
        source_label="Thuật ngữ",
        content_sha256="33" * 32,
        data_space_id=space,
        classification="internal",
    )
    seed.row(
        "context.knowledge_claim",
        org_id=org,
        page_version_id=version,
        anchor="c1",
        statement="Trang dùng để kiểm thử.",
        source_link_id=link,
        valid_from="2026-01-01",
    )


@pytest.mark.req("AC-07-10-02")
async def test_accepted_knowledge_page_version_is_served_and_old_version_is_preserved(seed) -> None:
    first = ask(seed, "Kiểm thử trang tri thức")
    page = seed.row(
        "context.knowledge_page",
        org_id=first.org,
        data_space_id=first.space,
        kind="topic",
        subject_kind="topic",
        slug="kiem-thu-trang-tri-thuc",
        title="Kiểm thử trang tri thức",
        owner_id=first.users[0],
        status="draft",
        policy_state="pending",
    )
    # BR-164: a version is born a draft; review is an update, and its trigger serves the page.
    old = seed.row(
        "context.knowledge_page_version",
        org_id=first.org,
        page_id=page,
        version=1,
        status="draft",
        origin="manual",
        body="Bản cũ",
        body_sha256="11" * 32,
        summary="Bản cũ",
        created_by=first.users[0],
    )
    _sourced_claim(seed, first.org, first.space, old)
    seed.conn.cursor().execute(
        "UPDATE context.knowledge_page_version SET status = 'pending_review' WHERE id = %s",
        (str(old),),
    )
    seed.conn.cursor().execute(
        "UPDATE context.knowledge_page_version SET status = 'reviewed', reviewed_by = %s,"
        " reviewed_at = '2026-10-01T00:00:00+00:00' WHERE id = %s",
        (str(first.users[1]), str(old)),
    )
    proposal_id = uuid.uuid4()
    new = seed.row(
        "context.knowledge_page_version",
        org_id=first.org,
        page_id=page,
        version=2,
        parent_version_id=old,
        status="draft",
        origin="manual",
        body="Bản mới",
        body_sha256="22" * 32,
        summary="Cập nhật",
        proposal_id=proposal_id,
        created_by=first.users[0],
    )
    _sourced_claim(seed, first.org, first.space, new)
    seed.conn.cursor().execute(
        "UPDATE context.knowledge_page_version SET status = 'pending_review' WHERE id = %s",
        (str(new),),
    )
    await proposals.knowledge_page_submitted(
        Event(
            uuid.uuid4(),
            first.org,
            proposals.KNOWLEDGE_SUBMITTED,
            "knowledge_page_version",
            new,
            {
                "page_id": str(page),
                "version_id": str(new),
                "version": 2,
                "data_space_id": str(first.space),
                "proposal_id": str(proposal_id),
                "created_by": str(first.users[0]),
                "origin": "manual",
            },
        )
    )
    assert (
        seed.one("SELECT kind FROM inbox.proposal WHERE id = %s", proposal_id) == "knowledge_page"
    )
    await proposals.decided(
        Event(
            uuid.uuid4(),
            first.org,
            proposals.DECIDED,
            "proposal",
            proposal_id,
            {
                "proposal_id": str(proposal_id),
                "status": "accepted",
                "reviewer_id": str(first.users[1]),
            },
        )
    )
    assert seed.one(
        "SELECT status, current_version_id, policy_state FROM context.knowledge_page WHERE id = %s",
        page,
    ) == ("active", new, "current")
    assert seed.one(
        "SELECT status, reviewed_by FROM context.knowledge_page_version WHERE id = %s", new
    ) == ("reviewed", first.users[1])
    assert (
        seed.one("SELECT status FROM context.knowledge_page_version WHERE id = %s", old)
        == "superseded"
    )
    assert seed.one("SELECT status FROM inbox.proposal WHERE id = %s", proposal_id) == "applied"
