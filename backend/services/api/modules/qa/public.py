"""What other api modules may ask of the qa module (ADR-28: through this, never its tables).

Each function opens its own svc_qa transaction: a caller's connection (svc_integration for MCP)
has no grant on schema qa, and modules do not share transactions.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import qa
from libs.dataagent_db.models import query as query_models
from services.api.kernel.auth import Principal
from services.api.kernel.db import transaction
from services.api.modules.qa import scope
from services.api.modules.qa.questions import insert_question
from services.api.modules.qa.schemas import AnswerView, QueryRunView

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


PREVIEW_ROWS = 50
PREVIEW_FRESH = datetime.timedelta(minutes=10)


def preview_marker(seq: int, steps: list[Any]) -> str:
    """What a step preview run is recognised by: the step and the steps as they are now."""
    digest = hashlib.sha256(
        json.dumps(steps, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()
    return f"-- pipeline step {seq} {digest}"


async def pipeline_step_preview(
    principal: Principal,
    data_space_id: uuid.UUID,
    pipeline_id: uuid.UUID,
    seq: int,
    steps: list[Any],
) -> QueryRunView:
    """The query run previewing one pipeline step on its source (UC-06-01 step 3): the caller's
    own recent one for these steps, or a new one queued. Its rows are read like any run's
    (GET /v1/query-runs/{id}/rows), under the caller's policy; worker-agent builds the SQL from
    the pipeline. The caller has checked the pipeline is theirs to see."""
    marker = preview_marker(seq, steps)
    async with transaction("svc_query", principal) as session:
        q = query_models.QueryRun
        known = await session.scalar(
            select(q)
            .where(
                q.org_id == principal.org_id,
                q.caller_kind == "pipeline",
                q.caller_id == pipeline_id,
                q.principal_id == principal.user_id,
                q.sql_generated == marker,
                q.status.in_(("queued", "running", "succeeded")),
                q.started_at > datetime.datetime.now(datetime.UTC) - PREVIEW_FRESH,
            )
            .order_by(q.started_at.desc())
            .limit(1)
        )
        if known is not None:
            return QueryRunView.model_validate(known)
        run = q(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=data_space_id,
            caller_kind="pipeline",
            caller_id=pipeline_id,
            principal_id=principal.user_id,
            route="live",
            sql_generated=marker,
            sql_executed="",
            status="queued",
        )
        session.add(run)
        await session.flush()
        await session.refresh(run)
        scope.emit(
            session,
            query_models.Outbox,
            principal,
            aggregate_type="query_run",
            aggregate_id=run.id,
            event_type="query_run.requested",
            payload={
                "query_run_id": run.id,
                "started_at": run.started_at,
                "data_space_id": data_space_id,
                "principal_id": principal.user_id,
                "mode": "pipeline_step",
                "pipeline_id": pipeline_id,
                "seq": seq,
                "limit": PREVIEW_ROWS,
                "status": "queued",
            },
        )
        return QueryRunView.model_validate(run)


async def query_result_columns(
    principal: Principal, query_run_id: uuid.UUID
) -> list[dict[str, Any]] | None:
    """The columns ({name, type}) a finished run returned, None while it has no result."""
    async with transaction("svc_query", principal) as session:
        columns = await session.scalar(
            select(query_models.QueryResult.columns).where(
                query_models.QueryResult.query_run_id == query_run_id,
                query_models.QueryResult.org_id == principal.org_id,
            )
        )
    if not isinstance(columns, list):
        return None
    return [
        {"name": str(c.get("name")), "type": str(c.get("type") or "unknown")}
        if isinstance(c, dict)
        else {"name": str(c), "type": "unknown"}
        for c in columns
    ]
