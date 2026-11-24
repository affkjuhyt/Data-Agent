"""Whether a dataset's numbers may go out (DF-08, F-02; DATA_PIPELINE_SPEC §2 B, BR-116).

A dataset is held back (`blocked`) while a high-severity rule fails its last check
(catalog.dataset.quality_state, worker_agent/quality.refresh_state) or its contract is `broken`.
A steward who acknowledged every failing high rule (POST /v1/quality-results/{id}:acknowledge)
lets it out with a caveat while that rule keeps failing the way it was acknowledged: the later
checks that fail with no more violations stay acknowledged, up to ack_hours() (QUALITY_ACK_HOURS,
7 days). A check that passes ends it, and one with more violations is a new problem, held again.
Until 24/11 the acknowledgement was the latest result's own, so the next scheduled check (a new
failing row) undid it within the day and the steward acknowledged the same failure every day. A
broken contract is not acknowledged away. `warning`
(a lower rule fails) goes out with a caveat. Either kind of warning caps the answer's trust at
EXPLORATORY (AE-21: libs/contracts/v1/trust.trust's ceiling), on every path that answers.

QUALITY_GATE=warn turns every block into a caveat, the rollback while the gate is new.

Called where numbers leave: the governed answer before it runs (questions.py, no figure on a
block), tiles, scheduled reports and verified queries through the executor
(run_query(quality_gate=True), whose outcome names the warned tables), the agent's caveats
(agentic/signals.py), a data product's review (reviews.py).
"""

from __future__ import annotations

import datetime
import os
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import select

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import catalog, ingest

ROLE = "svc_ingest"  # reads the catalogue (catalog_read) and the rules' results
ACK_HOURS = 24 * 7
# How far back a rule's results are read for the acknowledgement that covers the latest.
ACK_LOOKBACK = 200


def ack_hours() -> float:
    try:
        return float(os.environ.get("QUALITY_ACK_HOURS", ACK_HOURS))
    except ValueError:
        return float(ACK_HOURS)


def blocking() -> bool:
    return os.environ.get("QUALITY_GATE", "block") != "warn"


@dataclass(frozen=True)
class Held:
    dataset_id: uuid.UUID
    name: str
    why: str


@dataclass(frozen=True)
class Gate:
    blocked: list[Held] = field(default_factory=list)
    warnings: list[Held] = field(default_factory=list)

    @property
    def caveats(self) -> list[str]:
        return [f"Bảng {h.name} {h.why}." for h in self.warnings]

    @property
    def message(self) -> str:
        """Why no number goes out, for the asker (names only what they could read)."""
        held = "; ".join(f"bảng {h.name} {h.why}" for h in self.blocked)
        return (
            f"Chưa trả số vì dữ liệu đang bị chặn: {held}. Người phụ trách dữ liệu cần xử lý"
            " hoặc xác nhận trước."
        )


async def check(
    org_id: uuid.UUID,
    dataset_ids: Iterable[uuid.UUID],
    actor: uuid.UUID | None = None,
) -> Gate:
    ids = list(dict.fromkeys(dataset_ids))
    if not ids:
        return Gate()
    now = datetime.datetime.now(datetime.UTC)
    blocked: list[Held] = []
    warnings: list[Held] = []
    async with scoped_session(ROLE, org_id, actor) as session:
        rows = (
            await session.execute(
                select(
                    catalog.Dataset.id,
                    catalog.Dataset.display_name,
                    catalog.Dataset.table_name,
                    catalog.Dataset.quality_state,
                    catalog.DatasetContract.status,
                )
                .outerjoin(
                    catalog.DatasetContract,
                    catalog.DatasetContract.dataset_id == catalog.Dataset.id,
                )
                .where(catalog.Dataset.id.in_(ids))
            )
        ).all()
        for dataset_id, display, table, state, contract in rows:
            name = display or table
            if contract == "broken":
                blocked.append(Held(dataset_id, name, "có hợp đồng dữ liệu đang hỏng"))
            elif state == "blocked":
                if await _acknowledged(session, dataset_id, now):
                    warnings.append(
                        Held(
                            dataset_id,
                            name,
                            "có kiểm tra chất lượng mức cao không đạt, đã được người phụ trách"
                            " xác nhận dùng tiếp",
                        )
                    )
                else:
                    blocked.append(
                        Held(dataset_id, name, "có kiểm tra chất lượng mức cao không đạt")
                    )
            elif state == "warning":
                warnings.append(Held(dataset_id, name, "có kiểm tra chất lượng chưa đạt"))
    if not blocking():
        return Gate([], blocked + warnings)
    return Gate(blocked, warnings)


async def _acknowledged(session: object, dataset_id: uuid.UUID, now: datetime.datetime) -> bool:
    """Every active high rule whose last check failed is covered by an acknowledgement: the most
    recent one since it last passed, within ack_hours(), with no later check finding more."""
    since = now - datetime.timedelta(hours=ack_hours())
    rules = (
        await session.scalars(  # type: ignore[attr-defined]
            select(ingest.QualityRule.id).where(
                ingest.QualityRule.dataset_id == dataset_id,
                ingest.QualityRule.status == "active",
                ingest.QualityRule.severity == "high",
            )
        )
    ).all()
    failing = 0
    for rule_id in rules:
        results = (
            await session.execute(  # type: ignore[attr-defined]
                select(
                    ingest.QualityResult.passed,
                    ingest.QualityResult.acknowledged_at,
                    ingest.QualityResult.violations,
                )
                .where(ingest.QualityResult.rule_id == rule_id)
                .order_by(ingest.QualityResult.checked_at.desc())
                .limit(ACK_LOOKBACK)
            )
        ).all()
        if not results or results[0][0]:
            continue
        failing += 1
        if not _covered(results, since):
            return False
    return failing > 0


def _covered(
    results: Iterable[tuple[bool, datetime.datetime | None, int | None]], since: datetime.datetime
) -> bool:
    """Newest first: walking back over failing checks to the last acknowledgement, which covers
    them unless one found more violations than it did or it is older than `since`."""
    worst = 0
    for passed, acknowledged_at, violations in results:
        if passed:
            return False
        if acknowledged_at is not None:
            return acknowledged_at >= since and worst <= (violations or 0)
        worst = max(worst, violations or 0)
    return False
