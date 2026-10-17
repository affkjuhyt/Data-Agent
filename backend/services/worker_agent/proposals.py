"""Proposals that a module asks the inbox to open, and the decisions that come back.

Opening (svc_inbox): `skill_version.publish_requested` (UC-03-05), `skill.share_requested`
(UC-03-07) and `pack_install.activation_requested` (UC-09-02 step 6) each open one inbox.proposal
whose target is the object waiting for it. The proposal's id is derived from the event's, so a
redelivered event opens nothing twice.

Applying: `proposal.decided` (inbox.outbox) is routed by kind to the role that owns the object:
- skill_publish, accepted: the version is published (the one before it retired, as the api does
  for a personal skill), or, for a share, the skill's scope widens; `request_changes` or a
  rejection sends the version back to draft (AC-03-05-02);
- pack_activate, accepted: the install becomes active, unless a period of its Data Space is
  closing (AC-09-02-05): it then stays accepted and is activated by `activate_waiting` once the
  close ends; rejected, it goes back to calibrating.
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
from libs.dataagent_db.models import inbox, pack, period, pipeline, skill
from services.worker_agent.events import Event, Handler

logger = logging.getLogger(__name__)

PUBLISH = "skill_version.publish_requested"
SHARE = "skill.share_requested"
ACTIVATE = "pack_install.activation_requested"
WRITEBACK = "writeback.submitted"
DECIDED = "proposal.decided"
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
) -> uuid.UUID:
    pid = proposal_id(event)
    async with scoped_session("svc_inbox", event.org_id, proposed_by) as session:
        await session.execute(
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
                created_by=proposed_by,
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )
    return pid


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


async def activation_requested(event: Event) -> None:
    p = event.payload
    install_id = uuid.UUID(str(p["pack_install_id"]))
    actor = uuid.UUID(str(p["requested_by"]))
    pid = await _open(
        event,
        data_space_id=uuid.UUID(str(p["data_space_id"])),
        kind="pack_activate",
        target_service="pack",
        target_ref=install_id,
        title="Kích hoạt gói ngành",
        diff={
            "pack_version_id": p["pack_version_id"],
            "calibration_run_id": p.get("calibration_run_id"),
        },
        proposed_by=actor,
        eval_run_id=None,
    )
    async with scoped_session("svc_pack", event.org_id, actor) as session:
        await session.execute(
            update(pack.PackInstall)
            .where(pack.PackInstall.id == install_id, pack.PackInstall.proposal_id.is_(None))
            .values(proposal_id=pid)
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


async def _closing(org: uuid.UUID, space: uuid.UUID, actor: uuid.UUID) -> bool:
    # svc_pack reads no period table; svc_query does (period_read), read-only.
    async with scoped_session("svc_query", org, actor) as session:
        return (
            await session.scalar(
                select(period.PeriodClose.id)
                .where(
                    period.PeriodClose.data_space_id == space,
                    period.PeriodClose.status == "closing",
                )
                .limit(1)
            )
        ) is not None


async def _activate(
    org: uuid.UUID, install: pack.PackInstall, actor: uuid.UUID, session: Any
) -> None:
    install.status = "active"
    install.activated_at, install.activated_by = _now(), actor
    install.updated_at, install.updated_by = _now(), actor
    session.add(
        pack.Outbox(
            id=uuid7(),
            org_id=org,
            aggregate_type="pack_install",
            aggregate_id=install.id,
            event_type="pack_install.activated",
            payload=_plain(
                {
                    "pack_install_id": install.id,
                    "data_space_id": install.data_space_id,
                    "pack_version_id": install.pack_version_id,
                    "status": "active",
                }
            ),
        )
    )


async def _apply_pack(event: Event, proposal: dict[str, Any]) -> bool | None:
    """True applied, False moved on, None deferred (a period is closing)."""
    org, reviewer = event.org_id, uuid.UUID(str(event.payload["reviewer_id"]))
    accepted = event.payload.get("status") == "accepted"
    install_id = uuid.UUID(str(proposal["target_ref"]))
    if accepted and await _closing(org, proposal["data_space_id"], reviewer):
        return None
    async with scoped_session("svc_pack", org, reviewer) as session:
        install = await session.get(pack.PackInstall, install_id, with_for_update=True)
        if install is None or install.status != "pending_activation":
            return False
        if not accepted:
            install.status = "calibrating"
            install.updated_at, install.updated_by = _now(), reviewer
            return True
        await _activate(org, install, reviewer, session)
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
    from services.worker_agent import semantic_review

    proposal = await _proposal(event)
    applied_here = ("skill_publish", "pack_activate", "writeback", *semantic_review.KINDS)
    if proposal is None or proposal["kind"] not in applied_here:
        return
    if proposal["status"] not in ("accepted", "open", "rejected"):
        return  # already applied, or no longer waiting
    if proposal["kind"] in semantic_review.KINDS:
        outcome: bool | None = await semantic_review.apply(event, proposal)
    elif proposal["kind"] == "skill_publish":
        outcome = await _apply_skill(event, proposal)
    elif proposal["kind"] == "pack_activate":
        outcome = await _apply_pack(event, proposal)
    else:
        outcome = await _apply_writeback(event, proposal)
    if outcome is None:
        logger.info("worker-agent | pack activation %s waits for the period close", proposal["id"])
        return
    accepted = event.payload.get("status") == "accepted"
    if accepted:
        await _finish(event, proposal["id"], "applied" if outcome else "apply_failed")
    elif proposal["status"] == "open" and outcome:
        # Changes asked for: the version is a draft again and a resubmission opens a new proposal.
        await _finish(event, proposal["id"], "withdrawn")


async def activate_waiting(org: uuid.UUID, space: uuid.UUID, actor: uuid.UUID) -> int:
    """Activate the installs of `space` whose activation was accepted during a period close
    (AC-09-02-05); the period close worker calls this when the close ends. Returns how many."""
    async with scoped_session("svc_inbox", org) as session:
        accepted = (
            await session.scalars(
                select(inbox.Proposal.target_ref).where(
                    inbox.Proposal.data_space_id == space,
                    inbox.Proposal.kind == "pack_activate",
                    inbox.Proposal.status == "accepted",
                )
            )
        ).all()
    done = 0
    for install_id in accepted:
        async with scoped_session("svc_pack", org, actor) as session:
            install = await session.get(pack.PackInstall, install_id, with_for_update=True)
            if install is None or install.status != "pending_activation":
                continue
            await _activate(org, install, actor, session)
            done += 1
        async with scoped_session("svc_inbox", org) as session:
            await session.execute(
                update(inbox.Proposal)
                .where(inbox.Proposal.target_ref == install_id, inbox.Proposal.status == "accepted")
                .values(status="applied", applied_at=_now(), updated_at=_now())
            )
    return done


HANDLERS: dict[str, Handler] = {
    PUBLISH: publish_requested,
    SHARE: share_requested,
    ACTIVATE: activation_requested,
    WRITEBACK: writeback_submitted,
    DECIDED: decided,
}
