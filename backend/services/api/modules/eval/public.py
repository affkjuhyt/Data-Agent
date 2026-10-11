"""What other api modules may ask the eval module.

ADR-28: modules call each other through public interfaces, not through each other's tables. A
caller in another module (qa) has no grant on schema eval, so it asks here and the answer comes
from svc_eval's own connection.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, or_, select

from libs.dataagent_db.models import eval as evaluation
from services.api.kernel.auth import Principal
from services.api.kernel.db import transaction


async def eval_set_accepts_items(
    principal: Principal, data_space: uuid.UUID, eval_set_id: uuid.UUID
) -> bool:
    """True when the eval set is this organisation's, open, and the Data Space's or org-wide."""
    async with transaction("svc_eval", principal) as session:
        found = await session.scalar(
            select(evaluation.EvalSet.id).where(
                evaluation.EvalSet.id == eval_set_id,
                evaluation.EvalSet.org_id == principal.org_id,
                evaluation.EvalSet.status != "archived",
                or_(
                    evaluation.EvalSet.data_space_id == data_space,
                    evaluation.EvalSet.data_space_id.is_(None),
                ),
            )
        )
    return found is not None


@dataclass(frozen=True)
class EvalSummary:
    """What a data product's page shows of its eval set (SCR-67 Theo dõi)."""

    latest_run: dict[str, Any] | None
    open_error_book_entries: int


async def data_product_eval_summary(
    principal: Principal, eval_set_id: uuid.UUID | None, data_product_id: uuid.UUID
) -> EvalSummary:
    """The latest run of the product's eval set and its open Error Book entries."""
    async with transaction("svc_eval", principal) as session:
        latest = None
        if eval_set_id is not None:
            run = await session.scalar(
                select(evaluation.EvalRun)
                .where(
                    evaluation.EvalRun.eval_set_id == eval_set_id,
                    evaluation.EvalRun.org_id == principal.org_id,
                )
                .order_by(evaluation.EvalRun.started_at.desc(), evaluation.EvalRun.id.desc())
                .limit(1)
            )
            if run is not None:
                latest = {
                    "id": str(run.id),
                    "status": run.status,
                    "item_count": run.item_count,
                    "passed": run.passed,
                    "failed": run.failed,
                    "pass_rate": float(run.pass_rate) if run.pass_rate is not None else None,
                    "started_at": run.started_at.isoformat(),
                    "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                }
        open_entries = await session.scalar(
            select(func.count())
            .select_from(evaluation.ErrorBookEntry)
            .where(
                evaluation.ErrorBookEntry.org_id == principal.org_id,
                evaluation.ErrorBookEntry.data_product_id == data_product_id,
                evaluation.ErrorBookEntry.status == "open",
            )
        )
    return EvalSummary(latest_run=latest, open_error_book_entries=int(open_entries or 0))
