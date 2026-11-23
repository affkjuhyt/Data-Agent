"""What the reader should know about the data behind an answer, read from the catalogue, never
from the model (RES-07, after OpenMetadata's AI context envelope): for each source table a
governed figure was read from,

* its quality, as the one gate every path asks (worker_agent/quality_gate, DF-08): a table held
  back (a high-severity rule failing, unacknowledged, or a broken contract) hands the turn to the
  governed path, which refuses with QUALITY_BLOCKED; a failing lower rule or an acknowledged block
  is a caveat;
* its freshness against the data product it belongs to (data_product_member.freshness_state):
  `overdue` or `missing` means the data is not in time, which BR-178 does not let an answer state
  a figure from;
* whether its structure moved after the metric version was certified (the current
  dataset_version's valid_from later than the version's certified_at): the definition was checked
  against a table that is no longer quite the one read.

Each signal becomes a caveat in plain words; a table out of time hands the turn to the governed
path, which masks its figures (BR-178). AE-21: a table read with a quality warning does not only
add a caveat, it caps the answer's trust at EXPLORATORY (engine.persist).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import catalog, context
from services.worker_agent import quality_gate
from services.worker_agent.agentic.tools.base import ToolContext

ROLE = "svc_qa"


@dataclass
class Signals:
    caveats: list[str] = field(default_factory=list)
    # Tables whose data is not in time: no figure of theirs may be stated (BR-178).
    out_of_time: list[str] = field(default_factory=list)
    # AE-21: tables read with a quality warning; the answer is at most EXPLORATORY for them.
    quality_warned: list[str] = field(default_factory=list)


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
    # DF-08: one gate for every path (worker_agent/quality_gate). A held-back table is not
    # stated from: the turn goes to the governed path, which refuses with QUALITY_BLOCKED and
    # why; a failing lower rule or an acknowledged block is a caveat.
    held = await quality_gate.check(ctx.org_id, list(sources), ctx.user_id)
    for h in held.blocked:
        found.out_of_time.append(names.get(h.dataset_id, h.name))
    found.caveats.extend(held.caveats)
    found.quality_warned.extend(names.get(h.dataset_id, h.name) for h in held.warnings)
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
    found.quality_warned = list(dict.fromkeys(found.quality_warned))
    return found
