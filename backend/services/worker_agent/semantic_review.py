"""The review path of the semantic layer (UC-07-01, UC-07-02, UC-07-03, UC-07-09).

What a steward writes in the context module is a draft: a metric version, a synonym, a declared
relationship, an example question, a glossary term. The agent reads only what is active
(semantic/loader.py), so each draft reaches it through one inbox proposal that a second person
decides:

- metric_version.preview_requested: the draft and the version in force are run on the last six
  periods of the metric's time dimension (or once, without one), the Data Space's standard set is
  run with the draft in place, and a metric_change proposal carries both; a draft that does not
  compile becomes `broken` and opens nothing.
- synonym.created, relationship.created: the standard set is run with the item counted active,
  then a synonym / relationship proposal opens.
- example_query.created / updated (back to draft): its SQL is run read-only on the Data Space
  workspace; one that fails stays a draft, one that runs becomes pending_review with a proposal.
- glossary_term.created: a glossary proposal.

proposal.decided (proposals.decided routes these kinds here): accepted, the item becomes active,
a metric version certified with the reviewer as certifier and the metric active, the version it
replaces superseded (BR-26: never edited); rejected, it goes back to draft or rejected.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import Any

from sqlalchemy import select, update

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import catalog, context
from services.worker_agent import evaluation
from services.worker_agent.events import Event, Handler
from services.worker_agent.query.executor import run_query
from services.worker_agent.query.guard import SqlRejected
from services.worker_agent.semantic import compiler as c
from services.worker_agent.semantic.loader import Overlay, load

logger = logging.getLogger(__name__)

ROLE = "svc_context"
PREVIEW = "metric_version.preview_requested"
SYNONYM = "synonym.created"
RELATIONSHIP = "relationship.created"
EXAMPLE_CREATED = "example_query.created"
EXAMPLE_UPDATED = "example_query.updated"
TERM = "glossary_term.created"
KINDS = ("metric_change", "synonym", "relationship", "example", "glossary")
PERIODS = 6


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


async def _series(
    org_id: uuid.UUID,
    space_id: uuid.UUID,
    user_id: uuid.UUID,
    metric_id: uuid.UUID,
    overlay: Overlay | None,
    caller_id: uuid.UUID,
) -> tuple[list[dict[str, Any]], str | None]:
    """The metric on its last periods, as the layer (with `overlay`) defines it; or an error."""
    from services.worker_agent.questions import (
        QUERYABLE,
        _internal_workspace_source,
        dialect_for_source,
        secrets,
    )

    async with scoped_session("svc_qa", org_id, user_id) as session:
        layer = await load(session, space_id, overlay)
        version = layer.versions.get(metric_id)
        if version is None:
            return [], None  # no version in force: nothing to compare with
        time = next(
            (
                d.code
                for d in layer.layer.dimensions.values()
                if d.is_time
                and (not version.allowed_dimension_ids or d.id in version.allowed_dimension_ids)
            ),
            None,
        )
        request = c.Request(version, group_by=(time,) if time else ())
        try:
            compiled = c.compile_metric(request, layer.layer, dialect="postgres")
            dataset = layer.datasets[compiled.dataset_ids[0]]
            source = (
                _internal_workspace_source(space_id, org_id)
                if dataset.schema_name == "workspace" and dataset.source_id is None
                else await session.get(catalog.Source, dataset.source_id)
            )
            if source is None or source.mode != "federated" or source.status not in QUERYABLE:
                return [], "the metric's source is not queryable in place"
            compiled = c.compile_metric(
                request, layer.layer, dialect=dialect_for_source(source.kind)
            )
        except (c.CompileError, SqlRejected, KeyError) as exc:
            return [], str(exc) or type(exc).__name__
    outcome = await run_query(
        org_id=org_id,
        data_space_id=space_id,
        principal_id=user_id,
        caller_kind="eval",
        caller_id=caller_id,
        source=source,
        sql=compiled.sql,
        secrets=secrets(),
        metric_version_ids=[compiled.metric_version_id],
    )
    if not outcome.ok:
        return [], outcome.error or outcome.status
    names = [col["name"] for col in outcome.columns]
    rows = [dict(zip(names, row, strict=False)) for row in outcome.rows]
    if time:
        rows = sorted(rows, key=lambda r: str(r.get(time)))[-PERIODS:]
    return [
        {
            "period": str(r.get(time)) if time else None,
            "value": None if r.get("value") is None else str(r["value"]),
        }
        for r in rows
    ], None


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


async def _evaluate(
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


async def preview_requested(event: Event) -> None:
    p = event.payload
    space_id = uuid.UUID(str(p["data_space_id"]))
    version_id = uuid.UUID(str(p["version_id"]))
    async with scoped_session(ROLE, event.org_id) as session:
        version = await session.get(context.MetricVersion, version_id)
        metric = await session.get(context.Metric, uuid.UUID(str(p["metric_id"])))
        if version is None or metric is None or version.status != "testing":
            return
        actor = uuid.UUID(str(p.get("requested_by") or metric.updated_by or metric.created_by))
        in_force = await session.scalar(
            select(context.MetricVersion)
            .where(
                context.MetricVersion.metric_id == metric.id,
                context.MetricVersion.status == "certified",
            )
            .order_by(context.MetricVersion.version.desc())
            .limit(1)
        )
        code, number, expression = metric.code, version.version, version.expression
        previous_expression = in_force.expression if in_force else None
    overlay = Overlay(metric_version_ids=frozenset({version_id}))
    draft, error = await _series(event.org_id, space_id, actor, metric.id, overlay, version_id)
    if error is not None:
        async with scoped_session(ROLE, event.org_id, actor) as session:
            await session.execute(
                update(context.MetricVersion)
                .where(
                    context.MetricVersion.id == version_id,
                    context.MetricVersion.status == "testing",
                )
                .values(status="broken")
            )
        logger.info("worker-agent | metric version %s does not compile: %s", version_id, error)
        return
    current, _ = (
        await _series(event.org_id, space_id, actor, metric.id, None, version_id)
        if previous_expression is not None
        else ([], None)
    )
    eval_run_id, impact = await _evaluate(
        event,
        space_id,
        actor,
        overlay,
        f"metric_version:{version_id}",
        version_id,
        run_id=uuid.UUID(str(p["eval_run_id"])) if p.get("eval_run_id") else None,
    )
    async with scoped_session(ROLE, event.org_id, actor) as session:
        await session.execute(
            update(context.MetricVersion)
            .where(
                context.MetricVersion.id == version_id, context.MetricVersion.status == "testing"
            )
            .values(status="pending_review")
        )
    pid = await _open(
        event,
        space_id=space_id,
        kind="metric_change",
        target_ref=version_id,
        title=f"Phiên bản {number} của chỉ số {code}",
        diff={
            "metric_id": str(metric.id),
            "metric_code": code,
            "version": number,
            "from_expression": previous_expression,
            "to_expression": expression,
            "preview": {"draft": draft, "in_force": current},
            "impact": impact,
        },
        proposed_by=actor,
        eval_run_id=eval_run_id,
    )
    async with scoped_session(ROLE, event.org_id, actor) as session:
        await session.execute(
            update(context.MetricVersion)
            .where(context.MetricVersion.id == version_id)
            .values(proposal_id=pid)
        )


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
    run, impact = await _evaluate(
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
    run, impact = await _evaluate(
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
        space_id, question, sql = row.data_space_id, row.question, row.sql
    if actor is None:
        return
    outcome = await run_query(
        org_id=event.org_id,
        data_space_id=space_id,
        principal_id=actor,
        caller_kind="eval",
        caller_id=example_id,
        source=_internal_workspace_source(space_id, event.org_id),
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
            "sql": sql,
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
        if kind == "metric_change":
            version = await session.get(context.MetricVersion, ref, with_for_update=True)
            if version is None or version.status != "pending_review":
                return False
            if not accepted:
                version.status = "draft"
                return True
            # BR-26: the version in force is superseded, never edited.
            await session.execute(
                update(context.MetricVersion)
                .where(
                    context.MetricVersion.metric_id == version.metric_id,
                    context.MetricVersion.status == "certified",
                )
                .values(status="superseded", valid_to=now)
            )
            version.status = "certified"
            version.certified_by, version.certified_at, version.valid_from = reviewer, now, now
            await session.execute(
                update(context.Metric)
                .where(context.Metric.id == version.metric_id)
                .values(
                    status="active",
                    current_version=version.version,
                    updated_by=reviewer,
                    updated_at=now,
                )
            )
            return True
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
    PREVIEW: preview_requested,
    SYNONYM: synonym_created,
    RELATIONSHIP: relationship_created,
    EXAMPLE_CREATED: example_changed,
    EXAMPLE_UPDATED: example_changed,
    TERM: term_created,
}
