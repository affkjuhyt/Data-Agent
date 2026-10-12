"""Hỏi đáp against PostgreSQL 16: answers and lineage, feedback and its review, exports,
receipts (verify, replay) and the outbox rows each change writes."""

from __future__ import annotations

import uuid

import httpx
import pytest

from .conftest import Seed
from .test_qa_query import (
    World,
    api_for,
    as_,
    ask,
    build,
    events,
    jsonb,
    plan_for,
    query_run,
    sql,
)

pytestmark = [pytest.mark.integration, pytest.mark.contract]


@pytest.fixture
def world(seed: Seed) -> World:
    return build(seed)


async def answered(
    api: httpx.AsyncClient,
    seed: Seed,
    world: World,
    *,
    figures: list[dict[str, object]] | None = None,
    in_scope: bool = False,
) -> tuple[uuid.UUID, uuid.UUID]:
    """A question the worker has answered: (answer id, plan id)."""
    question = await ask(api, world)
    plan = plan_for(seed, world, question["id"])
    product = uuid.uuid4() if in_scope else None
    answer = seed.row(
        "qa.answer",
        org_id=world.org,
        question_id=uuid.UUID(question["id"]),
        plan_id=plan,
        text="Doanh thu 10 tỷ.",
        figures=jsonb(figures or []),
        product_scope="in_scope" if in_scope else "not_applicable",
        data_product_id=product,
    )
    return answer, plan


# --- answers and lineage ------------------------------------------------------------------------


@pytest.mark.req("AC-01-02-01")
async def test_answer_and_lineage_of_a_figure(world: World, seed: Seed) -> None:
    run = query_run(seed, world)
    version = seed.row(
        "context.metric_version",
        org_id=world.org,
        metric_id=seed.row(
            "context.metric",
            org_id=world.org,
            data_space_id=world.space,
            code=f"REV_{uuid.uuid4().hex[:6]}",
            owner_id=world.users["reviewer"],
            current_version=1,
        ),
        version=1,
        display_name="Doanh thu",
        expression="sum(revenue)",
        period_semantics="sum_over_period",
    )
    figures = [
        {"key": "F1", "value": "10", "query_run_id": str(run), "metric_version_id": str(version)},
        {
            "key": "F2",
            "value": "1",
            "source": {"compute_run_id": str(uuid.uuid4()), "expr": "F1-F0"},
        },
    ]
    async with api_for(world) as api:
        answer, plan = await answered(api, seed, world, figures=figures)
        got = await api.get(f"/v1/answers/{answer}", headers=as_(world, "voter1"))
        assert got.status_code == 200, got.text
        assert got.json()["plan_id"] == str(plan) and got.json()["figures"][0]["key"] == "F1"
        hidden = await api.get(
            f"/v1/answers/{answer}", headers=as_(world, "asker", world.other_space)
        )
        assert hidden.status_code == 404

        lineage = await api.get(
            f"/v1/answers/{answer}/lineage", headers=as_(world, "asker"), params={"figure": "F1"}
        )
        assert lineage.status_code == 200, lineage.text
        [figure] = lineage.json()["figures"]
        assert figure["query_run"]["id"] == str(run)
        assert figure["metric_version"]["id"] == str(version)
        assert lineage.json()["plan"]["id"] == str(plan)
        whole = await api.get(f"/v1/answers/{answer}/lineage", headers=as_(world, "asker"))
        assert whole.json()["figures"][1]["compute_run_id"] is not None
        missing = await api.get(
            f"/v1/answers/{answer}/lineage", headers=as_(world, "asker"), params={"figure": "F9"}
        )
        assert missing.status_code == 404
    audits = [p["action"] for e, _, p in events(seed, "qa", answer) if e == "audit.requested"]
    assert audits == ["view_lineage", "view_lineage"]


# --- feedback -----------------------------------------------------------------------------------


@pytest.mark.req("AC-01-05-01", "AC-01-05-02")
async def test_a_wrong_report_is_reviewed_and_confirmed(world: World, seed: Seed) -> None:
    async with api_for(world) as api:
        answer, _ = await answered(api, seed, world)
        reported = await api.post(
            f"/v1/answers/{answer}/feedback",
            headers=as_(world, "asker"),
            json={"rating": "wrong", "error_kind": "wrong_number", "expected_value": "9,8 tỷ"},
        )
        assert reported.status_code == 201, reported.text
        feedback = reported.json()
        assert feedback["status"] == "open" and feedback["user_id"] == str(world.users["asker"])
        [(event, aggregate, payload)] = events(seed, "qa", feedback["id"])
        assert (event, aggregate) == ("feedback.reported", "feedback")
        assert payload["trigger"] == "reported_wrong" and "expected_value" not in payload

        url = f"/v1/feedback/{feedback['id']}"
        asker_cannot = await api.patch(
            url, headers=as_(world, "asker"), json={"status": "confirmed"}
        )
        assert asker_cannot.status_code == 403
        reviewing = await api.patch(
            url, headers=as_(world, "reviewer"), json={"status": "in_review"}
        )
        assert reviewing.status_code == 200, reviewing.text
        confirmed = await api.patch(
            url, headers=as_(world, "reviewer"), json={"status": "confirmed"}
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["reviewer_id"] == str(world.users["reviewer"])
        assert confirmed.json()["decided_at"] is not None
        final = await api.patch(
            url, headers=as_(world, "reviewer"), json={"status": "rejected", "reason": "đổi ý"}
        )
        assert final.status_code == 409 and final.json()["code"] == "invalid_state"
    assert [e for e, _, _ in events(seed, "qa", feedback["id"])] == [
        "feedback.reported",
        "feedback.review_started",
        "feedback.confirmed",
    ]


@pytest.mark.req("AC-01-05-03")
async def test_three_thumbs_down_open_one_review(world: World, seed: Seed) -> None:
    async with api_for(world) as api:
        answer, _ = await answered(api, seed, world)
        ids = []
        for who in ("asker", "voter1", "asker", "voter2", "reviewer"):
            vote = await api.post(
                f"/v1/answers/{answer}/feedback", headers=as_(world, who), json={"rating": "down"}
            )
            assert vote.status_code == 201, vote.text
            ids.append(vote.json()["id"])
    reported = [i for i in ids if events(seed, "qa", i)]
    # The third distinct voter (voter2) opens the review; a repeat or a fourth voter does not.
    assert reported == [ids[3]]
    assert events(seed, "qa", ids[3])[0][2]["trigger"] == "down_votes"


@pytest.mark.req("AC-01-05-06")
def _eval_set(seed: Seed, world: World, space: uuid.UUID) -> str:
    eval_set = uuid.uuid4()
    sql(
        seed,
        "INSERT INTO eval.eval_set (id, org_id, data_space_id, subject_type, name) "
        "VALUES (%s, %s, %s, 'context', 'Bộ câu hỏi chuẩn')",
        eval_set,
        world.org,
        space,
    )
    return str(eval_set)


async def test_only_confirmed_feedback_goes_to_eval_once(world: World, seed: Seed) -> None:
    eval_set = _eval_set(seed, world, world.space)
    elsewhere = _eval_set(seed, world, world.other_space)
    async with api_for(world) as api:
        answer, _ = await answered(api, seed, world)
        created = await api.post(
            f"/v1/answers/{answer}/feedback",
            headers=as_(world, "asker"),
            json={"rating": "wrong", "error_kind": "wrong_period"},
        )
        feedback = created.json()["id"]
        url = f"/v1/feedback/{feedback}:to-eval"
        early = await api.post(url, headers=as_(world, "reviewer"), json={"eval_set_id": eval_set})
        assert early.status_code == 409 and early.json()["code"] == "invalid_state"
        await api.patch(
            f"/v1/feedback/{feedback}", headers=as_(world, "reviewer"), json={"status": "confirmed"}
        )
        # Another Data Space's eval set would receive this Data Space's question and answer.
        crossed = await api.post(
            url, headers=as_(world, "reviewer"), json={"eval_set_id": elsewhere}
        )
        assert crossed.status_code == 422, crossed.text
        assert crossed.json()["code"] == "validation_failed"
        sent = await api.post(url, headers=as_(world, "reviewer"), json={"eval_set_id": eval_set})
        assert sent.status_code == 202, sent.text
        item = sent.json()["id"]
        assert sent.json()["eval_set_id"] == eval_set
        again = await api.post(url, headers=as_(world, "reviewer"), json={"eval_set_id": eval_set})
        assert again.status_code == 409
    [[stored]] = sql(seed, "SELECT eval_item_id FROM qa.feedback WHERE id = %s", feedback)
    assert str(stored) == item
    last = events(seed, "qa", feedback)[-1]
    assert last[0] == "feedback.sent_to_eval" and last[2]["eval_item_id"] == item


# --- exports ------------------------------------------------------------------------------------


@pytest.mark.req("AC-01-06-02", "AC-01-06-03", "AC-01-06-05")
async def test_exports_by_size_and_owner(world: World, seed: Seed) -> None:
    small, large, huge = (query_run(seed, world, row_count=n) for n in (2_000, 15_000, 2_000_000))
    async with api_for(world) as api:
        answers = {}
        for name, run in (("small", small), ("large", large), ("huge", huge)):
            answers[name], _ = await answered(
                api, seed, world, figures=[{"key": "F1", "query_run_id": str(run)}]
            )
        queued = await api.post(
            f"/v1/answers/{answers['small']}/exports",
            headers=as_(world, "asker"),
            json={"format": "xlsx"},
        )
        assert queued.status_code == 202, queued.text
        assert queued.json()["status"] == "queued"
        assert queued.headers["location"] == f"/v1/exports/{queued.json()['id']}"
        [(event, _, payload)] = events(seed, "qa", queued.json()["id"])
        assert event == "export_job.requested" and payload["row_count"] == 2_000

        waiting = await api.post(
            f"/v1/answers/{answers['large']}/exports",
            headers=as_(world, "asker"),
            json={"format": "csv"},
        )
        assert waiting.json()["status"] == "waiting_approval"
        detail = await api.post(
            f"/v1/answers/{answers['small']}/exports",
            headers=as_(world, "asker"),
            json={"format": "csv", "include_detail_rows": True},
        )
        assert detail.json()["status"] == "waiting_approval"

        refused = await api.post(
            f"/v1/answers/{answers['huge']}/exports",
            headers=as_(world, "asker"),
            json={"format": "csv"},
        )
        assert refused.status_code == 422 and refused.json()["code"] == "business_rule_violated"
        assert sql(seed, "SELECT 1 FROM qa.export_job WHERE answer_id = %s", answers["huge"]) == []

        export = queued.json()["id"]
        mine = await api.get(f"/v1/exports/{export}", headers=as_(world, "asker"))
        assert mine.status_code == 200, mine.text
        assert mine.json()["format"] == "xlsx" and "object_key" not in mine.json()
        theirs = await api.get(f"/v1/exports/{export}", headers=as_(world, "voter1"))
        assert theirs.status_code == 404
        sql(
            seed,
            "UPDATE qa.export_job SET status = 'ready', object_key = 'x', expires_at = now() - interval '1 hour'"
            " WHERE id = %s",
            export,
        )
        expired = await api.get(f"/v1/exports/{export}", headers=as_(world, "asker"))
        assert expired.status_code == 410 and expired.json()["code"] == "gone"


# --- receipts and replays -----------------------------------------------------------------------


def receipt(
    seed: Seed, world: World, answer: uuid.UUID, plan: uuid.UUID, *, retain_until: str | None = None
) -> tuple[uuid.UUID, str]:
    [[product]] = sql(seed, "SELECT data_product_id FROM qa.answer WHERE id = %s", answer)
    values: dict[str, object] = {
        "org_id": world.org,
        "answer_id": answer,
        "receipt_no": "BN-00000000-000000",
        "data_product_id": uuid.UUID(str(product)),
        "data_product_code": "GIA_XANG_DAU",
        "data_product_version": 3,
        "plan_id": plan,
        "answered_at": "2026-07-02T09:00:00Z",
        "knowledge_mode": "latest",
        "compiler_version": "semcomp 1.8.2",
        "runtime_version": "runtime 3.1",
    }
    if retain_until:
        values["retain_until"] = retain_until
    receipt_id = seed.row("qa.answer_receipt", **values)
    seed.row(
        "qa.answer_receipt_figure",
        org_id=world.org,
        receipt_id=receipt_id,
        figure_key="F1",
        label="Giá trần E5RON92",
        value="19730",
        unit="đ/lít",
        source_kind="query_run",
        query_run_id=uuid.uuid4(),
        compiled_sql_sha256="9f" * 32,
        freshness_state="fresh",
    )
    sql(seed, "UPDATE qa.answer_receipt SET sealed_at = now() WHERE id = %s", receipt_id)
    [[number]] = sql(seed, "SELECT receipt_no FROM qa.answer_receipt WHERE id = %s", receipt_id)
    return receipt_id, number


@pytest.mark.req("AC-01-07-01", "AC-01-07-03")
async def test_receipts_are_shown_sealed_and_verified(world: World, seed: Seed) -> None:
    async with api_for(world) as api:
        answer, plan = await answered(api, seed, world, in_scope=True)
        receipt_id, number = receipt(seed, world, answer, plan)
        by_answer = await api.get(f"/v1/answers/{answer}/receipt", headers=as_(world, "voter1"))
        assert by_answer.status_code == 200, by_answer.text
        body = by_answer.json()
        assert body["receipt_no"] == number and body["figure_count"] == 1
        assert body["figures"][0]["value"] == "19730" and body["hash_match"] is None

        out_of_scope, _ = await answered(api, seed, world)
        none = await api.get(f"/v1/answers/{out_of_scope}/receipt", headers=as_(world, "asker"))
        assert none.status_code == 404

        verified = await api.get(
            f"/v1/receipts/{number}", headers=as_(world, "asker"), params={"verify": "true"}
        )
        assert verified.status_code == 200, verified.text
        assert verified.json()["hash_match"] is True
        by_id = await api.get(f"/v1/receipts/{receipt_id}", headers=as_(world, "asker"))
        assert by_id.json()["id"] == str(receipt_id)
        elsewhere = await api.get(
            f"/v1/receipts/{number}", headers=as_(world, "asker", world.other_space)
        )
        assert elsewhere.status_code == 404

        # Tampering below the triggers (as the owner, replication role) breaks the seal.
        sql(
            seed,
            "SET session_replication_role = replica;"
            " UPDATE qa.answer_receipt_figure SET value = 19000 WHERE receipt_id = %s;"
            " SET session_replication_role = origin;",
            receipt_id,
        )
        tampered = await api.get(
            f"/v1/receipts/{number}", headers=as_(world, "asker"), params={"verify": "true"}
        )
        assert tampered.status_code == 409 and tampered.json()["code"] == "conflict"
    assert [e for e, _, _ in events(seed, "qa", receipt_id)] == ["qa.receipt.hash_mismatch"]


@pytest.mark.req("AC-01-07-02")
async def test_a_replay_is_recorded_for_the_worker(world: World, seed: Seed) -> None:
    async with api_for(world) as api:
        answer, plan = await answered(api, seed, world, in_scope=True)
        _, number = receipt(seed, world, answer, plan)
        started = await api.post(
            f"/v1/receipts/{number}:replay",
            headers=as_(world, "voter1"),
            json={"mode": "figures", "reason": "Kiểm toán nội bộ"},
        )
        assert started.status_code == 202, started.text
        replay = started.json()["id"]
        assert started.headers["location"] == f"/v1/receipt-replays/{replay}"
        kinds = {e: p for e, _, p in events(seed, "qa", replay)}
        assert kinds["audit.requested"]["action"] == "receipt.replay"
        requested = kinds["receipt_replay.requested"]
        assert requested["knowledge_mode"] == "at" and requested["requested_by"] == str(
            world.users["voter1"]
        )
        assert "reason" not in requested

        got = await api.get(f"/v1/receipt-replays/{replay}", headers=as_(world, "voter1"))
        assert got.status_code == 200, got.text
        assert (
            got.json()["status"] == "running"
            and got.json()["compiler_version_used"] == "semcomp 1.8.2"
        )
        hidden = await api.get(
            f"/v1/receipt-replays/{replay}", headers=as_(world, "voter1", world.other_space)
        )
        assert hidden.status_code == 404

        old_answer, old_plan = await answered(api, seed, world, in_scope=True)
        _, old_number = receipt(seed, world, old_answer, old_plan, retain_until="2020-01-01")
        gone = await api.post(
            f"/v1/receipts/{old_number}:replay", headers=as_(world, "asker"), json={}
        )
        assert gone.status_code == 410 and gone.json()["code"] == "gone"
