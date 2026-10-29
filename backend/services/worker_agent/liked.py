"""A liked answer from SQL the model wrote becomes a proposed verified query (R1-08).

POST /v1/answers/{id}/feedback with rating `up`, on an answer whose plan is exploratory_sql, emits
feedback.liked. Here the question and the winning SQL become a draft context.example_query (origin
feedback), and example_query.created hands it to the checker (semantic_review.example_changed):
the SQL is run, and only one that runs goes to the steward to approve. A question the Data Space
already has an example for, active or on its way, is not proposed twice.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import context, qa
from services.worker_agent.events import Event, Handler

HANDLER = "feedback.liked"
ROLE = "svc_context"
_OPEN = ("draft", "pending_review", "active")


async def propose_example(event: Event) -> None:
    answer_id = uuid.UUID(str(event.payload["answer_id"]))
    space_id = uuid.UUID(str(event.payload["data_space_id"]))
    user_id = uuid.UUID(str(event.payload["user_id"]))
    async with scoped_session(ROLE, event.org_id, user_id) as session:
        answer = await session.get(qa.Answer, answer_id)
        if answer is None or answer.plan_id is None:
            return
        plan = await session.get(qa.Plan, answer.plan_id)
        question = await session.get(qa.Question, answer.question_id)
        steps = plan.steps if plan is not None and isinstance(plan.steps, dict) else {}
        sql = next(
            (
                s.get("args", {}).get("sql")
                for s in steps.get("steps", [])
                if isinstance(s, dict) and s.get("kind") == "sql_query"
            ),
            None,
        )
        if question is None or steps.get("path") != "exploratory_sql" or not sql:
            return
        text = question.text.strip()
        taken = await session.scalar(
            select(context.ExampleQuery.id).where(
                context.ExampleQuery.data_space_id == space_id,
                func.lower(context.ExampleQuery.question) == text.lower(),
                context.ExampleQuery.status.in_(_OPEN),
            )
        )
        if taken is not None:
            return
        row = context.ExampleQuery(
            id=uuid7(),
            org_id=event.org_id,
            data_space_id=space_id,
            question=text,
            sql=sql,
            metric_ids=[],
            origin="feedback",
            status="draft",
            created_by=user_id,
            updated_by=user_id,
        )
        session.add(row)
        await session.flush()
        session.add(
            context.Outbox(
                id=uuid7(),
                org_id=event.org_id,
                aggregate_type="example_query",
                aggregate_id=row.id,
                event_type="example_query.created",
                payload={
                    "example_query_id": str(row.id),
                    "data_space_id": str(space_id),
                    "status": row.status,
                    "origin": "feedback",
                },
            )
        )


HANDLERS: dict[str, Handler] = {HANDLER: propose_example}
