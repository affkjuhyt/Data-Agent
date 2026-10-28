"""The before/after evidence of a semantic proposal (UC-07-03), and of a data product (UC-07-13).

A run started from the api or the nightly schedule (eval_run.requested) is eval_runs.py's, through
the production question path. This module is called directly by the reviews that need a layer as
a proposal would leave it (semantic_review.evaluate_overlay, reviews.product_submitted).

An eval run asks every active item of its set the way a question is answered: the Data Space's
semantic layer, the rule Understander, the compiler, the query run in place (caller_kind "eval").
Nothing is written to qa: an item is scored against what it expects, and the result row says why
it failed (understanding, value, behaviour). A run with an `Overlay` asks against the layer as a
proposal would leave it, a draft metric version or a pending synonym counted as active, and is
compared with the set's last completed run: an item that passed there and fails here is a
regression, the number AC-07-03-06 holds a reviewer to.

Expected values (eval.eval_item.expected):
- "behavior": "refuse" or "clarify": the question must not be answered with a number;
- "metric": the metric code the question must be read as;
- "value": the number, within the item's tolerance (absolute), else 0.5 % of the value.

The Understander here is the rule one, without the model reader: a run must give the same
answer twice, and a regression must come from the change under review, not from sampling.
"""

from __future__ import annotations

import datetime
import decimal
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select, update

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import catalog
from libs.dataagent_db.models import eval as ev
from libs.sql_guard import SqlRejected
from services.worker_agent.query.executor import run_query
from services.worker_agent.semantic import compiler as c
from services.worker_agent.semantic.loader import Overlay, load
from services.worker_agent.understand.resolver import understand

logger = logging.getLogger(__name__)

ROLE = "svc_eval"
RELATIVE_TOLERANCE = decimal.Decimal("0.005")


@dataclass
class Asked:
    """What the answering path made of one question."""

    decision: str  # proceed, clarify, unknown, refused, failed
    metric_code: str | None = None
    value: decimal.Decimal | None = None
    unit: str | None = None
    rows: int = 0
    query_run_id: uuid.UUID | None = None
    error: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "metric": self.metric_code,
            "value": None if self.value is None else str(self.value),
            "unit": self.unit,
            "rows": self.rows,
            "query_run_id": str(self.query_run_id) if self.query_run_id else None,
            "error": self.error,
        }


@dataclass
class Summary:
    item_count: int = 0
    passed: int = 0
    failed: int = 0
    correct_refusals: int = 0
    regressions: int = 0
    changed: list[uuid.UUID] = field(default_factory=list)

    @property
    def pass_rate(self) -> decimal.Decimal | None:
        if not self.item_count:
            return None
        return (decimal.Decimal(self.passed) / self.item_count).quantize(decimal.Decimal("0.0001"))


def _number(value: Any) -> decimal.Decimal | None:
    try:
        return None if value is None else decimal.Decimal(str(value))
    except decimal.InvalidOperation:
        return None


async def ask(
    *,
    org_id: uuid.UUID,
    space_id: uuid.UUID,
    user_id: uuid.UUID,
    question: str,
    as_of: datetime.date | None,
    caller_id: uuid.UUID,
    overlay: Overlay | None = None,
) -> Asked:
    # The question path's own pieces: the same source rules decide where the SQL runs.
    from services.worker_agent.questions import (
        QUERYABLE,
        _internal_workspace_source,
        dialect_for_source,
        secrets,
    )

    async with scoped_session("svc_qa", org_id, user_id) as session:
        layer = await load(session, space_id, overlay)
        u = understand(question, layer.vocabulary, today=as_of or datetime.date.today())
        if u.decision != "proceed" or u.metric is None:
            return Asked(u.decision)
        version = layer.versions[u.metric.metric_id]
        request = c.Request(
            version,
            group_by=u.group_by,
            filters=u.filters,
            period=c.Period(u.period.start, u.period.end) if u.period else None,
        )
        try:
            compiled = c.compile_metric(request, layer.layer, dialect="postgres")
        except (c.CompileError, SqlRejected) as exc:
            return Asked("refused", u.metric.code, error=str(exc))
        dataset = layer.datasets[compiled.dataset_ids[0]]
        source = (
            _internal_workspace_source(space_id, org_id)
            if dataset.schema_name == "workspace" and dataset.source_id is None
            else await session.get(catalog.Source, dataset.source_id)
        )
        if source is None or source.mode != "federated" or source.status not in QUERYABLE:
            return Asked(
                "refused", u.metric.code, error="the metric's source is not queryable in place"
            )
        try:
            compiled = c.compile_metric(
                request, layer.layer, dialect=dialect_for_source(source.kind)
            )
        except (c.CompileError, SqlRejected) as exc:
            return Asked("refused", u.metric.code, error=str(exc))
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
        as_of=as_of,
    )
    if not outcome.ok:
        return Asked(
            "failed", u.metric.code, query_run_id=outcome.query_run_id, error=outcome.error
        )
    names = [col["name"] for col in outcome.columns]
    value = None
    if len(outcome.rows) == 1 and "value" in names:
        value = _number(outcome.rows[0][names.index("value")])
    return Asked(
        "proceed",
        u.metric.code,
        value,
        version.unit,
        len(outcome.rows),
        outcome.query_run_id,
    )


def score(
    expected: dict[str, Any], tolerance: decimal.Decimal | None, asked: Asked
) -> tuple[bool | None, bool | None, bool | None, bool, str | None]:
    """(understanding_ok, value_ok, behavior_ok, passed, cause)."""
    behavior = expected.get("behavior")
    if behavior in ("refuse", "clarify"):
        ok = asked.decision != "proceed" and (behavior != "clarify" or asked.decision == "clarify")
        return None, None, ok, ok, None if ok else f"expected to {behavior}, answered instead"
    understanding = None
    if expected.get("metric"):
        understanding = asked.metric_code == expected["metric"]
    value_ok = None
    want = _number(expected.get("value"))
    if want is not None:
        if asked.value is None:
            value_ok = False
        else:
            allowed = tolerance if tolerance is not None else abs(want) * RELATIVE_TOLERANCE
            value_ok = abs(asked.value - want) <= allowed
    answered = asked.decision == "proceed"
    passed = answered and understanding is not False and value_ok is not False
    cause = None
    if not answered:
        cause = asked.error or f"not answered ({asked.decision})"
    elif understanding is False:
        cause = f"read as {asked.metric_code}, expected {expected.get('metric')}"
    elif value_ok is False:
        cause = f"value {asked.value} instead of {want}"
    return understanding, value_ok, answered, passed, cause


async def run_set(
    *,
    org_id: uuid.UUID,
    space_id: uuid.UUID,
    run_id: uuid.UUID,
    user_id: uuid.UUID,
    overlay: Overlay | None = None,
) -> Summary:
    """Ask every active item of the run's set and record the results on the run."""
    async with scoped_session(ROLE, org_id, user_id) as session:
        run = await session.get(ev.EvalRun, run_id)
        if run is None or run.status != "running":
            return Summary()
        items = list(
            (
                await session.scalars(
                    select(ev.EvalItem)
                    .where(
                        ev.EvalItem.eval_set_id == run.eval_set_id, ev.EvalItem.status == "active"
                    )
                    .order_by(ev.EvalItem.created_at, ev.EvalItem.id)
                )
            ).all()
        )
        baseline: dict[uuid.UUID, bool] = {}
        if run.baseline_run_id is not None:
            found = await session.execute(
                select(ev.EvalResult.eval_item_id, ev.EvalResult.passed).where(
                    ev.EvalResult.eval_run_id == run.baseline_run_id
                )
            )
            baseline = dict(row.tuple() for row in found.all())
    summary = Summary()
    results: list[ev.EvalResult] = []
    for item in items:
        question = str((item.input or {}).get("question") or "").strip()
        asked = (
            await ask(
                org_id=org_id,
                space_id=space_id,
                user_id=user_id,
                question=question,
                as_of=item.as_of,
                caller_id=run_id,
                overlay=overlay,
            )
            if question
            else Asked("failed", error="the item has no question")
        )
        understanding, value_ok, behavior, passed, cause = score(
            dict(item.expected or {}), item.tolerance, asked
        )
        changed = item.id in baseline and baseline[item.id] != passed
        summary.item_count += 1
        summary.passed += passed
        summary.failed += not passed
        summary.correct_refusals += bool(
            passed and (item.expected or {}).get("behavior") == "refuse"
        )
        summary.regressions += bool(changed and not passed)
        if changed:
            summary.changed.append(item.id)
        results.append(
            ev.EvalResult(
                id=uuid7(),
                org_id=org_id,
                eval_run_id=run_id,
                eval_item_id=item.id,
                understanding_ok=understanding,
                value_ok=value_ok,
                behavior_ok=behavior,
                passed=passed,
                actual=asked.as_json(),
                cause=cause,
                changed_from_baseline=changed,
            )
        )
    async with scoped_session(ROLE, org_id, user_id) as session:
        session.add_all(results)
        await session.execute(
            update(ev.EvalRun)
            .where(ev.EvalRun.id == run_id, ev.EvalRun.status == "running")
            .values(
                item_count=summary.item_count,
                passed=summary.passed,
                failed=summary.failed,
                correct_refusals=summary.correct_refusals,
                pass_rate=summary.pass_rate,
                regressions=summary.regressions,
                status="completed",
                finished_at=func.now(),
            )
        )
    logger.info(
        "worker-agent | eval run %s: %d/%d passed, %d regressions",
        run_id,
        summary.passed,
        summary.item_count,
        summary.regressions,
    )
    return summary


async def start_run(
    *,
    org_id: uuid.UUID,
    space_id: uuid.UUID,
    user_id: uuid.UUID,
    trigger: str,
    trigger_ref: uuid.UUID | None,
    subject_version: str,
    run_id: uuid.UUID | None = None,
    eval_set_id: uuid.UUID | None = None,
) -> uuid.UUID | None:
    """A running eval_run on `eval_set_id`, else on the Data Space's active context set; None
    when there is no such set."""
    async with scoped_session(ROLE, org_id, user_id) as session:
        query = select(ev.EvalSet).where(ev.EvalSet.status == "active")
        if eval_set_id is not None:
            query = query.where(ev.EvalSet.id == eval_set_id)
        else:
            query = query.where(
                ev.EvalSet.data_space_id == space_id, ev.EvalSet.subject_type == "context"
            )
        eval_set = await session.scalar(query.order_by(ev.EvalSet.created_at).limit(1))
        if eval_set is None:
            return None
        baseline = await session.scalar(
            select(ev.EvalRun.id)
            .where(ev.EvalRun.eval_set_id == eval_set.id, ev.EvalRun.status == "completed")
            .order_by(ev.EvalRun.started_at.desc())
            .limit(1)
        )
        run = ev.EvalRun(
            id=run_id or uuid7(),
            org_id=org_id,
            eval_set_id=eval_set.id,
            subject_version=subject_version,
            trigger=trigger,
            trigger_ref=trigger_ref,
            baseline_run_id=baseline,
            status="running",
        )
        session.add(run)
        return run.id
