"""Answering a question (UC-01-01): the `question.received` handler.

This is the G1 frame of docs/v3/09-target-deploy-and-cleanup.md: the worker takes the
question the api recorded, writes the plan and the answer to qa.* as svc_qa, and moves the
question through planning → refused/answered, which is what GET /v1/questions/{id}/stream
reports. It does not compute numbers yet: the semantic compiler and the executor of the agent
core still live in the legacy runtime. Until they move here (R1), a question ends in a coded
refusal (libs.contracts.v1.errors) instead of an answer, and never in a number nobody can
trace (invariant 14, BR-171):

  * the Data Space has no active data product: nothing in it carries a commitment, so a
    figure would be out of scope (ADR-42) → INSUFFICIENT_EVIDENCE, ask the steward;
  * otherwise the tool that answers from the data product is not wired yet → TOOL_UNAVAILABLE.

The handler is idempotent: an event delivered twice finds the question past `received`
and does nothing, so the event source may redeliver after a crash.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import func, select

from libs.contracts.v1.errors import ErrorCode, NextAction, Refusal
from libs.contracts.v1.plan import OutputContract, PlanIR
from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import context, qa
from services.worker_agent.events import Event, Handler
from services.worker_agent.progress import publish

logger = logging.getLogger(__name__)

ROLE = "svc_qa"
AGENT_NAME = "Agent Hỏi đáp"


async def answer_question(event: Event) -> None:
    question_id = uuid.UUID(str(event.payload["question_id"]))
    user_id = event.payload.get("user_id")
    await publish(str(question_id), "planning", {"question_id": str(question_id)})
    async with scoped_session(ROLE, event.org_id, user_id) as session:
        question = await session.scalar(
            select(qa.Question).where(qa.Question.id == question_id).with_for_update()
        )
        if question is None or question.status != "received":
            logger.info("question %s: nothing to do (already handled or gone)", question_id)
            return
        space_id = await session.scalar(
            select(qa.Conversation.data_space_id).where(
                qa.Conversation.id == question.conversation_id
            )
        )
        products = await session.scalar(
            select(func.count())
            .select_from(context.DataProduct)
            .where(
                context.DataProduct.data_space_id == space_id,
                context.DataProduct.status == "active",
            )
        )
        if not products:
            refusal = Refusal.of(
                ErrorCode.INSUFFICIENT_EVIDENCE,
                message=(
                    "Data Space này chưa có data product nào được kích hoạt, nên chưa có số liệu nào "
                    "được cam kết để trả lời. Người phụ trách dữ liệu cần đăng ký và duyệt data product trước."
                ),
                next_action=NextAction.CONTACT_STEWARD,
                detail_id=str(question.id),
            )
        else:
            refusal = Refusal.of(
                ErrorCode.TOOL_UNAVAILABLE,
                message=(
                    "Agent chưa trả lời được câu hỏi số liệu trên dữ liệu mới: bộ biên dịch ngữ nghĩa "
                    "chưa được nối vào worker-agent."
                ),
                detail_id=str(question.id),
            )

        plan_id = uuid7()
        version = int(
            await session.scalar(
                select(func.coalesce(func.max(qa.Plan.version), 0) + 1).where(
                    qa.Plan.question_id == question.id
                )
            )
        )
        # A refusal still records how the question was read and the (empty) plan, so the
        # stream's "understood" event and GET /v1/questions/{id} show something to correct.
        plan_ir = PlanIR(plan_id=str(plan_id), output_contract=OutputContract(shape="text"))
        session.add(
            qa.Plan(
                id=plan_id,
                org_id=event.org_id,
                question_id=question.id,
                version=version,
                chips=[{"key": "question", "value": question.text, "origin": "default"}],
                steps=plan_ir.model_dump(mode="json", by_alias=True),
            )
        )
        await session.flush()
        answer_id = uuid7()
        session.add(
            qa.Answer(
                id=answer_id,
                org_id=event.org_id,
                question_id=question.id,
                plan_id=plan_id,
                kind="refusal",
                # qa.answer has no column for the refusal code; kind = refusal plus the
                # message is what A3's Answer carries, the code goes to the log and trace.
                text=refusal.message,
                agent_name=AGENT_NAME,
                product_scope="not_applicable",
            )
        )
        question.status = "refused"
        question.answered_at = func.now()
    logger.info("question %s refused with %s", question_id, refusal.code)
    await publish(
        str(question_id),
        "answer",
        {"question_id": str(question_id), "answer_id": str(answer_id), "kind": "refusal"},
    )
    await publish(
        str(question_id),
        "done",
        {"question_id": str(question_id), "status": "refused", "answer_id": str(answer_id)},
    )


HANDLERS: dict[str, Handler] = {"question.received": answer_question}
