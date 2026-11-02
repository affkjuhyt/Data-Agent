"""Recording a question: what POST /v1/questions does once it knows the conversation, shared with
the public interface (qa.public) so a question asked over MCP is the same row, seq and outbox
event as one asked on the web (UC-12-02: "như một câu hỏi bình thường")."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import qa
from services.api.kernel.auth import Principal
from services.api.modules.qa import scope

TITLE_LENGTH = 80


def title_of(text: str) -> str | None:
    """A new conversation's title: its first question, whitespace folded, cut at a word with an
    ellipsis past TITLE_LENGTH characters. Without it the history lists every conversation as
    untitled; v3_0028 titles the older ones the same way in SQL."""
    folded = " ".join(text.split())
    if len(folded) <= TITLE_LENGTH:
        return folded or None
    head = folded[:TITLE_LENGTH]
    cut = head.rsplit(" ", 1)[0] if " " in head else head[: TITLE_LENGTH - 1]
    return f"{cut[: TITLE_LENGTH - 1].rstrip()}…"


async def insert_question(
    session: AsyncSession,
    principal: Principal,
    data_space: uuid.UUID,
    *,
    text: str,
    conversation: qa.Conversation | None = None,
    parent_question_id: uuid.UUID | None = None,
    as_of: datetime.date | None = None,
    knowledge_mode: str = "latest",
    knowledge_at: datetime.datetime | None = None,
    purpose: str | None = None,
    channel: str = "web",
    api_client_id: uuid.UUID | None = None,
) -> tuple[qa.Question, qa.Conversation]:
    """Insert the question (in a new conversation when none is given, locked by the caller when
    it is) and its `question.received` event, in the caller's transaction."""
    if conversation is None:
        conversation = qa.Conversation(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=data_space,
            user_id=principal.user_id,
            channel=channel,
            api_client_id=api_client_id,
            title=title_of(text),
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(conversation)
        await session.flush()
    seq = int(
        await session.scalar(
            select(func.coalesce(func.max(qa.Question.seq), 0) + 1).where(
                qa.Question.conversation_id == conversation.id
            )
        )
        or 1
    )
    # knowledge_mode = at without knowledge_at is refused by the table's CHECK (422
    # business_rule_violated, A3).
    question = qa.Question(
        id=uuid7(),
        org_id=principal.org_id,
        conversation_id=conversation.id,
        seq=seq,
        text=text,
        parent_question_id=parent_question_id,
        as_of=as_of,
        knowledge_mode=knowledge_mode,
        knowledge_at=knowledge_at,
        purpose=purpose,
        status="received",
    )
    session.add(question)
    conversation.last_question_at = func.now()
    conversation.updated_at = func.now()
    conversation.updated_by = principal.user_id
    await session.flush()
    scope.emit(
        session,
        qa.Outbox,
        principal,
        aggregate_type="question",
        aggregate_id=question.id,
        event_type="question.received",
        payload={
            "question_id": question.id,
            "conversation_id": conversation.id,
            "data_space_id": data_space,
            "parent_question_id": parent_question_id,
            "user_id": principal.user_id,
            "status": "received",
        },
    )
    return question, conversation
