"""A Data Space's review in one pass: every draft metric version sent to preview, every open
proposal accepted, by the routes the screen uses, as a named user.

Curation is one decision per item: an import approves nothing (semantic_yaml), each synonym, join
and metric version is a proposal, and a seeded space's metrics sit as drafts nobody sent to
preview (DS-TLC's 13 on 24/11, so nothing of it could be certified and the golden tier could not
run). A person still decides (invariant 5): this is that person deciding the lot at once, after
reading the dry run, not a rule deciding for them.

    python -m services.api.modules.inbox.approve_space submit  --org <id> --space <code> --as <user> [--apply]
    python -m services.api.modules.inbox.approve_space approve --org <id> --space <code> --as <user> [--apply] [--kind synonym …]

`submit` calls preview_metric_version for every draft (draft, broken) version that has no open
proposal; the worker then compares it month by month, runs the space's eval set and opens its
metric_change proposal, a minute or so later. `approve` calls decide_proposal with accept for
every open proposal of the space, so every check of the route holds: the caller's approve
capability, maker-checker (the proposer accepts their own only under allow_self_approval), a
second step's compliance capability and other person (BR-119), and an eval run with regressions
is not accepted (BR-28): such a proposal is listed, left for a person to review item by item.
Without --apply both only list what they would do. (`make approve-space`.)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from typing import Any

from fastapi import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import context, inbox
from libs.dataagent_db.models import org as org_models
from services.api.catalogue import allowed
from services.api.kernel.auth import Principal
from services.api.kernel.problems import Problem
from services.api.modules.context.common import ROLE as CONTEXT_ROLE
from services.api.modules.context.routes_semantic import PREVIEWABLE, preview_metric_version
from services.api.modules.inbox.routes import DECIDABLE_PROPOSAL, ROLE, decide_proposal
from services.api.modules.inbox.schemas import ProposalDecision
from services.api.modules.org.public import capabilities_of

NOTE = "Duyệt theo đợt (approve_space) sau khi đọc danh sách chạy thử."


async def _space_and_user(
    session: AsyncSession, org_id: uuid.UUID, space_code: str, username: str
) -> tuple[org_models.DataSpace, org_models.AppUser]:
    space = await session.scalar(
        select(org_models.DataSpace).where(
            org_models.DataSpace.org_id == org_id, org_models.DataSpace.code == space_code
        )
    )
    if space is None:
        raise SystemExit(f"no Data Space {space_code} in organisation {org_id}")
    user = await session.scalar(
        select(org_models.AppUser).where(
            org_models.AppUser.org_id == org_id, org_models.AppUser.username == username
        )
    )
    if user is None:
        raise SystemExit(f"no user {username} in organisation {org_id}")
    return space, user


async def _principal(
    org_id: uuid.UUID, user: org_models.AppUser, space_id: uuid.UUID, method: str, path: str
) -> Principal:
    held = frozenset(await capabilities_of(org_id, user.id, space_id) or ())
    # The route's A2 gate is a dependency of its HTTP request, which a direct call does not
    # make: held here, or this would be a way round it.
    if not allowed(method, path, held):
        raise PermissionError(f"{user.username} may not {method} {path} in this Data Space")
    return Principal(
        user_id=user.id,
        org_id=org_id,
        external_id=user.external_id or "",
        capabilities=held,
        data_space_id=space_id,
    )


async def submit(org_id: uuid.UUID, space_code: str, username: str, apply: bool) -> dict[str, Any]:
    # Each schema by its own role: the open proposals are the inbox's, the versions the context's.
    async with scoped_session(ROLE, org_id, None) as session:
        space, user = await _space_and_user(session, org_id, space_code, username)
        open_for = set(
            (
                await session.scalars(
                    select(inbox.Proposal.target_ref).where(
                        inbox.Proposal.data_space_id == space.id,
                        inbox.Proposal.kind == "metric_change",
                        inbox.Proposal.status.in_(DECIDABLE_PROPOSAL),
                    )
                )
            ).all()
        )
    async with scoped_session(CONTEXT_ROLE, org_id, None) as session:
        rows = (
            await session.execute(
                select(context.Metric.code, context.MetricVersion.id, context.MetricVersion.status)
                .join(context.Metric, context.Metric.id == context.MetricVersion.metric_id)
                .where(
                    context.Metric.data_space_id == space.id,
                    context.MetricVersion.status.in_([s for s in PREVIEWABLE if s != "testing"]),
                )
                .order_by(context.Metric.code)
            )
        ).all()
    todo = [(code, vid, status) for code, vid, status in rows if vid not in open_for]
    out: dict[str, Any] = {"space": space_code, "apply": apply, "drafts": len(todo), "sent": []}
    if not apply or not todo:
        out["would_send"] = [f"{code} ({status})" for code, _, status in todo]
        return out
    principal = await _principal(org_id, user, space.id, "POST", "/v1/metric-versions/{id}:preview")
    for code, vid, _ in todo:
        try:
            await preview_metric_version(vid, principal, space.id, Response())
            out["sent"].append(code)
        except Problem as exc:
            out.setdefault("refused", []).append(f"{code}: {exc.detail}")
    return out


async def approve(
    org_id: uuid.UUID, space_code: str, username: str, apply: bool, kinds: list[str]
) -> dict[str, Any]:
    async with scoped_session(ROLE, org_id, None) as session:
        space, user = await _space_and_user(session, org_id, space_code, username)
        query = (
            select(inbox.Proposal.id, inbox.Proposal.kind, inbox.Proposal.title)
            .where(
                inbox.Proposal.data_space_id == space.id,
                inbox.Proposal.status.in_(DECIDABLE_PROPOSAL),
            )
            .order_by(inbox.Proposal.kind, inbox.Proposal.created_at)
        )
        if kinds:
            query = query.where(inbox.Proposal.kind.in_(kinds))
        proposals = (await session.execute(query)).all()
    out: dict[str, Any] = {"space": space_code, "apply": apply, "open": len(proposals)}
    if not apply or not proposals:
        out["would_accept"] = [f"{kind}: {title}" for _, kind, title in proposals]
        return out
    principal = await _principal(org_id, user, space.id, "POST", "/v1/proposals/{id}:decide")
    accepted: list[str] = []
    left: list[str] = []
    for pid, kind, title in proposals:
        try:
            await decide_proposal(
                ProposalDecision(decision="accept", note=NOTE), pid, principal, space.id
            )
            accepted.append(f"{kind}: {title}")
        except Problem as exc:
            # The route's own refusal (regressions to review, a second step, a lapsed proposal):
            # a person reviews that one on the screen.
            left.append(f"{kind}: {title}: {exc.detail}")
    out.update({"accepted": accepted, "left_for_review": left})
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("mode", choices=("submit", "approve"))
    parser.add_argument("--org", required=True, type=uuid.UUID)
    parser.add_argument("--space", required=True)
    parser.add_argument("--as", dest="username", required=True)
    parser.add_argument("--apply", action="store_true", help="do it; without, list only")
    parser.add_argument("--kind", dest="kinds", action="append", default=[])
    args = parser.parse_args(argv)
    if args.mode == "submit":
        out = asyncio.run(submit(args.org, args.space, args.username, args.apply))
    else:
        out = asyncio.run(approve(args.org, args.space, args.username, args.apply, args.kinds))
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
