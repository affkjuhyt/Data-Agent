"""Whether a dataset's numbers may go out (DF-08, F-02; DATA_PIPELINE_SPEC §2 B, BR-116).

A dataset is held back (`blocked`) while a high-severity rule fails its last check
(catalog.dataset.quality_state, worker_agent/quality.refresh_state) or its contract is `broken`.
A steward who acknowledged every failing high rule (POST /v1/quality-results/{id}:acknowledge)
lets it out for ACK_HOURS with a caveat; a broken contract is not acknowledged away. `warning`
(a lower rule fails) goes out with a caveat; the answer's trust label is not lowered for it.

QUALITY_GATE=warn turns every block into a caveat, the rollback while the gate is new.

Called where numbers leave: the governed answer before it runs (questions.py, no figure on a
block), tiles and scheduled reports through the executor (run_query(quality_gate=True)), the
agent's caveats (agentic/signals.py), a data product's review (reviews.py).
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
ACK_HOURS = 24


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
    """Every active high rule whose last check failed was acknowledged within ACK_HOURS."""
    since = now - datetime.timedelta(hours=ACK_HOURS)
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
        latest = (
            await session.execute(  # type: ignore[attr-defined]
                select(ingest.QualityResult.passed, ingest.QualityResult.acknowledged_at)
                .where(ingest.QualityResult.rule_id == rule_id)
                .order_by(ingest.QualityResult.checked_at.desc())
                .limit(1)
            )
        ).first()
        if latest is None or latest[0]:
            continue
        failing += 1
        if latest[1] is None or latest[1] < since:
            return False
    return failing > 0
