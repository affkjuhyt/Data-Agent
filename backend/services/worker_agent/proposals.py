"""Proposals that a module asks the inbox to open, and the decisions that come back.

Opening (svc_inbox): `skill_version.publish_requested` (UC-03-05) and `skill.share_requested`
(UC-03-07) each open one inbox.proposal whose target is the object waiting for it. The proposal's id is derived from the event's, so a
redelivered event opens nothing twice.

Applying: `proposal.decided` (inbox.outbox) is routed by kind to the role that owns the object:
- skill_publish, accepted: the version is published (the one before it retired, as the api does
  for a personal skill), or, for a share, the skill's scope widens; `request_changes` or a
  rejection sends the version back to draft (AC-03-05-02);
- knowledge_page (UC-07-10 step 6, opened from `knowledge_page_version.submitted` with the id the
  api reserved): accepted, the version becomes `reviewed` and the database's trigger serves it
  (the one served before becomes superseded, BR-164); rejected, the version is rejected;
  changes asked for, it is a draft again.
The proposal then ends `applied` (or `apply_failed` with the object gone or moved on). A
proposal the worker does not apply yet is left as the inbox wrote it.

A reviewer may rewrite a proposal's `diff` (accept_with_edit). What is applied is never read from
it: the request is kept in `evidence`, which no decision changes, and a share is checked again
at apply time (it widens, it is not org-wide, it is this skill's).

BR-96 wants an org-wide skill approved by the steward and then the system administrator. A
proposal's second step is the compliance capability's (BR-119) and the table has no column
naming another, so a two-step skill proposal is not opened: the version goes back to testing
(passed, ready to publish) and the share does not happen, rather than one person or the wrong
capability approving it. Data Space scope opens normally.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import context, inbox, pipeline, skill
from services.worker_agent.events import Event, Handler

logger = logging.getLogger(__name__)

PUBLISH = "skill_version.publish_requested"
SHARE = "skill.share_requested"
WRITEBACK = "writeback.submitted"
DECIDED = "proposal.decided"
KNOWLEDGE_SUBMITTED = "knowledge_page_version.submitted"
NAMESPACE = uuid.UUID("6b1f0c55-5f5e-4c49-9a39-2f4f5c1f1d7e")
TRANSITION = datetime.timedelta(days=30)  # BR-97, as services/api/modules/skill/routes.py
RUNNABLE = ("published", "needs_review")
SCOPE_RANK = {"personal": 0, "data_space": 1, "org": 2}  # as services/api/modules/skill/rules.py


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _uuid(value: Any) -> uuid.UUID | None:
    return uuid.UUID(str(value)) if value else None


def proposal_id(event: Event) -> uuid.UUID:
    if event.payload.get("proposal_id"):
        return uuid.UUID(str(event.payload["proposal_id"]))
    return uuid.uuid5(NAMESPACE, str(event.event_id))


def _plain(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        k: str(v)
        if isinstance(v, uuid.UUID)
        else v.isoformat()
        if isinstance(v, datetime.date)
        else v
        for k, v in payload.items()
    }


async def _open(
    event: Event,
    *,
    data_space_id: uuid.UUID,
    kind: str,
    target_service: str,
    target_ref: uuid.UUID,
    title: str,
    diff: dict[str, Any],
    proposed_by: uuid.UUID,
    eval_run_id: uuid.UUID | None,
    impact: dict[str, Any] | None = None,
    impact_level: str = "low",
) -> uuid.UUID:
    pid = proposal_id(event)
    async with scoped_session("svc_inbox", event.org_id, proposed_by) as session:
        opened = await session.execute(
            insert(inbox.Proposal)
            .values(
                id=pid,
                org_id=event.org_id,
                data_space_id=data_space_id,
                kind=kind,
                target_service=target_service,
                target_ref=target_ref,
                title=title,
                diff=_plain(diff),
                # A reviewer can replace `diff` (accept_with_edit) but not `evidence`: the request as
                # the module made it is kept here, and only this is ever applied.
                evidence=[{"requested": _plain(diff)}],
                proposed_by=proposed_by,
                eval_run_id=eval_run_id,
                # Every caller has run its checks and the standard set before it opens the proposal,
                # so it waits for a reviewer. Left at the column's default 'open', the review page
                # (ProposalPage.tsx), which decides only pending_review, offered no decision at all.
                status="pending_review",
                # What the change reaches, counted when it is proposed (metric_changes.py).
                impact=_plain(impact or {}),
                impact_level=impact_level,
                created_by=proposed_by,
            )
            .on_conflict_do_nothing(index_elements=["id"])
            .returning(inbox.Proposal.id)
        )
        if opened.scalar() is not None:
            _review_request(
                event, pid, data_space_id, kind, title, proposed_by, impact_level, session
            )
    return pid


def _review_request(
    event: Event,
    pid: uuid.UUID,
    data_space_id: uuid.UUID,
    kind: str,
    title: str,
    proposed_by: uuid.UUID,
    impact_level: str,
    session: Any,
) -> None:
    """The approve_proposal request that puts the proposal in its reviewers' Cần bạn.

    Cần bạn lists HITL requests (GET /v1/inbox), and a proposal reaches it only through one whose
    source_ref is the proposal (frontend decision.ts opens the proposal from it). Without it no
    reviewer ever saw a metric draft or a relationship. Anyone of the space holding `approve`
    decides, except the proposer, as :decide already refuses; deciding closes it (inbox routes)."""
    request_id = uuid7()
    session.add(
        inbox.HitlRequest(
            id=request_id,
            org_id=event.org_id,
            data_space_id=data_space_id,
            kind="approve_proposal",
            risk=impact_level,
            title=title,
            context={"proposal_kind": kind, "excluded_approver_ids": [str(proposed_by)]},
            options=[],
            source_service="inbox",
            source_ref=pid,
            assignee_capability="approve",
            created_by=proposed_by,
            updated_by=proposed_by,
        )
    )
    session.add(
        inbox.Outbox(
            id=uuid7(),
            org_id=event.org_id,
            aggregate_type="hitl_request",
            aggregate_id=request_id,
            event_type="hitl.opened",
            payload={
                "hitl_request_id": str(request_id),
                "data_space_id": str(data_space_id),
                "kind": "approve_proposal",
                "risk": impact_level,
                "source_service": "inbox",
            },
        )
    )


async def _skill_back(event: Event, version_id: uuid.UUID, actor: uuid.UUID, status: str) -> None:
    async with scoped_session("svc_skill", event.org_id, actor) as session:
        await session.execute(
            update(skill.SkillVersion)
            .where(
                skill.SkillVersion.id == version_id, skill.SkillVersion.status == "pending_review"
            )
            .values(status=status)
        )


async def publish_requested(event: Event) -> None:
    p = event.payload
    version_id = uuid.UUID(str(p["skill_version_id"]))
    actor = uuid.UUID(str(p["requested_by"]))
    if int(p.get("approval_steps") or 1) > 1:
        logger.warning(
            "worker-agent | skill version %s needs a 2-step proposal (BR-96)", version_id
        )
        await _skill_back(event, version_id, actor, "testing")
        return
    await _open(
        event,
        data_space_id=uuid.UUID(str(p["data_space_id"])),
        kind="skill_publish",
        target_service="skill",
        target_ref=version_id,
        title=f"Phát hành kỹ năng phiên bản {p.get('version')}",
        diff={
            "action": "publish",
            "skill_id": p["skill_id"],
            "version": p.get("version"),
            "scope": p.get("scope"),
        },
        proposed_by=actor,
        eval_run_id=_uuid(p.get("eval_run_id")),
    )


async def share_requested(event: Event) -> None:
    p = event.payload
    if int(p.get("approval_steps") or 1) > 1:
        logger.warning(
            "worker-agent | skill %s share needs a 2-step proposal (BR-96)", p["skill_id"]
        )
        return
    await _open(
        event,
        data_space_id=uuid.UUID(str(p["data_space_id"])),
        kind="skill_publish",
        target_service="skill",
        target_ref=uuid.UUID(str(p["skill_version_id"])),
        title=f"Chia sẻ kỹ năng ra phạm vi {p['scope']}",
        diff={
            "action": "share",
            "skill_id": p["skill_id"],
            "previous_scope": p.get("previous_scope"),
            "scope": p["scope"],
        },
        proposed_by=uuid.UUID(str(p["requested_by"])),
        eval_run_id=_uuid(p.get("eval_run_id")),
    )


async def writeback_submitted(event: Event) -> None:
    """Open the real inbox proposal after the pipeline transaction submits a writeback."""
    p = event.payload
    writeback_id = uuid.UUID(str(p["writeback_request_id"]))
    actor = uuid.UUID(str(p["requested_by"]))
    await _open(
        event,
        data_space_id=uuid.UUID(str(p["data_space_id"])),
        kind="writeback",
        target_service="pipeline",
        target_ref=writeback_id,
        title=f"Duyệt ghi ngược vào {p.get('target_table') or 'bảng đích'}",
        diff={
            "source_id": p.get("source_id"),
            "target_table": p.get("target_table"),
            "from_dataset_id": p.get("from_dataset_id"),
            "diff_summary": p.get("diff_summary") or {},
        },
        proposed_by=actor,
        eval_run_id=None,
    )


# ---------------------------------------------------------------- decisions


def _skill_event(
    org: uuid.UUID, row_id: uuid.UUID, event_type: str, **payload: Any
) -> skill.Outbox:
    return skill.Outbox(
        id=uuid7(),
        org_id=org,
        aggregate_type="skill_version" if "skill_version_id" in payload else "skill",
        aggregate_id=row_id,
        event_type=event_type,
        payload=_plain(payload),
    )


def _requested(proposal: dict[str, Any]) -> dict[str, Any]:
    """The request the module made, from `evidence`; never the editable `diff`."""
    for item in proposal.get("evidence") or []:
        if isinstance(item, dict) and isinstance(item.get("requested"), dict):
            return dict(item["requested"])
    return {}


async def _apply_skill(event: Event, proposal: dict[str, Any]) -> bool:
    """True when the decision changed the skill, False when its object had moved on."""
    org, reviewer = event.org_id, uuid.UUID(str(event.payload["reviewer_id"]))
    version_id = uuid.UUID(str(proposal["target_ref"]))
    diff = _requested(proposal)
    accepted = event.payload.get("status") == "accepted"
    async with scoped_session("svc_skill", org, reviewer) as session:
        version = await session.get(skill.SkillVersion, version_id, with_for_update=True)
        if version is None:
            return False
        row = await session.get(skill.Skill, version.skill_id, with_for_update=True)
        if row is None:
            return False
        if diff.get("action") == "share":
            if not accepted:
                return True  # the scope simply stays as it is
            scope = str(diff.get("scope") or "")
            if (
                version.status not in RUNNABLE
                or str(diff.get("skill_id")) != str(row.id)
                or SCOPE_RANK.get(scope, -1) <= SCOPE_RANK.get(row.scope, 99)
                # BR-96: org-wide needs a second, administrator step this proposal never had.
                or scope == "org"
            ):
                return False
            previous, row.scope = row.scope, scope
            row.updated_by, row.updated_at = reviewer, _now()
            session.add(
                _skill_event(
                    org,
                    row.id,
                    "skill.shared",
                    skill_id=row.id,
                    previous_scope=previous,
                    scope=row.scope,
                    data_space_id=row.data_space_id,
                )
            )
            return True
        if version.status != "pending_review" or diff.get("action") != "publish":
            return False
        if not accepted:
            # Rejected, or changes asked for: back to the author as a draft (AC-03-05-02).
            version.status = "draft"
            session.add(
                _skill_event(
                    org,
                    version.id,
                    "skill_version.returned",
                    skill_version_id=version.id,
                    skill_id=row.id,
                    decision=event.payload.get("decision"),
                )
            )
            return True
        now = _now()
        old = (
            await session.scalars(
                select(skill.SkillVersion)
                .where(
                    skill.SkillVersion.skill_id == row.id,
                    skill.SkillVersion.status.in_(RUNNABLE),
                    skill.SkillVersion.id != version.id,
                )
                .with_for_update()
            )
        ).all()
        for before in old:
            before.status, before.retire_at = "retired", now + TRANSITION
            session.add(
                _skill_event(
                    org,
                    before.id,
                    "skill_version.retired",
                    skill_version_id=before.id,
                    skill_id=row.id,
                    version=before.version,
                    retire_at=before.retire_at,
                    status="retired",
                )
            )
        # The one-published-version index must see the old rows retired first.
        await session.flush()
        version.status = "published"
        version.approved_by, version.approved_at = reviewer, now
        row.current_version = version.version
        row.updated_by, row.updated_at = reviewer, now
        session.add(
            _skill_event(
                org,
                version.id,
                "skill.published",
                skill_version_id=version.id,
                skill_id=row.id,
                version=version.version,
                scope=row.scope,
                data_space_id=row.data_space_id,
                status="published",
            )
        )
        return True


async def _apply_writeback(event: Event, proposal: dict[str, Any]) -> bool:
    """Reflect the inbox decision back into the pipeline aggregate."""
    reviewer = uuid.UUID(str(event.payload["reviewer_id"]))
    status = event.payload.get("status")
    next_status = (
        "approved" if status == "accepted" else "rejected" if status == "rejected" else "previewed"
    )
    async with scoped_session("svc_pipeline", event.org_id, reviewer) as session:
        row = await session.get(
            pipeline.WritebackRequest,
            uuid.UUID(str(proposal["target_ref"])),
            with_for_update=True,
        )
        if row is None or row.status != "submitted":
            return False
        row.status = next_status
        row.approved_by = reviewer if next_status == "approved" else None
        row.updated_by, row.updated_at = reviewer, _now()
        return True


async def knowledge_page_submitted(event: Event) -> None:
    """Opens the knowledge_page proposal for the page owner (UC-07-10 step 6)."""
    payload = event.payload
    version_id = uuid.UUID(str(payload["version_id"]))
    await _open(
        event,
        data_space_id=uuid.UUID(str(payload["data_space_id"])),
        kind="knowledge_page",
        target_service="context",
        target_ref=version_id,
        title=f"Trang tri thức · phiên bản {payload.get('version')}",
        diff={
            "page_id": payload.get("page_id"),
            "version_id": version_id,
            "version": payload.get("version"),
            "origin": payload.get("origin"),
            "change_size": payload.get("change_size"),
        },
        proposed_by=uuid.UUID(str(payload["created_by"])),
        eval_run_id=None,
    )


async def _apply_knowledge(event: Event, proposal: dict[str, Any]) -> bool:
    reviewer = uuid.UUID(str(event.payload["reviewer_id"]))
    decision = event.payload.get("status")
    version_id = uuid.UUID(str(proposal["target_ref"]))
    if decision == "accepted":
        # The trigger serves the version, supersedes the one before and recomputes the page's
        # policy in this same transaction (routines/context.sql); BR-164 refuses a self-review.
        values: dict[str, Any] = {
            "status": "reviewed",
            "reviewed_by": reviewer,
            "reviewed_at": _now(),
        }
    elif decision == "rejected":
        values = {"status": "rejected"}
    else:
        values = {"status": "draft"}
    async with scoped_session("svc_context", event.org_id, reviewer) as session:
        result = await session.execute(
            update(context.KnowledgePageVersion)
            .where(
                context.KnowledgePageVersion.id == version_id,
                context.KnowledgePageVersion.status == "pending_review",
            )
            .values(**values)
        )
        return result.rowcount == 1


async def _proposal(event: Event) -> dict[str, Any] | None:
    async with scoped_session("svc_inbox", event.org_id) as session:
        row = await session.get(inbox.Proposal, uuid.UUID(str(event.payload["proposal_id"])))
        if row is None:
            return None
        return {
            "id": row.id,
            "kind": row.kind,
            "status": row.status,
            "target_ref": row.target_ref,
            "evidence": row.evidence,
            "data_space_id": row.data_space_id,
            "eval_run_id": row.eval_run_id,
        }


async def _finish(event: Event, pid: uuid.UUID, status: str) -> None:
    async with scoped_session("svc_inbox", event.org_id) as session:
        await session.execute(
            update(inbox.Proposal)
            .where(
                inbox.Proposal.id == pid,
                inbox.Proposal.status.in_(("accepted", "open", "rejected")),
            )
            .values(
                status=status, applied_at=_now() if status == "applied" else None, updated_at=_now()
            )
        )


async def decided(event: Event) -> None:
    from services.worker_agent import reviews, semantic_review

    proposal = await _proposal(event)
    applied_here = (
        "skill_publish",
        "writeback",
        "metric_change",
        "knowledge_page",
        *semantic_review.KINDS,
        *reviews.KINDS,
    )
    if proposal is None or proposal["kind"] not in applied_here:
        return
    if proposal["status"] not in ("accepted", "open", "rejected"):
        return  # already applied, or no longer waiting
    outcome: bool
    if proposal["kind"] == "metric_change":
        # metric_changes checks again what the version would break before it certifies it;
        # imported here because it opens its proposals through this module.
        from services.worker_agent import metric_changes

        outcome = await metric_changes.apply(event, proposal)
    elif proposal["kind"] in semantic_review.KINDS:
        outcome = await semantic_review.apply(event, proposal)
    elif proposal["kind"] in reviews.KINDS:
        outcome = await reviews.apply(event, proposal)
    elif proposal["kind"] == "skill_publish":
        outcome = await _apply_skill(event, proposal)
    elif proposal["kind"] == "knowledge_page":
        outcome = await _apply_knowledge(event, proposal)
    else:
        outcome = await _apply_writeback(event, proposal)
    accepted = event.payload.get("status") == "accepted"
    if accepted:
        await _finish(event, proposal["id"], "applied" if outcome else "apply_failed")
    elif proposal["status"] == "open" and outcome:
        # Changes asked for: the version is a draft again and a resubmission opens a new proposal.
        await _finish(event, proposal["id"], "withdrawn")


HANDLERS: dict[str, Handler] = {
    PUBLISH: publish_requested,
    SHARE: share_requested,
    WRITEBACK: writeback_submitted,
    DECIDED: decided,
    KNOWLEDGE_SUBMITTED: knowledge_page_submitted,
}
