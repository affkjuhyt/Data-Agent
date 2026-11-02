"""What the reader should know about the data behind an answer, read from the catalogue, never
from the model (RES-07, after OpenMetadata's AI context envelope): for each source table a
governed figure was read from,

* its quality: the latest result of each of its active rules (catalog.dataset.quality_state):
  `blocked` when a high-severity rule fails, `warning` for any other failing rule;
* its freshness against the data product it belongs to (data_product_member.freshness_state):
  `overdue` or `missing` means the data is not in time, which BR-178 does not let an answer state
  a figure from;
* whether its structure moved after the metric version was certified (the current
  dataset_version's valid_from later than the version's certified_at): the definition was checked
  against a table that is no longer quite the one read.

Each signal becomes a caveat in plain words; a table out of time hands the turn to the governed
path, which masks its figures (BR-178).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import catalog, context
from services.worker_agent.agentic.tools.base import ToolContext

ROLE = "svc_qa"


@dataclass
class Signals:
    caveats: list[str] = field(default_factory=list)
    # Tables whose data is not in time: no figure of theirs may be stated (BR-178).
    out_of_time: list[str] = field(default_factory=list)


async def read(ctx: ToolContext) -> Signals:
    found = Signals()
    sources: dict[uuid.UUID, set[uuid.UUID]] = {}  # catalog dataset → metric ids read from it
    for ds in ctx.ws.datasets.values():
        metric_id = (ds.reading or {}).get("metric_id")
        for source in ds.sources:
            sources.setdefault(uuid.UUID(source), set()).update(
                {uuid.UUID(metric_id)} if metric_id else set()
            )
    if not sources:
        return found
    names = {
        i: (ctx.layer.datasets[i].display_name or ctx.layer.datasets[i].table_name)
        if i in ctx.layer.datasets
        else str(i)
        for i in sources
    }
    for i in sources:
        state = getattr(ctx.layer.datasets.get(i), "quality_state", None)
        if state == "blocked":
            found.caveats.append(
                f"Bảng {names[i]} đang có kiểm tra chất lượng mức cao không đạt: số liệu từ bảng"
                " này cần được kiểm lại trước khi dùng."
            )
        elif state == "warning":
            found.caveats.append(f"Bảng {names[i]} có kiểm tra chất lượng chưa đạt.")
    async with scoped_session(ROLE, ctx.org_id, ctx.user_id) as session:
        members = (
            await session.scalars(
                select(context.DataProductMember).where(
                    context.DataProductMember.member_kind == "dataset",
                    context.DataProductMember.member_ref.in_(list(sources)),
                )
            )
        ).all()
        for m in members:
            if m.freshness_state in ("overdue", "missing"):
                found.out_of_time.append(names.get(m.member_ref, str(m.member_ref)))
        current = (
            await session.scalars(
                select(catalog.DatasetVersion).where(
                    catalog.DatasetVersion.dataset_id.in_(list(sources)),
                    catalog.DatasetVersion.valid_to.is_(None),
                )
            )
        ).all()
    for v in current:
        for metric_id in sources.get(v.dataset_id, set()):
            row = ctx.layer.version_rows.get(metric_id)
            certified = getattr(row, "certified_at", None)
            if certified is not None and v.valid_from > certified and v.version > 1:
                found.caveats.append(
                    f"Bảng {names[v.dataset_id]} đã đổi cấu trúc sau khi chỉ số được chứng nhận"
                    f" (phiên bản bảng {v.version}): định nghĩa nên được người phụ trách xem lại."
                )
    found.caveats = list(dict.fromkeys(found.caveats))
    found.out_of_time = list(dict.fromkeys(found.out_of_time))
    return found
