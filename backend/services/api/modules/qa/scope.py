"""Where a qa row lives, and the outbox.

A conversation belongs to one Data Space and every other qa row hangs off it
(question → conversation, answer → question, receipt → answer, …). RLS only
isolates the organisation, so the Data Space of A1.2 is enforced here by joining
up to qa.conversation; a row of another Data Space answers 404 like a missing one.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import qa
from services.api.kernel.auth import Principal


def questions_in(principal: Principal, space: uuid.UUID) -> Select[tuple[qa.Question]]:
    return (
        select(qa.Question)
        .join(qa.Conversation, qa.Conversation.id == qa.Question.conversation_id)
        .where(qa.Question.org_id == principal.org_id, qa.Conversation.data_space_id == space)
    )


def answers_in(principal: Principal, space: uuid.UUID) -> Select[tuple[qa.Answer]]:
    return (
        select(qa.Answer)
        .join(qa.Question, qa.Question.id == qa.Answer.question_id)
        .join(qa.Conversation, qa.Conversation.id == qa.Question.conversation_id)
        .where(qa.Answer.org_id == principal.org_id, qa.Conversation.data_space_id == space)
    )


def _via_answer(stmt: Select[Any], principal: Principal, space: uuid.UUID) -> Select[Any]:
    return (
        stmt.join(qa.Question, qa.Question.id == qa.Answer.question_id)
        .join(qa.Conversation, qa.Conversation.id == qa.Question.conversation_id)
        .where(qa.Answer.org_id == principal.org_id, qa.Conversation.data_space_id == space)
    )


def feedback_in(principal: Principal, space: uuid.UUID) -> Select[tuple[qa.Feedback]]:
    stmt = select(qa.Feedback).join(qa.Answer, qa.Answer.id == qa.Feedback.answer_id)
    return _via_answer(stmt, principal, space)


def exports_in(principal: Principal, space: uuid.UUID) -> Select[tuple[qa.ExportJob]]:
    stmt = select(qa.ExportJob).join(qa.Answer, qa.Answer.id == qa.ExportJob.answer_id)
    return _via_answer(stmt, principal, space)


def receipts_in(principal: Principal, space: uuid.UUID) -> Select[tuple[qa.AnswerReceipt]]:
    stmt = select(qa.AnswerReceipt).join(qa.Answer, qa.Answer.id == qa.AnswerReceipt.answer_id)
    return _via_answer(stmt, principal, space)


def replays_in(principal: Principal, space: uuid.UUID) -> Select[tuple[qa.ReceiptReplay]]:
    stmt = (
        select(qa.ReceiptReplay)
        .join(qa.AnswerReceipt, qa.AnswerReceipt.id == qa.ReceiptReplay.receipt_id)
        .join(qa.Answer, qa.Answer.id == qa.AnswerReceipt.answer_id)
    )
    return _via_answer(stmt, principal, space)


def jsonable(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = json.loads(json.dumps(payload, default=str))
    return out


def emit(
    session: AsyncSession,
    outbox: type[Any],
    principal: Principal,
    *,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """One outbox row in the caller's transaction (D1 rule 8): the event leaves only if the
    change commits. `outbox` is the Outbox model of the schema the change was written to."""
    session.add(
        outbox(
            id=uuid7(),
            org_id=principal.org_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=jsonable(payload),
        )
    )


def audit(
    session: AsyncSession,
    outbox: type[Any],
    principal: Principal,
    *,
    action: str,
    object_kind: str,
    object_id: uuid.UUID,
    after: dict[str, Any] | None = None,
) -> None:
    """Audit is a separate database (D1.1): the row is requested through the outbox, in the shape
    the qa triggers already use for audit.requested."""
    emit(
        session,
        outbox,
        principal,
        aggregate_type=object_kind,
        aggregate_id=object_id,
        event_type="audit.requested",
        payload={
            "action": action,
            "object_kind": object_kind,
            "object_id": object_id,
            "actor_id": principal.user_id,
            "client_id": principal.client_id,
            "before": None,
            "after": after,
        },
    )
