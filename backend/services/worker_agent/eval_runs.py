"""Run evaluation items through the production QA handler (UC-07-06)."""
from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import func, literal, select, update

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import context, notify, org, qa
from libs.dataagent_db.models import eval as evaluation
from services.worker_agent import proposals
from services.worker_agent.events import Event
from services.worker_agent.questions import answer_question

HANDLER = "eval_run.requested"
DATA_PRODUCT_SUBMITTED = "data_product.submitted"
ROLE = "svc_eval"


def _question_text(item: evaluation.EvalItem) -> str:
    value = item.input or {}
    if isinstance(value, str):
        return value
    return str(value.get("question") or value.get("text") or "")


def _values(figures: Any) -> list[float]:
    """The answer's own figures as numbers, in order. A figure's value is stored as text
    (Figure.as_json writes the Decimal with format "f"), so a filter on int/float would drop every
    one; the totals and changes derived from them (source.expr) are not what an item expects."""
    out: list[float] = []
    for figure in figures or []:
        if not isinstance(figure, dict) or "expr" in (figure.get("source") or {}):
            continue
        try:
            out.append(float(figure["value"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _score(item: evaluation.EvalItem, answer: qa.Answer | None, question: qa.Question | None) -> tuple[bool | None, bool | None, bool | None, bool, dict[str, Any], str | None]:
    expected = item.expected if isinstance(item.expected, dict) else {}
    actual = {"kind": answer.kind if answer else None, "status": question.status if question else None, "text": answer.text if answer else None, "figures": answer.figures if answer else []}
    if answer is None or question is None:
        return False, False, False, False, actual, "QA handler did not produce an answer"
    expected_kind = expected.get("kind") or ("refusal" if item.category == "refusal" else None)
    understanding_ok = expected_kind is None or answer.kind == expected_kind
    expected_values = expected.get("values")
    if expected_values is None and "value" in expected:
        expected_values = [expected["value"]]
    if expected_values is None:
        value_ok: bool | None = True
    else:
        actual_values = _values(answer.figures)
        tolerance = float(item.tolerance or expected.get("tolerance") or 0)
        value_ok = len(actual_values) == len(expected_values) and all(abs(a - float(e)) <= tolerance for a, e in zip(actual_values, expected_values, strict=True))
    contains = expected.get("text_contains")
    behavior_ok = contains is None or all(str(part) in answer.text for part in contains)
    passed = bool(understanding_ok and (value_ok is not False) and behavior_ok)
    cause = None if passed else "answer did not satisfy expected understanding, value, or behavior"
    return understanding_ok, value_ok, behavior_ok, passed, actual, cause


async def run_eval(event: Event) -> None:
    run_id = uuid.UUID(str(event.payload["eval_run_id"]))
    user_id = uuid.UUID(str(event.payload["requested_by"]))
    space_id = uuid.UUID(str(event.payload["data_space_id"]))
    async with scoped_session(ROLE, event.org_id, user_id) as session:
        run = await session.scalar(select(evaluation.EvalRun).where(evaluation.EvalRun.id == run_id, evaluation.EvalRun.org_id == event.org_id).with_for_update())
        if run is None or run.status != "running":
            return
        items = list((await session.scalars(select(evaluation.EvalItem).where(evaluation.EvalItem.eval_set_id == run.eval_set_id, evaluation.EvalItem.status == "active").order_by(evaluation.EvalItem.id))).all())
        baseline = {}
        baseline_rate = None
        if run.baseline_run_id:
            baseline_rate = await session.scalar(
                select(evaluation.EvalRun.pass_rate).where(
                    evaluation.EvalRun.id == run.baseline_run_id
                )
            )
            baseline = dict((await session.execute(select(evaluation.EvalResult.eval_item_id, evaluation.EvalResult.passed).where(evaluation.EvalResult.eval_run_id == run.baseline_run_id))).all())
        # A nightly run has no person who asked for it: each item is answered as the one who last
        # wrote it, so a steward of narrower scope cannot have the Data Space steward's rights
        # answer, and record, a question they wrote. A run someone started answers as them.
        nightly = run.trigger == "nightly"
        active_authors: set[uuid.UUID] = set()
        if nightly:
            authors = {a for i in items if (a := i.updated_by or i.created_by) is not None}
            active_authors = set((await session.scalars(select(org.AppUser.id).where(org.AppUser.id.in_(authors), org.AppUser.status == "active"))).all()) if authors else set()
        passed = failed = refusals = regressions = 0
        for item in items:
            asker = (item.updated_by or item.created_by) if nightly else user_id
            understanding_ok: bool | None
            value_ok: bool | None
            behavior_ok: bool | None
            actual: dict[str, Any]
            cause: str | None
            if asker is None or (nightly and asker not in active_authors):
                understanding_ok = value_ok = behavior_ok = item_passed = False
                actual, cause = {}, "the item has no active author to answer it as"
            else:
                question_id, conversation_id = uuid7(), uuid7()
                async with scoped_session("svc_qa", event.org_id, asker) as qa_session:
                    qa_session.add(qa.Conversation(id=conversation_id, org_id=event.org_id, data_space_id=space_id, user_id=asker, channel="api", created_by=asker, updated_by=asker))
                    qa_session.add(qa.Question(id=question_id, org_id=event.org_id, conversation_id=conversation_id, seq=1, text=_question_text(item), as_of=item.as_of, received_at=func.now()))
                await answer_question(Event(uuid7(), event.org_id, "question.received", "question", question_id, {"question_id": str(question_id), "user_id": str(asker), "eval_run_id": str(run_id)}))
                async with scoped_session("svc_qa", event.org_id, asker) as qa_session:
                    question = await qa_session.get(qa.Question, question_id)
                    answer = await qa_session.scalar(select(qa.Answer).where(qa.Answer.question_id == question_id))
                understanding_ok, value_ok, behavior_ok, item_passed, actual, cause = _score(item, answer, question)
                # The api shows this answer only to whom it was answered as (routes._result_view).
                actual["answered_as"] = str(asker)
            changed = item.id in baseline and baseline[item.id] != item_passed
            regressions += int(changed and baseline[item.id] and not item_passed)
            passed += int(item_passed)
            failed += int(not item_passed)
            refusals += int(item_passed and item.category == "refusal")
            session.add(evaluation.EvalResult(id=uuid7(), org_id=event.org_id, eval_run_id=run_id, eval_item_id=item.id, understanding_ok=understanding_ok, value_ok=value_ok, behavior_ok=behavior_ok, passed=item_passed, actual=actual, cause=cause, changed_from_baseline=changed))
        run.item_count, run.passed, run.failed, run.correct_refusals = len(items), passed, failed, refusals
        run.pass_rate = passed / len(items) if items else 0
        run.regressions, run.status, run.finished_at = regressions, "completed", datetime.datetime.now(datetime.UTC)
        nightly_gate = (
            float(run.pass_rate or 0) >= 0.90
            and (baseline_rate is None or float(run.pass_rate or 0) >= float(baseline_rate))
        )
        session.add(
            evaluation.Outbox(
                id=uuid7(),
                org_id=event.org_id,
                aggregate_type="eval_run",
                aggregate_id=run_id,
                event_type="eval.metric_recorded",
                payload={
                    "eval_run_id": str(run_id),
                    "data_space_id": str(space_id),
                    "trigger": event.payload.get("trigger", "manual"),
                    "metric_name": "da_eval_pass_ratio",
                    "metric_value": float(run.pass_rate or 0),
                    "baseline_value": float(baseline_rate) if baseline_rate is not None else None,
                    "gate_passed": nightly_gate,
                },
            )
        )
        space = await session.get(org.DataSpace, space_id)
        if (
            space is not None
            and space.status == "active"
            and baseline_rate is not None
            and float(baseline_rate) >= 0.90
            and float(run.pass_rate or 0) < 0.90
        ):
            recipients = {space.steward_id, space.backup_steward_id} - {None}
            compliance = await session.scalars(
                select(org.DataSpaceMember.principal_id)
                .join(org.Role, org.Role.id == org.DataSpaceMember.role_id)
                .where(
                    org.DataSpaceMember.data_space_id == space_id,
                    org.DataSpaceMember.principal_type == "user",
                    literal("compliance") == func.any(org.Role.capabilities),
                )
            )
            recipients.update(compliance.all())
            async with scoped_session("svc_notify", event.org_id, user_id) as notify_session:
                for recipient in recipients:
                    exists = await notify_session.scalar(
                        select(notify.Notification.id).where(
                            notify.Notification.source_ref == run_id,
                            notify.Notification.user_id == recipient,
                            notify.Notification.kind == "alert",
                        )
                    )
                    if exists is None:
                        notify_session.add(
                            notify.Notification(
                                id=uuid7(),
                                org_id=event.org_id,
                                user_id=recipient,
                                data_space_id=space_id,
                                kind="alert",
                                title="Tỷ lệ eval của Data Space đã giảm",
                                body=f"Pass-rate giảm từ {float(baseline_rate):.1%} xuống {float(run.pass_rate or 0):.1%}.",
                                link=f"/data-spaces/{space_id}/eval-runs/{run_id}",
                                source_ref=run_id,
                                priority="high",
                            )
                        )


async def data_product_submitted(event: Event) -> None:
    """Create and execute the reserved eval run for a submitted Data Product."""
    p = event.payload
    run_id = uuid.UUID(str(p["eval_run_id"]))
    eval_set_id = uuid.UUID(str(p["eval_set_id"]))
    actor = uuid.UUID(str(p["requested_by"]))
    async with scoped_session(ROLE, event.org_id, actor) as session:
        existing = await session.get(evaluation.EvalRun, run_id)
        if existing is None:
            count = await session.scalar(
                select(func.count()).select_from(evaluation.EvalItem).where(
                    evaluation.EvalItem.eval_set_id == eval_set_id,
                    evaluation.EvalItem.status == "active",
                )
            )
            baseline = await session.scalar(
                select(evaluation.EvalRun.id)
                .where(
                    evaluation.EvalRun.eval_set_id == eval_set_id,
                    evaluation.EvalRun.status == "completed",
                )
                .order_by(evaluation.EvalRun.started_at.desc())
                .limit(1)
            )
            session.add(
                evaluation.EvalRun(
                    id=run_id,
                    org_id=event.org_id,
                    eval_set_id=eval_set_id,
                    subject_version=f"data_product@{p.get('version', 'draft')}",
                    trigger="proposal",
                    item_count=int(count or 0),
                    baseline_run_id=baseline,
                    status="running",
                )
            )
    await run_eval(
        Event(
            event_id=event.event_id,
            org_id=event.org_id,
            event_type=HANDLER,
            aggregate_type="eval_run",
            aggregate_id=run_id,
            payload={
                "eval_run_id": str(run_id),
                "data_space_id": str(p["data_space_id"]),
                "requested_by": str(actor),
                "trigger": "proposal",
            },
        )
    )
    async with scoped_session(ROLE, event.org_id, actor) as session:
        run = await session.get(evaluation.EvalRun, run_id)
        product_id = uuid.UUID(str(p["data_product_id"]))
        passed = (
            run is not None
            and run.item_count >= int(p.get("min_items") or 10)
            and float(run.pass_rate or 0) >= float(p.get("min_pass_rate") or 0.95)
        )
    proposal_id = None
    if passed:
        proposal_id = await proposals._open(
            event,
            data_space_id=uuid.UUID(str(p["data_space_id"])),
            kind="data_product",
            target_service="context",
            target_ref=product_id,
            title="Duyệt Data Product sau khi eval đạt",
            diff={"action": "activate", "data_product_id": str(product_id), "version": p.get("version")},
            proposed_by=actor,
            eval_run_id=run_id,
        )
    async with scoped_session("svc_context", event.org_id, actor) as session:
        await session.execute(
            update(context.DataProduct)
            .where(context.DataProduct.id == product_id, context.DataProduct.org_id == event.org_id)
            .values(
                status="pending_review" if passed else "rejected",
                proposal_id=proposal_id,
                updated_by=actor,
                updated_at=func.now(),
            )
        )


HANDLERS = {HANDLER: run_eval, DATA_PRODUCT_SUBMITTED: data_product_submitted}
