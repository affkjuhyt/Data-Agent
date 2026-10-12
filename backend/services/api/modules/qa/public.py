"""What other api modules may ask of the qa module (ADR-28: through this, never its tables).

Each function opens its own svc_qa transaction: a caller's connection (svc_integration for MCP)
has no grant on schema qa, and modules do not share transactions.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from libs.dataagent_db.models import qa
from services.api.kernel.auth import Principal
from services.api.kernel.db import transaction
from services.api.modules.qa import scope
from services.api.modules.qa.questions import insert_question
from services.api.modules.qa.schemas import AnswerView

ROLE = "svc_qa"


@dataclass(frozen=True)
class RecordedQuestion:
    question_id: uuid.UUID
    conversation_id: uuid.UUID


async def record_question(
    principal: Principal,
    data_space_id: uuid.UUID,
    text: str,
    *,
    channel: str,
    api_client_id: uuid.UUID | None,
    as_of: datetime.date | None = None,
) -> RecordedQuestion:
    """A question in a new conversation of `principal` (for MCP: the client's owner, BR-48),
    answered by worker-agent like any other."""
    async with transaction(ROLE, principal) as session:
        question, conversation = await insert_question(
            session,
            principal,
            data_space_id,
            text=text,
            as_of=as_of,
            channel=channel,
            api_client_id=api_client_id,
        )
        return RecordedQuestion(question.id, conversation.id)


async def answer_of(
    principal: Principal, data_space_id: uuid.UUID, question_id: uuid.UUID
) -> tuple[str, AnswerView | None]:
    """The question's status and, once there is one, its latest answer."""
    async with transaction(ROLE, principal) as session:
        status = await session.scalar(
            scope.questions_in(principal, data_space_id)
            .where(qa.Question.id == question_id)
            .with_only_columns(qa.Question.status)
        )
        if status is None:
            raise LookupError(question_id)
        answer = await session.scalar(
            select(qa.Answer)
            .where(qa.Answer.question_id == question_id)
            .order_by(qa.Answer.created_at.desc(), qa.Answer.id.desc())
            .limit(1)
        )
        return str(status), AnswerView.model_validate(answer) if answer else None
