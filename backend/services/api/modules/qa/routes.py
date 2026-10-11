"""A2 group Hỏi đáp (schema qa): questions, plans, conversations, answers, feedback,
exports, receipts and their replays (UC-01-01 … UC-01-07).

The api records requests; it runs nothing. Answering a question, re-running an
edited plan, building an export file and replaying a receipt are worker jobs:
the route writes the row and the outbox event the worker consumes in one
transaction, and answers 202 with a Location to poll (A1.6).
"""

from __future__ import annotations

import datetime
import json
import re
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy import func, select, text, tuple_

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import context, qa
from libs.dataagent_db.models import query as query_models
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import (
    Page,
    PageRequest,
    encode_page_token,
    invalid_page_token,
    page_request,
)
from services.api.kernel.problems import FieldError, Problem
from services.api.kernel.sse import Event, event_stream
from services.api.kernel.sse import last_event_id as get_last_event_id
from services.api.modules.eval.public import eval_set_accepts_items
from services.api.modules.qa import progress
from services.api.modules.qa import scope
from services.api.modules.qa.schemas import (
    AnswerView,
    ConversationView,
    EvalItemAccepted,
    ExportCreate,
    ExportView,
    FeedbackCreate,
    FeedbackPatch,
    FeedbackToEval,
    FeedbackView,
    JobAccepted,
    LineageFigure,
    LineageView,
    MetricVersionRef,
    PlanAccepted,
    PlanPatch,
    PlanView,
    QueryRunRef,
    QuestionAccepted,
    QuestionCreate,
    QuestionView,
    ReceiptFigureView,
    ReceiptView,
    ReplayCreate,
    ReplayView,
)

router = APIRouter()
ROLE = "svc_qa"
QUERY_ROLE = "svc_query"

IdPath = Annotated[uuid.UUID, Path(alias="id")]

# UC-01-03: a plan is edited once the question was understood and nothing runs on it.
PLAN_EDITABLE = frozenset({"clarifying", "answered", "refused", "failed"})
QUESTION_DONE = frozenset({"answered", "refused", "failed"})
# UC-01-05: open → in_review → confirmed | rejected; a decision is final.
FEEDBACK_NEXT: dict[str, frozenset[str]] = {
    "open": frozenset({"in_review", "confirmed", "rejected"}),
    "in_review": frozenset({"confirmed", "rejected"}),
}
FEEDBACK_EVENTS = {
    "in_review": "feedback.review_started",
    "confirmed": "feedback.confirmed",
    "rejected": "feedback.rejected",
}
# UC-01-05 1a: three thumbs-down from different people in 7 days open a review.
DOWN_VOTES_FOR_REVIEW = 3
DOWN_VOTE_WINDOW = datetime.timedelta(days=7)
# UC-01-06: above 10,000 rows an export waits for approval; above 1,000,000 it is refused (E1).
EXPORT_APPROVAL_ROWS = 10_000
EXPORT_MAX_ROWS = 1_000_000

# The SHA-256 the seal trigger takes (routines/qa.sql, qa.answer_receipt_guard), recomputed over
# the stored rows: same object, same key order, same text.
# The same digest the seal trigger stores (routines/qa.sql), computed with TimeZone pinned to UTC.
RECEIPT_HASH_SQL = text("SELECT qa.receipt_digest(r) FROM qa.answer_receipt r WHERE r.id = :id")


def _invalid(field: str, message: str) -> Problem:
    return Problem(
        422,
        "validation_failed",
        message,
        errors=[FieldError(field=field, code="invalid", message=message)],
    )


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


# ─── questions ───


async def _current_plan(session: Any, question_id: uuid.UUID) -> qa.Plan | None:
    plan: qa.Plan | None = await session.scalar(
        select(qa.Plan)
        .where(qa.Plan.question_id == question_id)
        .order_by(qa.Plan.version.desc())
        .limit(1)
    )
    return plan


async def _question_view(session: Any, question: qa.Question) -> QuestionView:
    plan = await _current_plan(session, question.id)
    answer_id = await session.scalar(
        select(qa.Answer.id)
        .where(qa.Answer.question_id == question.id)
        .order_by(qa.Answer.created_at.desc(), qa.Answer.id.desc())
        .limit(1)
    )
    view = QuestionView.model_validate(question)
    view.plan = PlanView.model_validate(plan) if plan else None
    view.answer_id = answer_id
    return view


async def _question(
    session: Any, question_id: uuid.UUID, principal: Principal, space: uuid.UUID
) -> qa.Question:
    question: qa.Question | None = await session.scalar(
        scope.questions_in(principal, space).where(qa.Question.id == question_id)
    )
    if question is None:
        raise Problem(404, "not_found", "Question not found.")
    return question


@router.post(
    "/v1/questions",
    status_code=202,
    response_model=QuestionAccepted,
    **endpoint("POST", "/v1/questions", operation_id="createQuestion"),
)
async def create_question(
    body: QuestionCreate,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
) -> QuestionAccepted:
    async with transaction(ROLE, principal) as session:
        conversation_id = body.conversation_id
        if body.parent_question_id is not None:
            # UC-01-04: a follow-up inherits from a question of one of the caller's conversations.
            parent = await session.scalar(
                scope.questions_in(principal, data_space).where(
                    qa.Question.id == body.parent_question_id,
                    qa.Conversation.user_id == principal.user_id,
                    qa.Conversation.deleted_at.is_(None),
                )
            )
            if parent is None:
                raise _invalid("parent_question_id", "No such question in this Data Space.")
            if conversation_id is not None and parent.conversation_id != conversation_id:
                raise _invalid(
                    "parent_question_id", "The parent question belongs to another conversation."
                )
            conversation_id = parent.conversation_id
        if conversation_id is not None:
            # FOR UPDATE: two questions sent at once into one conversation get distinct seq.
            conversation = await session.scalar(
                select(qa.Conversation)
                .where(
                    qa.Conversation.id == conversation_id,
                    qa.Conversation.org_id == principal.org_id,
                    qa.Conversation.user_id == principal.user_id,
                    qa.Conversation.data_space_id == data_space,
                    qa.Conversation.deleted_at.is_(None),
                )
                .with_for_update()
            )
            if conversation is None:
                raise _invalid("conversation_id", "No such conversation in this Data Space.")
        else:
            conversation = qa.Conversation(
                id=uuid7(),
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
        # knowledge_mode = at without knowledge_at is refused by the table's CHECK (422
        # business_rule_violated, A3).
        question = qa.Question(
            id=uuid7(),
            org_id=principal.org_id,
            conversation_id=conversation.id,
            seq=seq,
            text=body.text,
            parent_question_id=body.parent_question_id,
            as_of=body.as_of,
            knowledge_mode=body.knowledge_mode,
            knowledge_at=body.knowledge_at,
            purpose=body.purpose,
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
                "parent_question_id": body.parent_question_id,
                "user_id": principal.user_id,
                "status": "received",
            },
        )
    response.headers["Location"] = f"/v1/questions/{question.id}"
    return QuestionAccepted(
        id=question.id,
        status="received",
        poll_after_ms=250,
        question_id=question.id,
        conversation_id=conversation.id,
        stream_url=f"/v1/questions/{question.id}/stream" if body.stream else None,
    )


@router.get(
    "/v1/questions/{id}",
    response_model=QuestionView,
    **endpoint("GET", "/v1/questions/{id}", operation_id="getQuestion"),
)
async def get_question(
    question_id: IdPath, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> QuestionView:
    async with transaction(ROLE, principal) as session:
        question = await _question(session, question_id, principal, data_space)
        return await _question_view(session, question)


def _stream_events(view: QuestionView, answer_kind: str | None) -> list[Event]:
    """The question's state as A1.6 events. Ids are plan_version * 10 + step, so they grow over
    the question's life (a plan edit starts a new decade) and Last-Event-ID resumes after the
    last one a client saw."""
    version = view.plan.version if view.plan else 0

    def event(step: int, name: str, data: dict[str, Any]) -> Event:
        return Event(str(version * 10 + step), name, scope.jsonable(data))

    events: list[Event] = []
    if view.plan is not None:
        events.append(event(1, "understood", {"plan": view.plan.model_dump()}))
    if view.status in ("received", "planning"):
        events.append(event(2, "progress", {"question_id": view.id, "status": view.status}))
    if view.status == "running":
        events.append(event(3, "progress", {"question_id": view.id, "status": view.status}))
    if view.status == "clarifying":
        events.append(
            event(4, "clarification", {"question_id": view.id, "answer_id": view.answer_id})
        )
    if view.status in ("answered", "refused") and view.answer_id is not None:
        events.append(event(5, "answer", {"answer_id": view.answer_id, "kind": answer_kind}))
    if view.status == "failed":
        events.append(event(6, "error", {"question_id": view.id, "status": view.status}))
    if view.status in QUESTION_DONE:
        events.append(
            event(
                7,
                "done",
                {"question_id": view.id, "status": view.status, "answer_id": view.answer_id},
            )
        )
    return events


@router.get(
    "/v1/questions/{id}/stream",
    **endpoint("GET", "/v1/questions/{id}/stream", operation_id="streamQuestion"),
)
async def stream_question(
    question_id: IdPath,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    last_id: Annotated[str | None, Depends(get_last_event_id)],
) -> Response:
    redis_last_id = last_id if last_id and re.fullmatch(r"\d+-\d+", last_id) else None
    if last_id and redis_last_id is None and not last_id.isdigit():
        raise Problem(400, "invalid_request", "Last-Event-ID is not an id this stream sent.")
    try:
        after = int(last_id) if last_id and redis_last_id is None else -1
    except ValueError as exc:
        raise Problem(
            400, "invalid_request", "Last-Event-ID is not an id this stream sent."
        ) from exc
    async with transaction(ROLE, principal) as session:
        question = await _question(session, question_id, principal, data_space)
        view = await _question_view(session, question)
        kind = (
            await session.scalar(select(qa.Answer.kind).where(qa.Answer.id == view.answer_id))
            if view.answer_id
            else None
        )
    # Redis is the live/replay path from worker-agent. The database snapshot remains the
    # fallback for a cold stream, Redis outage, or an older numeric Last-Event-ID.
    events = await progress.read(str(question_id), redis_last_id)
    if events and redis_last_id is None:
        # The plan is persisted in Postgres before worker progress is published. Keep
        # the A1.6 understood frame in the initial response; subsequent frames use the
        # Redis IDs so EventSource can resume directly from the worker stream.
        events = [e for e in _stream_events(view, kind) if e.event == "understood"] + events
    elif not events:
        events = [e for e in _stream_events(view, kind) if int(e.id) > after]

    async def produce() -> AsyncIterator[Event]:
        for item in events:
            yield item

    return event_stream(produce())


def _stamp_chips(
    chips: list[dict[str, Any]], previous: Any, editor: uuid.UUID
) -> list[dict[str, Any]]:
    """A3: every chip the user changed records that the user set it; unchanged chips keep
    their origin (default, own interpretation, earlier edit)."""

    def key(chip: Any) -> str:
        return json.dumps(chip, sort_keys=True, default=str)

    before = {key(c) for c in previous} if isinstance(previous, list) else set()
    return [
        chip if key(chip) in before else {**chip, "origin": "user", "edited_by": str(editor)}
        for chip in chips
    ]


@router.patch(
    "/v1/questions/{id}/plan",
    status_code=202,
    response_model=PlanAccepted,
    **endpoint("PATCH", "/v1/questions/{id}/plan", operation_id="editQuestionPlan"),
)
async def edit_question_plan(
    body: PlanPatch,
    question_id: IdPath,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
) -> PlanAccepted:
    async with transaction(ROLE, principal) as session:
        question = await session.scalar(
            scope.questions_in(principal, data_space)
            .where(qa.Question.id == question_id, qa.Conversation.user_id == principal.user_id)
            .with_for_update(of=qa.Question)
        )
        if question is None:
            raise Problem(404, "not_found", "Question not found.")
        current = await _current_plan(session, question.id)
        if current is None or question.status not in PLAN_EDITABLE:
            raise Problem(
                409,
                "invalid_state",
                f"The plan cannot be edited while the question is {question.status}.",
            )
        # A new version; the worker re-plans its steps from the edited chips (UC-01-03 step 4).
        plan = qa.Plan(
            id=uuid7(),
            org_id=principal.org_id,
            question_id=question.id,
            version=current.version + 1,
            chips=_stamp_chips(body.chips, current.chips, principal.user_id),
            steps=[],
            edited_by_user=True,
            edit_note=body.edit_note,
        )
        session.add(plan)
        question.status = "planning"
        await session.flush()
        scope.emit(
            session,
            qa.Outbox,
            principal,
            aggregate_type="plan",
            aggregate_id=plan.id,
            event_type="plan.edited",
            payload={
                "plan_id": plan.id,
                "question_id": question.id,
                "version": plan.version,
                "previous_plan_id": current.id,
                "question_status": "planning",
                "edited_by": principal.user_id,
            },
        )
    response.headers["Location"] = f"/v1/questions/{question.id}"
    return PlanAccepted(id=question.id, status="planning", plan_id=plan.id, version=plan.version)


# ─── conversations ───


@router.get(
    "/v1/conversations",
    response_model=Page[ConversationView],
    **endpoint("GET", "/v1/conversations", operation_id="listConversations"),
)
async def list_conversations(
    principal: CurrentPrincipal,
    page: Annotated[PageRequest, Depends(page_request)],
    data_space: CurrentDataSpace,
    q: Annotated[str | None, Query(max_length=200)] = None,
    from_: Annotated[datetime.datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime.datetime | None, Query()] = None,
) -> Page[ConversationView]:
    stmt = (
        select(qa.Conversation)
        .where(
            qa.Conversation.org_id == principal.org_id,
            qa.Conversation.user_id == principal.user_id,
            qa.Conversation.data_space_id == data_space,
            qa.Conversation.deleted_at.is_(None),
        )
        .order_by(qa.Conversation.updated_at.desc(), qa.Conversation.id.desc())
        .limit(page.page_size + 1)
    )
    if q:
        stmt = stmt.where(qa.Conversation.title.ilike(f"%{q}%"))
    if from_ is not None:
        stmt = stmt.where(qa.Conversation.updated_at >= from_)
    if to is not None:
        stmt = stmt.where(qa.Conversation.updated_at < to)
    if page.cursor:
        try:
            after = (
                datetime.datetime.fromisoformat(page.cursor["updated_at"]),
                uuid.UUID(page.cursor["id"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise invalid_page_token() from exc
        stmt = stmt.where(tuple_(qa.Conversation.updated_at, qa.Conversation.id) < after)
    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(stmt)).all())
    more = len(rows) > page.page_size
    rows = rows[: page.page_size]
    last = rows[-1] if more else None
    return Page(
        items=[ConversationView.model_validate(row) for row in rows],
        next_page_token=encode_page_token(
            {"updated_at": last.updated_at.isoformat(), "id": str(last.id)} if last else None
        ),
    )


# ─── answers and lineage ───


async def _answer(
    session: Any,
    answer_id: uuid.UUID,
    principal: Principal,
    space: uuid.UUID,
    *,
    lock: bool = False,
) -> qa.Answer:
    stmt = scope.answers_in(principal, space).where(qa.Answer.id == answer_id)
    if lock:
        stmt = stmt.with_for_update(of=qa.Answer)
    answer: qa.Answer | None = await session.scalar(stmt)
    if answer is None:
        raise Problem(404, "not_found", "Answer not found.")
    return answer


@router.get(
    "/v1/answers/{id}",
    response_model=AnswerView,
    **endpoint("GET", "/v1/answers/{id}", operation_id="getAnswer"),
)
async def get_answer(
    answer_id: IdPath, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> AnswerView:
    async with transaction(ROLE, principal) as session:
        answer = await _answer(session, answer_id, principal, data_space)
    return AnswerView.model_validate(answer)


def _figure_key(figure: dict[str, Any]) -> str | None:
    key = figure.get("figure_key") or figure.get("key") or figure.get("id")
    return str(key) if key is not None else None


def _figure_ref(figure: dict[str, Any], name: str) -> uuid.UUID | None:
    # G5.15: a figure carries its source as {query_run_id, row, col} or {compute_run_id, expr},
    # either flat or under "source".
    nested = figure.get("source")
    source: dict[str, Any] = nested if isinstance(nested, dict) else {}
    raw = figure.get(name) or source.get(name)
    try:
        return uuid.UUID(str(raw)) if raw else None
    except ValueError:
        return None


@router.get(
    "/v1/answers/{id}/lineage",
    response_model=LineageView,
    **endpoint("GET", "/v1/answers/{id}/lineage", operation_id="getAnswerLineage"),
)
async def get_answer_lineage(
    answer_id: IdPath,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    figure: Annotated[str | None, Query(max_length=64)] = None,
) -> LineageView:
    async with transaction(ROLE, principal) as session:
        answer = await _answer(session, answer_id, principal, data_space)
        plan = await session.scalar(select(qa.Plan).where(qa.Plan.id == answer.plan_id))
        figures = [f for f in (answer.figures or []) if isinstance(f, dict)]
        if figure is not None:
            figures = [f for f in figures if _figure_key(f) == figure]
            if not figures:
                raise Problem(404, "not_found", "The answer has no such figure.")
        version_ids = {v for f in figures if (v := _figure_ref(f, "metric_version_id"))}
        versions = (
            {
                v.id: v
                for v in (
                    await session.scalars(
                        select(context.MetricVersion).where(
                            context.MetricVersion.id.in_(version_ids)
                        )
                    )
                ).all()
            }
            if version_ids
            else {}
        )
        receipt_figures: dict[str, Any] = {}
        receipt = await session.scalar(
            select(qa.AnswerReceipt).where(
                qa.AnswerReceipt.answer_id == answer.id, qa.AnswerReceipt.sealed_at.is_not(None)
            )
        )
        if receipt is not None:
            for row in (
                await session.scalars(
                    select(qa.AnswerReceiptFigure).where(
                        qa.AnswerReceiptFigure.receipt_id == receipt.id
                    )
                )
            ).all():
                receipt_figures[row.figure_key] = ReceiptFigureView.model_validate(row).model_dump(
                    mode="json"
                )
        # UC-01-02 postcondition: audit_log has a view_lineage row.
        scope.audit(
            session,
            qa.Outbox,
            principal,
            action="view_lineage",
            object_kind="answer",
            object_id=answer.id,
            after={"figure": figure},
        )
    run_ids = {r for f in figures if (r := _figure_ref(f, "query_run_id"))}
    runs: dict[uuid.UUID, query_models.QueryRun] = {}
    if run_ids:
        async with transaction(QUERY_ROLE, principal) as session:
            for run in (
                await session.scalars(
                    select(query_models.QueryRun).where(
                        query_models.QueryRun.id.in_(run_ids),
                        query_models.QueryRun.org_id == principal.org_id,
                        query_models.QueryRun.data_space_id == data_space,
                    )
                )
            ).all():
                runs[run.id] = run
    items = []
    for f in figures:
        version_ref = _figure_ref(f, "metric_version_id")
        run_ref = _figure_ref(f, "query_run_id")
        version = versions.get(version_ref) if version_ref else None
        figure_run = runs.get(run_ref) if run_ref else None
        key = _figure_key(f)
        items.append(
            LineageFigure(
                figure_key=key,
                figure=f,
                metric_version=MetricVersionRef.model_validate(version, from_attributes=True)
                if version
                else None,
                query_run=QueryRunRef.model_validate(figure_run, from_attributes=True)
                if figure_run
                else None,
                compute_run_id=_figure_ref(f, "compute_run_id"),
                receipt_figure=receipt_figures.get(key) if key else None,
            )
        )
    return LineageView(
        answer_id=answer.id,
        lineage_hash=answer.lineage_hash,
        plan=PlanView.model_validate(plan) if plan else None,
        figures=items,
    )


# ─── feedback ───


@router.post(
    "/v1/answers/{id}/feedback",
    status_code=201,
    response_model=FeedbackView,
    **endpoint("POST", "/v1/answers/{id}/feedback", operation_id="createAnswerFeedback"),
)
async def create_feedback(
    body: FeedbackCreate,
    answer_id: IdPath,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> FeedbackView:
    async with transaction(ROLE, principal) as session:
        # Locked so concurrent thumbs-down are counted one after the other (UC-01-05 1a).
        answer = await _answer(session, answer_id, principal, data_space, lock=True)
        feedback = qa.Feedback(
            id=uuid7(),
            org_id=principal.org_id,
            answer_id=answer.id,
            user_id=principal.user_id,
            created_by=principal.user_id,
            updated_by=principal.user_id,
            status="open",
            **body.model_dump(),
        )
        session.add(feedback)
        await session.flush()
        trigger = None
        if body.rating == "wrong":
            trigger = "reported_wrong"
        elif body.rating == "down":
            voters = await session.scalar(
                select(func.count(func.distinct(qa.Feedback.user_id))).where(
                    qa.Feedback.answer_id == answer.id,
                    qa.Feedback.rating == "down",
                    qa.Feedback.created_at > func.now() - DOWN_VOTE_WINDOW,
                )
            )
            already = await session.scalar(
                select(func.count()).where(
                    qa.Feedback.answer_id == answer.id,
                    qa.Feedback.rating == "down",
                    qa.Feedback.user_id == principal.user_id,
                    qa.Feedback.id != feedback.id,
                    qa.Feedback.created_at > func.now() - DOWN_VOTE_WINDOW,
                )
            )
            # Exactly at the threshold, and only from a new voter: one review, not one per vote.
            if voters == DOWN_VOTES_FOR_REVIEW and not already:
                trigger = "down_votes"
        if trigger is not None:
            # §9.3: Inbox opens the review request for the Data Space's steward.
            scope.emit(
                session,
                qa.Outbox,
                principal,
                aggregate_type="feedback",
                aggregate_id=feedback.id,
                event_type="feedback.reported",
                payload={
                    "feedback_id": feedback.id,
                    "answer_id": answer.id,
                    "question_id": answer.question_id,
                    "data_space_id": data_space,
                    "reporter_id": principal.user_id,
                    "rating": body.rating,
                    "error_kind": body.error_kind,
                    "trigger": trigger,
                    "status": "open",
                },
            )
        await session.refresh(feedback)
    return FeedbackView.model_validate(feedback)


async def _feedback(
    session: Any, feedback_id: uuid.UUID, principal: Principal, space: uuid.UUID
) -> qa.Feedback:
    feedback: qa.Feedback | None = await session.scalar(
        scope.feedback_in(principal, space)
        .where(qa.Feedback.id == feedback_id)
        .with_for_update(of=qa.Feedback)
    )
    if feedback is None:
        raise Problem(404, "not_found", "Feedback not found.")
    return feedback


@router.patch(
    "/v1/feedback/{id}",
    response_model=FeedbackView,
    **endpoint("PATCH", "/v1/feedback/{id}", operation_id="reviewFeedback"),
)
async def review_feedback(
    body: FeedbackPatch,
    feedback_id: IdPath,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> FeedbackView:
    async with transaction(ROLE, principal) as session:
        feedback = await _feedback(session, feedback_id, principal, data_space)
        if body.status not in FEEDBACK_NEXT.get(feedback.status, frozenset()):
            raise Problem(
                409,
                "invalid_state",
                f"Feedback in status {feedback.status} cannot become {body.status}.",
            )
        feedback.status = body.status
        if body.reason is not None:
            feedback.reason = body.reason
        if body.status in ("confirmed", "rejected"):
            feedback.reviewer_id = principal.user_id
            feedback.decided_at = func.now()
        feedback.updated_by = principal.user_id
        feedback.updated_at = func.now()
        await session.flush()
        scope.emit(
            session,
            qa.Outbox,
            principal,
            aggregate_type="feedback",
            aggregate_id=feedback.id,
            event_type=FEEDBACK_EVENTS[body.status],
            payload={
                "feedback_id": feedback.id,
                "answer_id": feedback.answer_id,
                "data_space_id": data_space,
                "reporter_id": feedback.user_id,
                "reviewer_id": principal.user_id,
                "status": body.status,
            },
        )
        await session.refresh(feedback)
    return FeedbackView.model_validate(feedback)


@router.post(
    "/v1/feedback/{id}:to-eval",
    status_code=202,
    response_model=EvalItemAccepted,
    **endpoint("POST", "/v1/feedback/{id}:to-eval", operation_id="sendFeedbackToEval"),
)
async def send_feedback_to_eval(
    body: FeedbackToEval,
    feedback_id: IdPath,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> EvalItemAccepted:
    # The event makes the Eval service add an item to this set: a set of another Data Space would
    # carry this Data Space's question and answer into it.
    if not await eval_set_accepts_items(principal, data_space, body.eval_set_id):
        raise _invalid("eval_set_id", "No open eval set with this id in this Data Space.")
    async with transaction(ROLE, principal) as session:
        feedback = await _feedback(session, feedback_id, principal, data_space)
        if feedback.status != "confirmed":
            raise Problem(409, "invalid_state", "Only confirmed feedback goes into an eval set.")
        if feedback.eval_item_id is not None:
            raise Problem(409, "invalid_state", "This feedback is already in an eval set.")
        # The eval item lives in schema eval (D1.2): the Eval service creates it from the event,
        # with the id reserved here so the feedback points at it from now on (BR-83).
        item_id = uuid7()
        feedback.eval_item_id = item_id
        feedback.updated_by = principal.user_id
        feedback.updated_at = func.now()
        answer = await session.scalar(select(qa.Answer).where(qa.Answer.id == feedback.answer_id))
        scope.emit(
            session,
            qa.Outbox,
            principal,
            aggregate_type="feedback",
            aggregate_id=feedback.id,
            event_type="feedback.sent_to_eval",
            payload={
                "feedback_id": feedback.id,
                "eval_item_id": item_id,
                "eval_set_id": body.eval_set_id,
                "answer_id": feedback.answer_id,
                "question_id": answer.question_id if answer else None,
                "plan_id": answer.plan_id if answer else None,
                "data_space_id": data_space,
                "requested_by": principal.user_id,
            },
        )
    return EvalItemAccepted(
        id=item_id, status="requested", feedback_id=feedback.id, eval_set_id=body.eval_set_id
    )


# ─── exports ───


async def _answer_row_count(principal: Principal, space: uuid.UUID, figures: Any) -> int | None:
    run_ids = {
        r for f in (figures or []) if isinstance(f, dict) and (r := _figure_ref(f, "query_run_id"))
    }
    if not run_ids:
        return None
    async with transaction(QUERY_ROLE, principal) as session:
        count: int | None = await session.scalar(
            select(func.max(query_models.QueryRun.row_count)).where(
                query_models.QueryRun.id.in_(run_ids),
                query_models.QueryRun.org_id == principal.org_id,
                query_models.QueryRun.data_space_id == space,
            )
        )
    return count


@router.post(
    "/v1/answers/{id}/exports",
    status_code=202,
    response_model=JobAccepted,
    **endpoint("POST", "/v1/answers/{id}/exports", operation_id="createAnswerExport"),
)
async def create_answer_export(
    body: ExportCreate,
    answer_id: IdPath,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
) -> JobAccepted:
    async with transaction(ROLE, principal) as session:
        answer = await _answer(session, answer_id, principal, data_space)
    row_count = await _answer_row_count(principal, data_space, answer.figures)
    if row_count is not None and row_count > EXPORT_MAX_ROWS:
        raise Problem(
            422,
            "business_rule_violated",
            f"The result has {row_count} rows, more than a file export allows ({EXPORT_MAX_ROWS}); "
            "save it as a derived table instead (UC-01-06 E1).",
        )
    # UC-01-06 2a: detail rows may carry personal data and large results exceed the export
    # policy, so both wait for an approval request (Inbox creates it from the event).
    needs_approval = body.include_detail_rows or (row_count or 0) > EXPORT_APPROVAL_ROWS
    status = "waiting_approval" if needs_approval else "queued"
    async with transaction(ROLE, principal) as session:
        export = qa.ExportJob(
            id=uuid7(),
            org_id=principal.org_id,
            answer_id=answer.id,
            requested_by=principal.user_id,
            format=body.format,
            include_detail_rows=body.include_detail_rows,
            row_count=row_count,
            status=status,
        )
        session.add(export)
        await session.flush()
        scope.emit(
            session,
            qa.Outbox,
            principal,
            aggregate_type="export_job",
            aggregate_id=export.id,
            event_type="export_job.requested",
            payload={
                "export_job_id": export.id,
                "answer_id": answer.id,
                "data_space_id": data_space,
                "requested_by": principal.user_id,
                "format": body.format,
                "include_detail_rows": body.include_detail_rows,
                "row_count": row_count,
                "status": status,
            },
        )
    response.headers["Location"] = f"/v1/exports/{export.id}"
    return JobAccepted(id=export.id, status=status)


@router.get(
    "/v1/exports/{id}",
    response_model=ExportView,
    **endpoint("GET", "/v1/exports/{id}", operation_id="getExport"),
)
async def get_export(
    export_id: IdPath, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> ExportView:
    async with transaction(ROLE, principal) as session:
        # BR-86: only the person who exported can fetch the file.
        export = await session.scalar(
            scope.exports_in(principal, data_space).where(
                qa.ExportJob.id == export_id, qa.ExportJob.requested_by == principal.user_id
            )
        )
    if export is None:
        raise Problem(404, "not_found", "Export not found.")
    if export.status == "expired" or (
        export.expires_at is not None and export.expires_at <= _now()
    ):
        raise Problem(410, "gone", "The download link has expired (BR-86).")
    return ExportView.model_validate(export)


# ─── receipts and replays ───


def _receipt_lookup(principal: Principal, space: uuid.UUID, ref: str) -> Any:
    # A1.1: a resource with a business code takes the code or the id in its path.
    stmt = scope.receipts_in(principal, space)
    try:
        return stmt.where(qa.AnswerReceipt.id == uuid.UUID(ref))
    except ValueError:
        return stmt.where(qa.AnswerReceipt.receipt_no == ref)


async def _receipt_view(session: Any, receipt: qa.AnswerReceipt) -> ReceiptView:
    figures = (
        await session.scalars(
            select(qa.AnswerReceiptFigure)
            .where(qa.AnswerReceiptFigure.receipt_id == receipt.id)
            .order_by(qa.AnswerReceiptFigure.figure_key)
        )
    ).all()
    view = ReceiptView.model_validate(receipt)
    view.figures = [ReceiptFigureView.model_validate(f) for f in figures]
    return view


@router.get(
    "/v1/answers/{id}/receipt",
    response_model=ReceiptView,
    **endpoint("GET", "/v1/answers/{id}/receipt", operation_id="getAnswerReceipt"),
)
async def get_answer_receipt(
    answer_id: IdPath, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> ReceiptView:
    async with transaction(ROLE, principal) as session:
        # BR-176: a receipt is shown once sealed; an out-of-scope answer has none (404, A3).
        receipt = await session.scalar(
            scope.receipts_in(principal, data_space).where(
                qa.AnswerReceipt.answer_id == answer_id, qa.AnswerReceipt.sealed_at.is_not(None)
            )
        )
        if receipt is None:
            raise Problem(404, "not_found", "This answer has no receipt.")
        return await _receipt_view(session, receipt)


@router.get(
    "/v1/receipts/{receipt_no}",
    response_model=ReceiptView,
    **endpoint("GET", "/v1/receipts/{receipt_no}", operation_id="getReceipt"),
)
async def get_receipt(
    receipt_no: Annotated[str, Path(max_length=64)],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    verify: bool = False,
) -> ReceiptView:
    async with transaction(ROLE, principal) as session:
        receipt = await session.scalar(
            _receipt_lookup(principal, data_space, receipt_no).where(
                qa.AnswerReceipt.sealed_at.is_not(None)
            )
        )
        if receipt is None:
            raise Problem(404, "not_found", "Receipt not found.")
        view = await _receipt_view(session, receipt)
        if verify:
            recomputed = await session.scalar(RECEIPT_HASH_SQL, {"id": receipt.id})
            view.hash_match = recomputed == receipt.receipt_sha256
            if not view.hash_match:
                # A3: a mismatch is a security event; Audit records it from this event. Written
                # before the 409 so the refusal does not roll it back.
                scope.emit(
                    session,
                    qa.Outbox,
                    principal,
                    aggregate_type="answer_receipt",
                    aggregate_id=receipt.id,
                    event_type="qa.receipt.hash_mismatch",
                    payload={
                        "receipt_id": receipt.id,
                        "receipt_no": receipt.receipt_no,
                        "answer_id": receipt.answer_id,
                        "verified_by": principal.user_id,
                    },
                )
    if view.hash_match is False:
        raise Problem(
            409, "conflict", "The receipt does not match its SHA-256 (hash_match = false)."
        )
    return view


@router.post(
    "/v1/receipts/{id}:replay",
    status_code=202,
    response_model=JobAccepted,
    **endpoint("POST", "/v1/receipts/{id}:replay", operation_id="createReceiptReplay"),
)
async def create_receipt_replay(
    body: ReplayCreate,
    receipt_ref: Annotated[str, Path(alias="id", max_length=64)],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
) -> JobAccepted:
    async with transaction(ROLE, principal) as session:
        receipt = await session.scalar(_receipt_lookup(principal, data_space, receipt_ref))
        if receipt is None:
            raise Problem(404, "not_found", "Receipt not found.")
        if receipt.sealed_at is None:
            raise Problem(409, "invalid_state", "The receipt is not sealed yet.")
        if receipt.retain_until < _now().date():
            raise Problem(410, "gone", "The receipt is past its retention date.")
        # The insert trigger requests the receipt.replay audit row (routines/qa.sql).
        replay = qa.ReceiptReplay(
            id=uuid7(),
            org_id=principal.org_id,
            receipt_id=receipt.id,
            requested_by=principal.user_id,
            reason=body.reason,
            mode=body.mode,
            compiler_version_used=receipt.compiler_version,
            status="running",
        )
        session.add(replay)
        await session.flush()
        # BR-177: the worker re-runs the stored plan with the caller's rights and
        # knowledge_at = answered_at.
        scope.emit(
            session,
            qa.Outbox,
            principal,
            aggregate_type="receipt_replay",
            aggregate_id=replay.id,
            event_type="receipt_replay.requested",
            payload={
                "replay_id": replay.id,
                "receipt_id": receipt.id,
                "answer_id": receipt.answer_id,
                "plan_id": receipt.plan_id,
                "data_space_id": data_space,
                "requested_by": principal.user_id,
                "mode": body.mode,
                "knowledge_mode": "at",
                "knowledge_at": receipt.answered_at,
                "compiler_version": receipt.compiler_version,
                "status": "running",
            },
        )
    response.headers["Location"] = f"/v1/receipt-replays/{replay.id}"
    return JobAccepted(id=replay.id, status="running", poll_after_ms=2000)


@router.get(
    "/v1/receipt-replays/{id}",
    response_model=ReplayView,
    **endpoint("GET", "/v1/receipt-replays/{id}", operation_id="getReceiptReplay"),
)
async def get_receipt_replay(
    replay_id: IdPath, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> ReplayView:
    async with transaction(ROLE, principal) as session:
        replay = await session.scalar(
            scope.replays_in(principal, data_space).where(qa.ReceiptReplay.id == replay_id)
        )
    if replay is None:
        raise Problem(404, "not_found", "Receipt replay not found.")
    return ReplayView.model_validate(replay)
