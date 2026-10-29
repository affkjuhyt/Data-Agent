"""The review path of the semantic layer (UC-07-01, UC-07-02, UC-07-03, UC-07-09).

What a steward writes in the context module is a draft: a metric version, a synonym, a declared
relationship, an example question, a glossary term. The agent reads only what is active
(semantic/loader.py), so each draft reaches it through one inbox proposal that a second person
decides. A metric version is metric_changes.py's (its impact on what depends on the metric, and
the standard set run with it in place through evaluate_overlay here); the rest are this module's:

- synonym.created, relationship.created: the standard set is run with the item counted active,
  then a synonym / relationship proposal opens.
- example_query.created / updated (back to draft): its SQL is run read-only on the Data Space
  workspace; one that fails stays a draft, one that runs becomes pending_review with a proposal.
- glossary_term.created: a glossary proposal.

proposal.decided (proposals.decided routes these kinds here): accepted, the item becomes active;
rejected, it goes back to draft or rejected.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import Any

from sqlalchemy import update

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import catalog, context
from services.worker_agent import evaluation
from services.worker_agent.events import Event, Handler
from services.worker_agent.query.executor import run_query
from services.worker_agent.semantic.loader import Overlay
from services.worker_agent.sqlgen import verified
from services.worker_agent.sqlgen.answer import QUERYABLE
from services.worker_agent.understand.text import find_period

logger = logging.getLogger(__name__)

ROLE = "svc_context"
SYNONYM = "synonym.created"
RELATIONSHIP = "relationship.created"
EXAMPLE_CREATED = "example_query.created"
EXAMPLE_UPDATED = "example_query.updated"
TERM = "glossary_term.created"
KINDS = ("synonym", "relationship", "example", "glossary")
PERIODS = 6


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


async def _open(
    event: Event,
    *,
    space_id: uuid.UUID,
    kind: str,
    target_ref: uuid.UUID,
    title: str,
    diff: dict[str, Any],
    proposed_by: uuid.UUID,
    eval_run_id: uuid.UUID | None,
) -> uuid.UUID:
    from services.worker_agent import proposals

    return await proposals._open(
        event,
        data_space_id=space_id,
        kind=kind,
        target_service="context",
        target_ref=target_ref,
        title=title,
        diff=diff,
        proposed_by=proposed_by,
        eval_run_id=eval_run_id,
    )


async def evaluate_overlay(
    event: Event,
    space_id: uuid.UUID,
    actor: uuid.UUID,
    overlay: Overlay,
    subject: str,
    trigger_ref: uuid.UUID,
    run_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID | None, dict[str, Any]]:
    run = await evaluation.start_run(
        org_id=event.org_id,
        space_id=space_id,
        user_id=actor,
        trigger="proposal",
        trigger_ref=trigger_ref,
        subject_version=subject,
        run_id=run_id,
    )
    if run is None:
        return None, {"note": "Data Space chưa có bộ câu hỏi chuẩn; chưa đo được ảnh hưởng."}
    summary = await evaluation.run_set(
        org_id=event.org_id, space_id=space_id, run_id=run, user_id=actor, overlay=overlay
    )
    return run, {
        "items": summary.item_count,
        "passed": summary.passed,
        "pass_rate": None if summary.pass_rate is None else str(summary.pass_rate),
        "regressions": summary.regressions,
        "changed_items": [str(i) for i in summary.changed],
    }


async def synonym_created(event: Event) -> None:
    p = event.payload
    synonym_id = uuid.UUID(str(p["synonym_id"]))
    async with scoped_session(ROLE, event.org_id) as session:
        row = await session.get(context.Synonym, synonym_id)
        if row is None or row.status != "pending_review":
            return
        actor, phrase, kind, target, value = (
            row.created_by,
            row.phrase,
            row.target_kind,
            row.target_id,
            row.target_value,
        )
    if actor is None:
        return
    space_id = uuid.UUID(str(p["data_space_id"]))
    run, impact = await evaluate_overlay(
        event,
        space_id,
        actor,
        Overlay(synonym_ids=frozenset({synonym_id})),
        f"synonym:{synonym_id}",
        synonym_id,
    )
    await _open(
        event,
        space_id=space_id,
        kind="synonym",
        target_ref=synonym_id,
        title=f"Từ đồng nghĩa “{phrase}”",
        diff={
            "phrase": phrase,
            "target_kind": kind,
            "target_id": str(target),
            "target_value": value,
            "impact": impact,
        },
        proposed_by=actor,
        eval_run_id=run,
    )


async def relationship_created(event: Event) -> None:
    rel_id = uuid.UUID(str(event.aggregate_id))
    async with scoped_session(ROLE, event.org_id) as session:
        row = await session.get(context.Relationship, rel_id)
        if row is None or row.status != "pending_review":
            return
        actor = row.created_by or row.updated_by
        space_id = row.data_space_id
        diff = {
            "from_dataset_id": str(row.from_dataset_id),
            "from_columns": list(row.from_columns),
            "to_dataset_id": str(row.to_dataset_id),
            "to_columns": list(row.to_columns),
            "cardinality": row.cardinality,
        }
    if actor is None:
        return
    run, impact = await evaluate_overlay(
        event,
        space_id,
        actor,
        Overlay(relationship_ids=frozenset({rel_id})),
        f"relationship:{rel_id}",
        rel_id,
    )
    await _open(
        event,
        space_id=space_id,
        kind="relationship",
        target_ref=rel_id,
        title="Quan hệ giữa hai bảng",
        diff={**diff, "impact": impact},
        proposed_by=actor,
        eval_run_id=run,
    )


async def example_changed(event: Event) -> None:
    """The checker of an example's SQL (UC-07-02 step 3): a draft whose SQL runs read-only on the
    workspace becomes pending_review with a proposal; one that fails stays a draft."""
    from services.worker_agent.questions import _internal_workspace_source, secrets

    example_id = uuid.UUID(str(event.aggregate_id))
    async with scoped_session(ROLE, event.org_id) as session:
        row = await session.get(context.ExampleQuery, example_id)
        if row is None or row.status != "draft":
            return
        actor = row.created_by or row.updated_by
        space_id, question, stored_sql = row.data_space_id, row.question, row.sql
        # Run as it will answer (sqlgen/verified.py): its period placeholders bound to its own
        # question's period, on the source its tables are datasets of.
        sql = verified.bind(
            stored_sql, find_period(question, datetime.date.today()), datetime.date.today()
        )
        if actor is None:
            return
        origin = await verified.origin_of(session, space_id, sql, actor)
        if not origin.ok:
            logger.info("worker-agent | example %s reads no one source of its space", example_id)
            return
        source = (
            await session.get(catalog.Source, origin.source_id)
            if origin.source_id is not None
            else None
        )
        # Run only where an answer would (sqlgen/answer.py): a source queried in place, live.
        if origin.source_id is not None and (
            source is None or source.mode != "federated" or source.status not in QUERYABLE
        ):
            logger.info("worker-agent | example %s source is not queryable", example_id)
            return
        if source is not None:
            session.expunge(source)
    outcome = await run_query(
        org_id=event.org_id,
        data_space_id=space_id,
        principal_id=actor,
        caller_kind="eval",
        caller_id=example_id,
        source=source or _internal_workspace_source(space_id, event.org_id),
        sql=sql,
        secrets=secrets(),
        limit=100,
    )
    if not outcome.ok:
        logger.info("worker-agent | example %s SQL failed: %s", example_id, outcome.error)
        return
    async with scoped_session(ROLE, event.org_id, actor) as session:
        await session.execute(
            update(context.ExampleQuery)
            .where(context.ExampleQuery.id == example_id, context.ExampleQuery.status == "draft")
            .values(status="pending_review", last_verified_at=_now())
        )
    await _open(
        event,
        space_id=space_id,
        kind="example",
        target_ref=example_id,
        title=f"Câu hỏi mẫu “{question[:80]}”",
        diff={
            "question": question,
            "sql": stored_sql,
            "rows": len(outcome.rows),
            "query_run_id": str(outcome.query_run_id),
        },
        proposed_by=actor,
        eval_run_id=None,
    )


async def term_created(event: Event) -> None:
    term_id = uuid.UUID(str(event.aggregate_id))
    async with scoped_session(ROLE, event.org_id) as session:
        row = await session.get(context.GlossaryTerm, term_id)
        if row is None or row.status != "pending_review":
            return
        actor = row.created_by or row.updated_by
        space_id, term, definition = row.data_space_id, row.term, row.definition
    if actor is None:
        return
    await _open(
        event,
        space_id=space_id,
        kind="glossary",
        target_ref=term_id,
        title=f"Thuật ngữ “{term}”",
        diff={"term": term, "definition": definition},
        proposed_by=actor,
        eval_run_id=None,
    )


async def apply(event: Event, proposal: dict[str, Any]) -> bool:
    """Apply a decided semantic proposal; False when its object is gone or has moved on."""
    accepted = event.payload.get("status") == "accepted"
    reviewer = uuid.UUID(str(event.payload["reviewer_id"]))
    ref = proposal["target_ref"]
    kind = proposal["kind"]
    now = _now()
    async with scoped_session(ROLE, event.org_id, reviewer) as session:
        model: Any
        model, waiting, yes, no = {
            "synonym": (context.Synonym, "pending_review", "active", "rejected"),
            "relationship": (context.Relationship, "pending_review", "active", "rejected"),
            "example": (context.ExampleQuery, "pending_review", "active", "rejected"),
            "glossary": (context.GlossaryTerm, "pending_review", "active", "retired"),
        }[kind]
        row = await session.get(model, ref, with_for_update=True)
        if row is None or row.status != waiting:
            return False
        row.status = yes if accepted else no
        if hasattr(row, "updated_by"):
            row.updated_by = reviewer
        if hasattr(row, "updated_at"):
            row.updated_at = now
        return True


HANDLERS: dict[str, Handler] = {
    SYNONYM: synonym_created,
    RELATIONSHIP: relationship_created,
    EXAMPLE_CREATED: example_changed,
    EXAMPLE_UPDATED: example_changed,
    TERM: term_created,
}
