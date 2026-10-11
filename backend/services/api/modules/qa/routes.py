"""API-02 question, conversation, answer and feedback routes."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy import func, select, update

from libs.dataagent_db.models import qa
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import Page, PageRequest, encode_page_token, invalid_page_token, page_request
from services.api.kernel.problems import Problem
from services.api.kernel.sse import Event, event_stream, last_event_id as get_last_event_id
from services.api.modules.qa.schemas import (
    AnswerView,
    ConversationSummary,
    ExportCreate,
    ExportView,
    FeedbackPatch,
    FeedbackCreate,
    FeedbackView,
    LineageView,
    PlanPatch,
    PlanSummary,
    QuestionAccepted,
    QuestionCreate,
    QuestionStatus,
    ReceiptView,
    ReplayCreate,
    ReplayView,
)

router = APIRouter()
ROLE = "svc_qa"


def _plan(plan: qa.Plan | None) -> PlanSummary | None:
    if plan is None:
        return None
    return PlanSummary(
        id=plan.id,
        version=plan.version,
        chips=plan.chips,
        steps=plan.steps,
        confidence=float(plan.confidence) if plan.confidence is not None else None,
        edited_by_user=plan.edited_by_user,
        edit_note=plan.edit_note,
    )


async def _question_status(session, question_id: uuid.UUID, org_id: uuid.UUID) -> QuestionStatus:
    question = await session.scalar(
        select(qa.Question).where(qa.Question.id == question_id, qa.Question.org_id == org_id)
    )
    if question is None:
        raise Problem(404, "not_found", "Question not found.")
    plan = await session.scalar(
        select(qa.Plan)
        .where(qa.Plan.question_id == question.id)
        .order_by(qa.Plan.version.desc())
        .limit(1)
    )
    answer_id = await session.scalar(
        select(qa.Answer.id).where(qa.Answer.question_id == question.id).order_by(qa.Answer.created_at.desc()).limit(1)
    )
    return QuestionStatus(
        id=question.id,
        conversation_id=question.conversation_id,
        seq=question.seq,
        text=question.text,
        status=question.status,
        knowledge_mode=question.knowledge_mode,
        knowledge_at=question.knowledge_at,
        plan=_plan(plan),
        answer_id=answer_id,
    )


@router.post("/v1/questions", status_code=202, response_model=QuestionAccepted, **endpoint("POST", "/v1/questions", operation_id="createQuestion"))
async def create_question(
    body: QuestionCreate,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> QuestionAccepted:
    async with transaction(ROLE, principal) as session:
        if body.conversation_id:
            conversation = await session.scalar(
                select(qa.Conversation).where(
                    qa.Conversation.id == body.conversation_id,
                    qa.Conversation.org_id == principal.org_id,
                    qa.Conversation.user_id == principal.user_id,
                    qa.Conversation.data_space_id == data_space,
                    qa.Conversation.deleted_at.is_(None),
                )
            )
            if conversation is None:
                raise Problem(404, "not_found", "Conversation not found.")
        else:
            conversation = qa.Conversation(
                org_id=principal.org_id,
                data_space_id=data_space,
                user_id=principal.user_id,
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
        )
        question = qa.Question(
            org_id=principal.org_id,
            conversation_id=conversation.id,
            seq=seq,
            text=body.text,
            as_of=body.as_of,
            knowledge_mode=body.knowledge_mode,
            knowledge_at=body.knowledge_at,
            purpose=body.purpose,
        )
        session.add(question)
        await session.execute(
            update(qa.Conversation)
            .where(qa.Conversation.id == conversation.id)
            .values(last_question_at=func.now(), updated_at=func.now(), updated_by=principal.user_id)
        )
        await session.flush()
        return QuestionAccepted(question_id=question.id, status=question.status, poll_after_ms=250)


@router.get("/v1/questions/{id}", response_model=QuestionStatus, **endpoint("GET", "/v1/questions/{id}", operation_id="getQuestion"))
async def get_question(question_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> QuestionStatus:
    async with transaction(ROLE, principal) as session:
        return await _question_status(session, question_id, principal.org_id)


@router.get("/v1/questions/{id}/stream", **endpoint("GET", "/v1/questions/{id}/stream", operation_id="streamQuestion"))
async def stream_question(
    question_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    last_id: Annotated[str | None, Depends(get_last_event_id)],
):
    async with transaction(ROLE, principal) as session:
        status = await _question_status(session, question_id, principal.org_id)
    events: list[Event] = [Event("1", "question.status", status.model_dump(mode="json"))]
    if status.answer_id:
        events.append(Event("2", "answer.ready", {"answer_id": str(status.answer_id)}))
    if status.status in {"answered", "refused", "failed"}:
        events.append(Event("3", "question.complete", {"status": status.status, "answer_id": status.answer_id}))
    if last_id:
        try:
            events = [event for event in events if int(event.id) > int(last_id)]
        except ValueError as exc:
            raise Problem(400, "invalid_request", "Last-Event-ID must be numeric.") from exc

    async def produce() -> AsyncIterator[Event]:
        for event in events:
            yield event

    return event_stream(produce())


@router.patch("/v1/questions/{id}/plan", response_model=QuestionStatus, **endpoint("PATCH", "/v1/questions/{id}/plan", operation_id="editQuestionPlan"))
async def edit_question_plan(
    body: PlanPatch,
    question_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
) -> QuestionStatus:
    async with transaction(ROLE, principal) as session:
        question = await session.scalar(
            select(qa.Question).where(qa.Question.id == question_id, qa.Question.org_id == principal.org_id)
        )
        if question is None:
            raise Problem(404, "not_found", "Question not found.")
        current = await session.scalar(
            select(qa.Plan).where(qa.Plan.question_id == question.id).order_by(qa.Plan.version.desc()).limit(1)
        )
        if current is None:
            raise Problem(409, "invalid_state", "Question has no plan to edit.")
        session.add(
            qa.Plan(
                org_id=principal.org_id,
                question_id=question.id,
                version=current.version + 1,
                chips=body.chips,
                steps=current.steps,
                confidence=current.confidence,
                edited_by_user=True,
                edit_note=body.edit_note,
            )
        )
        await session.flush()
        return await _question_status(session, question_id, principal.org_id)


@router.get("/v1/conversations", response_model=Page[ConversationSummary], **endpoint("GET", "/v1/conversations", operation_id="listConversations"))
async def list_conversations(
    principal: CurrentPrincipal,
    page: Annotated[PageRequest, Depends(page_request)],
    data_space: CurrentDataSpace,
) -> Page[ConversationSummary]:
    query = select(qa.Conversation).where(
        qa.Conversation.org_id == principal.org_id,
        qa.Conversation.user_id == principal.user_id,
        qa.Conversation.data_space_id == data_space,
        qa.Conversation.deleted_at.is_(None),
    ).order_by(qa.Conversation.updated_at.desc(), qa.Conversation.id.desc()).limit(page.page_size + 1)
    if page.cursor:
        try:
            query = query.where(qa.Conversation.updated_at < page.cursor["updated_at"])
        except KeyError as exc:
            raise invalid_page_token() from exc
    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(query)).all())
    more = len(rows) > page.page_size
    rows = rows[: page.page_size]
    last = rows[-1] if more else None
    return Page(
        items=[ConversationSummary.model_validate(row, from_attributes=True) for row in rows],
        next_page_token=encode_page_token({"updated_at": last.updated_at.isoformat()} if last else None),
    )


@router.get("/v1/answers/{id}", response_model=AnswerView, **endpoint("GET", "/v1/answers/{id}", operation_id="getAnswer"))
async def get_answer(answer_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> AnswerView:
    async with transaction(ROLE, principal) as session:
        answer = await session.scalar(select(qa.Answer).where(qa.Answer.id == answer_id, qa.Answer.org_id == principal.org_id))
    if answer is None:
        raise Problem(404, "not_found", "Answer not found.")
    return AnswerView.model_validate(answer, from_attributes=True)


@router.get("/v1/answers/{id}/lineage", response_model=LineageView, **endpoint("GET", "/v1/answers/{id}/lineage", operation_id="getAnswerLineage"))
async def get_answer_lineage(answer_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> LineageView:
    async with transaction(ROLE, principal) as session:
        answer = await session.scalar(select(qa.Answer).where(qa.Answer.id == answer_id, qa.Answer.org_id == principal.org_id))
    if answer is None:
        raise Problem(404, "not_found", "Answer not found.")
    return LineageView(answer_id=answer.id, lineage_hash=answer.lineage_hash, figures=answer.figures)


@router.post("/v1/answers/{id}/feedback", response_model=FeedbackView, **endpoint("POST", "/v1/answers/{id}/feedback", operation_id="createAnswerFeedback"))
async def create_feedback(
    body: FeedbackCreate,
    answer_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
) -> FeedbackView:
    async with transaction(ROLE, principal) as session:
        exists = await session.scalar(select(qa.Answer.id).where(qa.Answer.id == answer_id, qa.Answer.org_id == principal.org_id))
        if exists is None:
            raise Problem(404, "not_found", "Answer not found.")
        feedback = qa.Feedback(
            org_id=principal.org_id,
            answer_id=answer_id,
            user_id=principal.user_id,
            created_by=principal.user_id,
            updated_by=principal.user_id,
            **body.model_dump(),
        )
        session.add(feedback)
        await session.flush()
    return FeedbackView.model_validate(feedback, from_attributes=True)


@router.get("/v1/answers/{id}/receipt", response_model=ReceiptView, **endpoint("GET", "/v1/answers/{id}/receipt", operation_id="getAnswerReceipt"))
async def get_answer_receipt(answer_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> ReceiptView:
    async with transaction(ROLE, principal) as session:
        receipt = await session.scalar(select(qa.AnswerReceipt).where(qa.AnswerReceipt.answer_id == answer_id, qa.AnswerReceipt.org_id == principal.org_id))
    if receipt is None:
        raise Problem(404, "not_found", "Receipt not found.")
    return ReceiptView.model_validate(receipt, from_attributes=True)


@router.patch("/v1/feedback/{id}", response_model=FeedbackView, **endpoint("PATCH", "/v1/feedback/{id}", operation_id="reviewFeedback"))
async def review_feedback(body: FeedbackPatch, feedback_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> FeedbackView:
    async with transaction(ROLE, principal) as session:
        feedback = await session.scalar(select(qa.Feedback).where(qa.Feedback.id == feedback_id, qa.Feedback.org_id == principal.org_id))
        if feedback is None:
            raise Problem(404, "not_found", "Feedback not found.")
        feedback.status = body.status
        feedback.reason = body.reason
        feedback.reviewer_id = principal.user_id
        feedback.decided_at = func.now()
        feedback.updated_by = principal.user_id
        await session.flush()
    return FeedbackView.model_validate(feedback, from_attributes=True)


@router.post("/v1/feedback/{id}:to-eval", response_model=FeedbackView, **endpoint("POST", "/v1/feedback/{id}:to-eval", operation_id="sendFeedbackToEval"))
async def send_feedback_to_eval(feedback_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> FeedbackView:
    async with transaction(ROLE, principal) as session:
        feedback = await session.scalar(select(qa.Feedback).where(qa.Feedback.id == feedback_id, qa.Feedback.org_id == principal.org_id))
        if feedback is None:
            raise Problem(404, "not_found", "Feedback not found.")
        feedback.status = "in_review"
        feedback.updated_by = principal.user_id
        await session.flush()
    return FeedbackView.model_validate(feedback, from_attributes=True)


@router.post("/v1/answers/{id}/exports", response_model=ExportView, status_code=202, **endpoint("POST", "/v1/answers/{id}/exports", operation_id="createAnswerExport"))
async def create_answer_export(body: ExportCreate, answer_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> ExportView:
    async with transaction(ROLE, principal) as session:
        exists = await session.scalar(select(qa.Answer.id).where(qa.Answer.id == answer_id, qa.Answer.org_id == principal.org_id))
        if exists is None:
            raise Problem(404, "not_found", "Answer not found.")
        export = qa.ExportJob(org_id=principal.org_id, answer_id=answer_id, requested_by=principal.user_id, **body.model_dump())
        session.add(export)
        await session.flush()
    return ExportView.model_validate(export, from_attributes=True)


@router.get("/v1/exports/{id}", response_model=ExportView, **endpoint("GET", "/v1/exports/{id}", operation_id="getExport"))
async def get_export(export_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> ExportView:
    async with transaction(ROLE, principal) as session:
        export = await session.scalar(select(qa.ExportJob).where(qa.ExportJob.id == export_id, qa.ExportJob.org_id == principal.org_id))
    if export is None:
        raise Problem(404, "not_found", "Export not found.")
    return ExportView.model_validate(export, from_attributes=True)


@router.get("/v1/receipts/{receipt_no}", response_model=ReceiptView, **endpoint("GET", "/v1/receipts/{receipt_no}", operation_id="getReceipt"))
async def get_receipt(receipt_no: str, principal: CurrentPrincipal) -> ReceiptView:
    async with transaction(ROLE, principal) as session:
        receipt = await session.scalar(select(qa.AnswerReceipt).where(qa.AnswerReceipt.receipt_no == receipt_no, qa.AnswerReceipt.org_id == principal.org_id))
    if receipt is None:
        raise Problem(404, "not_found", "Receipt not found.")
    return ReceiptView.model_validate(receipt, from_attributes=True)


@router.post("/v1/receipts/{id}:replay", response_model=ReplayView, status_code=202, **endpoint("POST", "/v1/receipts/{id}:replay", operation_id="createReceiptReplay"))
async def create_receipt_replay(body: ReplayCreate, receipt_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> ReplayView:
    async with transaction(ROLE, principal) as session:
        receipt = await session.scalar(select(qa.AnswerReceipt).where(qa.AnswerReceipt.id == receipt_id, qa.AnswerReceipt.org_id == principal.org_id))
        if receipt is None:
            raise Problem(404, "not_found", "Receipt not found.")
        replay = qa.ReceiptReplay(org_id=principal.org_id, receipt_id=receipt_id, requested_by=principal.user_id, compiler_version_used=receipt.compiler_version, **body.model_dump())
        session.add(replay)
        await session.flush()
    return ReplayView.model_validate(replay, from_attributes=True)


@router.get("/v1/receipt-replays/{id}", response_model=ReplayView, **endpoint("GET", "/v1/receipt-replays/{id}", operation_id="getReceiptReplay"))
async def get_receipt_replay(replay_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> ReplayView:
    async with transaction(ROLE, principal) as session:
        replay = await session.scalar(select(qa.ReceiptReplay).where(qa.ReceiptReplay.id == replay_id, qa.ReceiptReplay.org_id == principal.org_id))
    if replay is None:
        raise Problem(404, "not_found", "Receipt replay not found.")
    return ReplayView.model_validate(replay, from_attributes=True)
