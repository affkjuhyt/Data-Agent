"""Request the nightly eval run of every active context eval set (UC-07-06, the eval-nightly CronJob).

`python -m services.worker_agent.nightly_evals` runs once and exits. It only requests: each run is
an eval.eval_run in status running plus its eval_run.requested event, written in one transaction,
and the worker's eval_runs.run_eval answers it, as it does a run started from the api. The run is
answered as the Data Space's steward, the person accountable for its context.

A set that already had a nightly run started within MIN_GAP is skipped, so a CronJob retried or
started by hand the same night does not run the set twice.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import uuid
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import eval as evaluation
from libs.dataagent_db.session import unscoped_session

ROLE = "svc_eval"
MIN_GAP = datetime.timedelta(hours=20)
# The run's label names the business day it was run for, in the bank's time zone, not UTC's.
ZONE = ZoneInfo(os.environ.get("NIGHTLY_EVAL_TZ", "Asia/Ho_Chi_Minh"))

logger = logging.getLogger(__name__)


async def due_sets() -> list[Any]:
    """Every active context eval set of an active Data Space, across organisations.

    The SECURITY DEFINER eval.nightly_eval_sets() is the one cross-organisation read; everything
    after it runs scoped to the set's organisation."""
    async with unscoped_session(ROLE) as session:
        rows = await session.execute(
            text(
                "SELECT org_id, eval_set_id, data_space_id, steward_id FROM eval.nightly_eval_sets()"
            )
        )
        return list(rows.all())


async def request_run(
    org_id: uuid.UUID,
    eval_set_id: uuid.UUID,
    data_space_id: uuid.UUID,
    steward_id: uuid.UUID,
    now: datetime.datetime,
) -> uuid.UUID | None:
    """The requested run's id, or None when the set already had its nightly run."""
    async with scoped_session(ROLE, org_id, steward_id) as session:
        # Lock the set so two schedulers started together cannot both pass the check below.
        eval_set = await session.scalar(
            select(evaluation.EvalSet)
            .where(evaluation.EvalSet.id == eval_set_id, evaluation.EvalSet.org_id == org_id)
            .with_for_update()
        )
        if eval_set is None:
            return None
        recent = await session.scalar(
            select(evaluation.EvalRun.id).where(
                evaluation.EvalRun.eval_set_id == eval_set_id,
                evaluation.EvalRun.trigger == "nightly",
                evaluation.EvalRun.started_at > now - MIN_GAP,
            )
        )
        if recent is not None:
            return None
        count = await session.scalar(
            select(func.count())
            .select_from(evaluation.EvalItem)
            .where(
                evaluation.EvalItem.eval_set_id == eval_set_id,
                evaluation.EvalItem.status == "active",
            )
        )
        baseline = await session.scalar(
            select(evaluation.EvalRun.id)
            .where(
                evaluation.EvalRun.eval_set_id == eval_set_id,
                evaluation.EvalRun.status == "completed",
            )
            .order_by(evaluation.EvalRun.started_at.desc())
            .limit(1)
        )
        run = evaluation.EvalRun(
            id=uuid7(),
            org_id=org_id,
            eval_set_id=eval_set_id,
            subject_version=f"context@nightly-{now.astimezone(ZONE).date().isoformat()}",
            trigger="nightly",
            item_count=int(count or 0),
            baseline_run_id=baseline,
            status="running",
            started_at=now,
        )
        session.add(run)
        session.add(
            evaluation.Outbox(
                id=uuid7(),
                org_id=org_id,
                aggregate_type="eval_run",
                aggregate_id=run.id,
                event_type="eval_run.requested",
                payload={
                    "eval_run_id": str(run.id),
                    "eval_set_id": str(eval_set_id),
                    "data_space_id": str(data_space_id),
                    "subject_version": run.subject_version,
                    "trigger": "nightly",
                    "requested_by": str(steward_id),
                    "status": "running",
                },
            )
        )
        return run.id


async def request_all(now: datetime.datetime | None = None) -> list[uuid.UUID]:
    now = now or datetime.datetime.now(datetime.UTC)
    requested = []
    for org_id, eval_set_id, data_space_id, steward_id in await due_sets():
        try:
            run_id = await request_run(org_id, eval_set_id, data_space_id, steward_id, now)
        except Exception:
            # One organisation's failure must not cost the others their run.
            logger.exception("nightly-evals | eval set %s failed to request", eval_set_id)
            continue
        if run_id is not None:
            requested.append(run_id)
    logger.info("nightly-evals | %d runs requested", len(requested))
    return requested


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    asyncio.run(request_all())
