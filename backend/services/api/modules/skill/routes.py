"""A2 group Kỹ năng (P03, UC-03-01..07): the library, versions, runs and suggestions of schema skill.

Runs and tests are long work (A1.6): a run is written queued with `skill_run.requested`, a test
reserves the eval run's id and writes `skill_version.eval_requested`; worker-agent and the eval
module act on the events. A version's `eval_run_id` is the run that passed it: the consumer of
the eval result writes it (or sends the version back to draft), so `testing` with an eval run is
"passed, ready to publish" (UC-03-04 postcondition). Publishing at Data Space or org scope opens
a skill_publish proposal in Cần bạn through `skill_version.publish_requested` (UC-03-05); the
decision comes back as proposal.decided. Every create and status change writes its event to
skill.outbox in the same transaction (D1 rule 8).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Response
from sqlalchemy import ColumnElement, and_, exists, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import agent, context, skill
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import (
    Order,
    Page,
    PageRequest,
    keyset,
    next_page,
    order_by,
    page_request,
)
from services.api.kernel.problems import FieldError, Problem
from services.api.modules.eval import eval_runs
from services.api.modules.skill import rules
from services.api.modules.skill.schemas import (
    Accepted,
    Category,
    RetireRequest,
    RunCreate,
    RunDetail,
    RunStepView,
    RunView,
    Scope,
    ShareRequest,
    SkillCreate,
    SkillDetail,
    SkillView,
    SuggestionStatus,
    SuggestionView,
    ToolView,
    TryRequest,
    VersionCreate,
    VersionPatch,
    VersionSummary,
    VersionView,
)

router = APIRouter()
# BR-12: passing test cases a skill shared beyond its author needs.
MIN_PASSING_CASES = 3
ROLE = "svc_skill"
POLL_AFTER_MS = 1_000
# BR-97 / UC-03-06 3a: a retired version still serves the places that use it for 30 days.
TRANSITION = datetime.timedelta(days=30)
# UC-03-07 3a: a dismissed pattern is not suggested again for 90 days.
DISMISS_FOR = datetime.timedelta(days=90)
# A run uses the published version; one flagged needs_review still runs, with its label (UC-03-04 1a).
RUNNABLE = ("published", "needs_review")

SkillRef = Annotated[str, Path(alias="id", description="Skill id or code (A1.1)")]
RowId = Annotated[uuid.UUID, Path(alias="id")]


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _emit(
    session: AsyncSession,
    principal: Principal,
    table: str,
    row_id: uuid.UUID,
    event_type: str,
    **payload: Any,
) -> None:
    """One skill.outbox row in the caller's transaction: ids and states, no free text."""
    session.add(
        skill.Outbox(
            id=uuid7(),
            org_id=principal.org_id,
            aggregate_type=table,
            aggregate_id=row_id,
            event_type=event_type,
            payload={
                k: v.isoformat()
                if isinstance(v, datetime.date)
                else str(v)
                if isinstance(v, uuid.UUID)
                else v
                for k, v in payload.items()
            },
        )
    )


def _visible(principal: Principal, data_space: uuid.UUID) -> ColumnElement[bool]:
    """Skills the caller sees from this Data Space: org-wide ones, the Data Space's, and their
    own personal ones (UC-03-01 precondition: the scope includes the current Data Space)."""
    s = skill.Skill
    return and_(
        s.org_id == principal.org_id,
        s.deleted_at.is_(None),
        or_(s.scope == "org", s.data_space_id == data_space),
        or_(s.scope != "personal", s.owner_id == principal.user_id),
    )


async def _skill(
    session: AsyncSession,
    ref: str,
    principal: Principal,
    data_space: uuid.UUID,
    *,
    lock: bool = False,
) -> skill.Skill:
    """By id or by code (A1.1); a code names the Data Space's skill before an org-wide one."""
    query = select(skill.Skill).where(_visible(principal, data_space))
    try:
        query = query.where(skill.Skill.id == uuid.UUID(ref))
    except ValueError:
        # Every skill records the Data Space it was authored in, org-wide ones too, so "the
        # Data Space's skill first" is "authored here first", not "data_space_id set first".
        query = query.where(skill.Skill.code == ref).order_by(
            (skill.Skill.data_space_id == data_space).desc().nulls_last(), skill.Skill.created_at
        )
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query.limit(1))
    if row is None:
        raise Problem(404, "not_found", "Skill not found.")
    return row


def _own(row: skill.Skill, principal: Principal) -> None:
    if row.owner_id != principal.user_id:
        raise Problem(403, "forbidden", "Only the skill's owner can change it.")


async def _version(
    session: AsyncSession,
    version_id: uuid.UUID,
    principal: Principal,
    data_space: uuid.UUID,
) -> tuple[skill.SkillVersion, skill.Skill]:
    found = (
        await session.execute(
            select(skill.SkillVersion, skill.Skill)
            .join(skill.Skill, skill.Skill.id == skill.SkillVersion.skill_id)
            .where(skill.SkillVersion.id == version_id, _visible(principal, data_space))
            .with_for_update()
        )
    ).first()
    if found is None:
        raise Problem(404, "not_found", "Skill version not found.")
    return found[0], found[1]


async def _runnable(session: AsyncSession, skill_id: uuid.UUID) -> skill.SkillVersion | None:
    found: skill.SkillVersion | None = await session.scalar(
        select(skill.SkillVersion)
        .where(skill.SkillVersion.skill_id == skill_id, skill.SkillVersion.status.in_(RUNNABLE))
        .order_by(skill.SkillVersion.version.desc())
        .limit(1)
    )
    return found


# ---------------------------------------------------------------- library (UC-03-01, UC-03-03)


@router.get(
    "/v1/skills",
    response_model=Page[SkillView],
    **endpoint("GET", "/v1/skills", operation_id="listSkills"),
)
async def list_skills(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(
            order_by(
                "created_at",
                "updated_at",
                "code",
                "name",
                "category",
                "run_count_30d",
                default="created_at desc",
            )
        ),
    ],
    category: Category | None = None,
    scope: Scope | None = None,
    q: str | None = None,
) -> Page[SkillView]:
    s = skill.Skill
    v = aliased(skill.SkillVersion)
    live = aliased(skill.SkillVersion)
    # BR-97: a skill whose every version is retired leaves the library; its runs stay readable.
    retired = and_(
        exists().where(v.skill_id == s.id),
        not_(exists().where(live.skill_id == s.id, live.status != "retired")),
    )
    query = select(s).where(_visible(principal, data_space), not_(retired))
    if category:
        query = query.where(s.category == category)
    if scope:
        query = query.where(s.scope == scope)
    if q and q.strip():
        pattern = (
            "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        )
        query = query.where(
            or_(s.name.ilike(pattern), s.code.ilike(pattern), s.description.ilike(pattern))
        )
    query = keyset(
        query,
        columns={
            "created_at": s.created_at,
            "updated_at": s.updated_at,
            "code": s.code,
            "name": s.name,
            "category": s.category,
            "run_count_30d": s.run_count_30d,
        },
        tiebreak=s.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    return Page(items=[SkillView.model_validate(r) for r in rows], next_page_token=token)


@router.post(
    "/v1/skills",
    status_code=201,
    response_model=SkillView,
    **endpoint("POST", "/v1/skills", operation_id="createSkill"),
)
async def create_skill(
    body: SkillCreate, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> SkillView:
    async with transaction(ROLE, principal) as session:
        row = skill.Skill(
            id=uuid7(),
            org_id=principal.org_id,
            # Always recorded, even org-wide: it is where the skill was authored and is reviewed.
            data_space_id=data_space,
            code=body.code,
            name=body.name.strip(),
            description=body.description,
            category=body.category,
            scope=body.scope,
            owner_id=principal.user_id,
            origin=body.origin,
            origin_ref=body.origin_ref,
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(row)
        _emit_created(session, principal, row)
        # A duplicate code is UNIQUE (org_id, data_space_id, code): 409 conflict from the kernel.
        await session.flush()
        await session.refresh(row)
        return SkillView.model_validate(row)


def _emit_created(session: AsyncSession, principal: Principal, row: skill.Skill) -> None:
    _emit(
        session,
        principal,
        "skill",
        row.id,
        "skill.created",
        skill_id=row.id,
        data_space_id=row.data_space_id,
        scope=row.scope,
        origin=row.origin,
        owner_id=row.owner_id,
    )


@router.get(
    "/v1/skills/{id}",
    response_model=SkillDetail,
    **endpoint("GET", "/v1/skills/{id}", operation_id="getSkill"),
)
async def get_skill(
    ref: SkillRef, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> SkillDetail:
    async with transaction(ROLE, principal) as session:
        row = await _skill(session, ref, principal, data_space)
        versions = list(
            (
                await session.scalars(
                    select(skill.SkillVersion)
                    .where(skill.SkillVersion.skill_id == row.id)
                    .order_by(skill.SkillVersion.version.desc())
                )
            ).all()
        )
    published = next((v for v in versions if v.status in RUNNABLE), None)
    return SkillDetail(
        **SkillView.model_validate(row).model_dump(),
        published_version=VersionView.model_validate(published) if published else None,
        versions=[VersionSummary.model_validate(v) for v in versions],
    )


# ---------------------------------------------------------------- runs (UC-03-01, UC-03-02)


@router.post(
    "/v1/skills/{id}/runs",
    status_code=202,
    response_model=Accepted,
    **endpoint("POST", "/v1/skills/{id}/runs", operation_id="createSkillRun"),
)
async def create_run(
    ref: SkillRef,
    body: RunCreate,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
) -> Accepted:
    async with transaction(ROLE, principal) as session:
        row = await _skill(session, ref, principal, data_space)
        version = await _runnable(session, row.id)
        if version is None:
            raise Problem(409, "invalid_state", "The skill has no published version to run.")
        errors = rules.param_errors(version.params_schema, body.params)
        if errors:
            raise Problem(
                422,
                "validation_failed",
                "params do not match the skill's params_schema.",
                errors=errors,
            )
        run = skill.SkillRun(
            id=uuid7(),
            org_id=principal.org_id,
            skill_version_id=version.id,
            data_space_id=data_space,
            caller_kind="user",
            principal_id=principal.user_id,
            params=body.params,
            as_of=body.as_of,
            status="queued",
        )
        session.add(run)
        _emit(
            session,
            principal,
            "skill_run",
            run.id,
            "skill_run.requested",
            skill_run_id=run.id,
            skill_id=row.id,
            skill_version_id=version.id,
            version=version.version,
            data_space_id=data_space,
            status=run.status,
        )
    response.headers["Location"] = f"/v1/skill-runs/{run.id}"
    return Accepted(id=run.id, status="queued", poll_after_ms=POLL_AFTER_MS)


@router.get(
    "/v1/skill-runs/{id}",
    response_model=RunDetail,
    **endpoint("GET", "/v1/skill-runs/{id}", operation_id="getSkillRun"),
)
async def get_run(
    run_id: RowId, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> RunDetail:
    async with transaction(ROLE, principal) as session:
        found = (
            await session.execute(
                select(skill.SkillRun, skill.SkillVersion)
                .join(skill.SkillVersion, skill.SkillVersion.id == skill.SkillRun.skill_version_id)
                .where(
                    skill.SkillRun.id == run_id,
                    skill.SkillRun.org_id == principal.org_id,
                    skill.SkillRun.data_space_id == data_space,
                    # A result is computed under its runner's data rights (A1.2 policies).
                    skill.SkillRun.principal_id == principal.user_id,
                )
            )
        ).first()
        if found is None:
            raise Problem(404, "not_found", "Skill run not found.")
        run, version = found
        steps = (
            await session.scalars(
                select(skill.SkillRunStep)
                .where(skill.SkillRunStep.skill_run_id == run.id)
                .order_by(skill.SkillRunStep.seq)
            )
        ).all()
    return RunDetail(
        **RunView.model_validate(run).model_dump(),
        skill_id=version.skill_id,
        version=version.version,
        steps=[RunStepView.model_validate(s) for s in steps],
    )


# ---------------------------------------------------------------- versions (UC-03-03..06)


async def _check_metrics(session: AsyncSession, principal: Principal, ids: list[uuid.UUID]) -> None:
    if not ids:
        return
    found = set(
        (
            await session.scalars(
                select(context.MetricVersion.id).where(
                    context.MetricVersion.id.in_(ids),
                    context.MetricVersion.org_id == principal.org_id,
                )
            )
        ).all()
    )
    missing = [str(i) for i in ids if i not in found]
    if missing:
        raise Problem(
            422,
            "validation_failed",
            "Unknown metric versions.",
            errors=[
                FieldError(field="metric_version_ids", code="not_found", message=", ".join(missing))
            ],
        )


def _check_schema(schema: dict[str, Any]) -> None:
    errors = rules.schema_errors(schema)
    if errors:
        raise Problem(422, "validation_failed", "params_schema is not usable.", errors=errors)


@router.post(
    "/v1/skills/{id}/versions",
    status_code=201,
    response_model=VersionView,
    **endpoint("POST", "/v1/skills/{id}/versions", operation_id="createSkillVersion"),
)
async def create_version(
    ref: SkillRef, body: VersionCreate, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> VersionView:
    _check_schema(body.params_schema)
    async with transaction(ROLE, principal) as session:
        # Locked: the next version number is read and written under it.
        row = await _skill(session, ref, principal, data_space, lock=True)
        _own(row, principal)
        await _check_metrics(session, principal, body.metric_version_ids)
        last = await session.scalar(
            select(func.max(skill.SkillVersion.version)).where(
                skill.SkillVersion.skill_id == row.id
            )
        )
        version = skill.SkillVersion(
            id=uuid7(),
            org_id=principal.org_id,
            skill_id=row.id,
            version=(last or 0) + 1,
            params_schema=body.params_schema,
            steps=body.steps,
            template_md=body.template_md,
            metric_version_ids=body.metric_version_ids,
            sandbox_limits=(
                body.sandbox_limits.model_dump(exclude_none=True) if body.sandbox_limits else {}
            ),
            status="draft",
        )
        session.add(version)
        _emit(
            session,
            principal,
            "skill_version",
            version.id,
            "skill_version.created",
            skill_version_id=version.id,
            skill_id=row.id,
            version=version.version,
            status="draft",
        )
        await session.flush()
        await session.refresh(version)
        return VersionView.model_validate(version)


@router.patch(
    "/v1/skill-versions/{id}",
    response_model=VersionView,
    **endpoint("PATCH", "/v1/skill-versions/{id}", operation_id="updateSkillVersion"),
)
async def update_version(
    version_id: RowId, body: VersionPatch, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> VersionView:
    sent = body.model_fields_set
    for field in ("params_schema", "steps", "sandbox_limits"):
        if field in sent and getattr(body, field) is None:
            raise Problem(
                422,
                "validation_failed",
                f"{field} cannot be null.",
                errors=[FieldError(field=field, code="null", message="Cannot be null.")],
            )
    if body.params_schema is not None:
        _check_schema(body.params_schema)
    async with transaction(ROLE, principal) as session:
        version, row = await _version(session, version_id, principal, data_space)
        _own(row, principal)
        if version.status != "draft":
            # BR-12: what was tested or published is not edited; a new version is.
            raise Problem(
                409,
                "invalid_state",
                f"Only a draft version is edited; this one is {version.status}.",
            )
        if body.params_schema is not None:
            version.params_schema = body.params_schema
        if body.steps is not None:
            version.steps = body.steps
        if "template_md" in sent:
            version.template_md = body.template_md
        if body.sandbox_limits is not None:
            version.sandbox_limits = body.sandbox_limits.model_dump(exclude_none=True)
        row.updated_by, row.updated_at = principal.user_id, _now()
        await session.flush()
        return VersionView.model_validate(version)


@router.post(
    "/v1/skill-versions/{id}:test",
    status_code=202,
    response_model=Accepted,
    **endpoint("POST", "/v1/skill-versions/{id}:test", operation_id="testSkillVersion"),
)
async def test_version(
    version_id: RowId, principal: CurrentPrincipal, data_space: CurrentDataSpace, response: Response
) -> Accepted:
    async with transaction(ROLE, principal) as session:
        version, row = await _version(session, version_id, principal, data_space)
        _own(row, principal)
        # needs_review is re-tested in place: it keeps running, labelled, meanwhile (UC-03-04 1a).
        if version.status not in ("draft", "needs_review"):
            raise Problem(
                409, "invalid_state", f"A {version.status} version is not sent to testing."
            )
        previous = version.status
        if version.status == "draft":
            version.status = "testing"
        # eval.eval_run belongs to the eval module: the id is reserved here and the run is
        # created and scored from the event.
        eval_run_id = uuid7()
        _emit(
            session,
            principal,
            "skill_version",
            version.id,
            "skill_version.eval_requested",
            skill_version_id=version.id,
            skill_id=row.id,
            version=version.version,
            data_space_id=row.data_space_id,
            eval_run_id=eval_run_id,
            previous_status=previous,
            status=version.status,
        )
    response.headers["Location"] = f"/v1/eval-runs/{eval_run_id}"
    return Accepted(id=eval_run_id, status="queued", poll_after_ms=POLL_AFTER_MS)


@router.post(
    "/v1/skill-versions/{id}:try",
    status_code=202,
    response_model=Accepted,
    **endpoint("POST", "/v1/skill-versions/{id}:try", operation_id="trySkillVersion"),
)
async def try_version(
    version_id: RowId,
    body: TryRequest,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
) -> Accepted:
    # SCR-33: the author previews a draft with other parameters before :test. The run is the
    # author's own (caller_kind user, read back by its runner only); mode "try" on the row and
    # the event tells the worker to leave the library alone: no run_count_30d, no
    # current_version, no suggestion input.
    async with transaction(ROLE, principal) as session:
        version, row = await _version(session, version_id, principal, data_space)
        _own(row, principal)
        if version.status != "draft":
            raise Problem(
                409,
                "invalid_state",
                f"Only a draft is tried; this version is {version.status}. Use a run instead.",
            )
        errors = rules.param_errors(version.params_schema, body.params)
        if errors:
            raise Problem(
                422,
                "validation_failed",
                "params do not match the version's params_schema.",
                errors=errors,
            )
        run = skill.SkillRun(
            id=uuid7(),
            org_id=principal.org_id,
            skill_version_id=version.id,
            data_space_id=data_space,
            caller_kind="user",
            principal_id=principal.user_id,
            params=body.params,
            status="queued",
            mode="try",
        )
        session.add(run)
        _emit(
            session,
            principal,
            "skill_run",
            run.id,
            "skill_run.requested",
            skill_run_id=run.id,
            skill_id=row.id,
            skill_version_id=version.id,
            version=version.version,
            data_space_id=data_space,
            mode="try",
            status=run.status,
        )
    response.headers["Location"] = f"/v1/skill-runs/{run.id}"
    return Accepted(id=run.id, status="queued", poll_after_ms=POLL_AFTER_MS)


async def _retire_published(
    session: AsyncSession, principal: Principal, row: skill.Skill, keep: uuid.UUID
) -> None:
    """A newly published version replaces the one before it (UC-03-06 trigger), which keeps
    serving its users through the BR-97 transition."""
    now = _now()
    old = (
        await session.scalars(
            select(skill.SkillVersion)
            .where(
                skill.SkillVersion.skill_id == row.id,
                skill.SkillVersion.status.in_(RUNNABLE),
                skill.SkillVersion.id != keep,
            )
            .with_for_update()
        )
    ).all()
    for version in old:
        version.status, version.retire_at = "retired", now + TRANSITION
        _emit(
            session,
            principal,
            "skill_version",
            version.id,
            "skill_version.retired",
            skill_version_id=version.id,
            skill_id=row.id,
            version=version.version,
            replaced_by_skill_id=None,
            retire_at=version.retire_at,
            status="retired",
        )
    # The one-published-version index must see the old row retired before the new is published.
    await session.flush()


@router.post(
    "/v1/skill-versions/{id}:publish",
    response_model=VersionView,
    **endpoint("POST", "/v1/skill-versions/{id}:publish", operation_id="publishSkillVersion"),
)
async def publish_version(
    version_id: RowId, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> VersionView:
    async with transaction(ROLE, principal) as session:
        version, row = await _version(session, version_id, principal, data_space)
        _own(row, principal)
        if version.status != "testing" or version.eval_run_id is None:
            raise Problem(
                409,
                "invalid_state",
                "The version has not passed its tests yet (UC-03-04); run :test and wait for it.",
            )
        steps = rules.approval_steps(row.scope)
        if row.scope != "personal":
            # BR-12: Data Space scope and wider needs at least 3 passing cases; the eval module
            # holds the run and answers on its own connection (svc_skill reads no eval table).
            run = (await eval_runs(principal, [version.eval_run_id])).get(version.eval_run_id)
            passed = int(run["passed"]) if run else 0
            if passed < MIN_PASSING_CASES:
                raise Problem(
                    422,
                    "business_rule_violated",
                    f"{passed} passing test cases; a {row.scope} skill needs at least "
                    f"{MIN_PASSING_CASES} (BR-12).",
                )
        if row.scope == "personal":
            # BR-12: a personal skill needs no review; its owner answers for it.
            await _retire_published(session, principal, row, version.id)
            version.status = "published"
            version.approved_by, version.approved_at = principal.user_id, _now()
            version.approval_steps = steps
            row.current_version = version.version
            row.updated_by, row.updated_at = principal.user_id, _now()
            _emit(
                session,
                principal,
                "skill_version",
                version.id,
                "skill.published",
                skill_id=row.id,
                skill_version_id=version.id,
                version=version.version,
                scope=row.scope,
                data_space_id=row.data_space_id,
                status="published",
            )
        else:
            # UC-03-05: the steward (and for org scope the administrator, BR-96) decide in Cần
            # bạn; inbox opens the skill_publish proposal from this event.
            version.status = "pending_review"
            version.approval_steps = steps
            _emit(
                session,
                principal,
                "skill_version",
                version.id,
                "skill_version.publish_requested",
                skill_version_id=version.id,
                skill_id=row.id,
                version=version.version,
                scope=row.scope,
                data_space_id=row.data_space_id,
                approval_steps=steps,
                eval_run_id=version.eval_run_id,
                requested_by=principal.user_id,
                status="pending_review",
            )
        await session.flush()
        return VersionView.model_validate(version)


@router.post(
    "/v1/skill-versions/{id}:retire",
    response_model=VersionView,
    **endpoint("POST", "/v1/skill-versions/{id}:retire", operation_id="retireSkillVersion"),
)
async def retire_version(
    version_id: RowId,
    body: RetireRequest,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> VersionView:
    async with transaction(ROLE, principal) as session:
        version, row = await _version(session, version_id, principal, data_space)
        # UC-03-06 is the steward's of the Data Space the skill was authored in: an org-wide
        # skill is visible from every Data Space, but another one's approver does not retire it.
        if row.data_space_id is not None and row.data_space_id != data_space:
            raise Problem(
                403, "forbidden", "Only an approver of the skill's own Data Space retires it."
            )
        if version.status not in RUNNABLE:
            raise Problem(
                409,
                "invalid_state",
                f"Only a published version is retired; this one is {version.status}.",
            )
        if body.replaced_by_skill_id is not None:
            replacement = await session.scalar(
                select(skill.Skill).where(
                    skill.Skill.id == body.replaced_by_skill_id, _visible(principal, data_space)
                )
            )
            if (
                replacement is None
                or replacement.id == row.id
                or await _runnable(session, replacement.id) is None
            ):
                raise Problem(
                    422,
                    "validation_failed",
                    "The replacement must be another published skill you can see.",
                    errors=[
                        FieldError(
                            field="replaced_by_skill_id",
                            code="invalid",
                            message="Not a published skill in reach.",
                        )
                    ],
                )
            row.replaced_by_skill_id = replacement.id
        now, previous = _now(), version.status
        version.status, version.retire_at = "retired", now + TRANSITION
        if row.current_version == version.version:
            row.current_version = None
        row.updated_by, row.updated_at = principal.user_id, now
        # Agents, report schedules and owners move to the replacement or are told (UC-03-06 2-4).
        _emit(
            session,
            principal,
            "skill_version",
            version.id,
            "skill_version.retired",
            skill_version_id=version.id,
            skill_id=row.id,
            version=version.version,
            replaced_by_skill_id=row.replaced_by_skill_id,
            retire_at=version.retire_at,
            status="retired",
        )
        # The reason is the audit record's (A1.8), not part of the domain event.
        _emit(
            session,
            principal,
            "skill_version",
            version.id,
            "audit.requested",
            action="skill_version.retire",
            object_kind="skill_version",
            object_id=version.id,
            actor_id=principal.user_id,
            before={"status": previous},
            after={
                "status": "retired",
                "retire_at": version.retire_at.isoformat(),
                "replaced_by_skill_id": str(row.replaced_by_skill_id)
                if row.replaced_by_skill_id
                else None,
                "reason": body.reason.strip(),
            },
        )
        await session.flush()
        return VersionView.model_validate(version)


@router.post(
    "/v1/skills/{id}:share",
    response_model=SkillView,
    **endpoint("POST", "/v1/skills/{id}:share", operation_id="shareSkill"),
)
async def share_skill(
    ref: SkillRef, body: ShareRequest, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> SkillView:
    async with transaction(ROLE, principal) as session:
        row = await _skill(session, ref, principal, data_space, lock=True)
        _own(row, principal)
        if body.scope == row.scope:
            return SkillView.model_validate(row)
        if rules.widens(row.scope, body.scope):
            # UC-03-07: sharing wider starts the UC-03-05 review of the published version; the
            # scope changes when the proposal is accepted, never before.
            published = await session.scalar(
                select(skill.SkillVersion).where(
                    skill.SkillVersion.skill_id == row.id,
                    skill.SkillVersion.status == "published",
                )
            )
            if published is None:
                raise Problem(
                    409,
                    "invalid_state",
                    "Only a skill with a tested, published version is shared (UC-03-07).",
                )
            _emit(
                session,
                principal,
                "skill",
                row.id,
                "skill.share_requested",
                skill_id=row.id,
                skill_version_id=published.id,
                version=published.version,
                data_space_id=row.data_space_id,
                previous_scope=row.scope,
                scope=body.scope,
                approval_steps=rules.approval_steps(body.scope),
                eval_run_id=published.eval_run_id,
                requested_by=principal.user_id,
            )
            return SkillView.model_validate(row)
        # Narrowing takes nothing from review: it applies at once.
        previous = row.scope
        if row.data_space_id is None:
            row.data_space_id = data_space
        row.scope = body.scope
        row.updated_by, row.updated_at = principal.user_id, _now()
        _emit(
            session,
            principal,
            "skill",
            row.id,
            "skill.shared",
            skill_id=row.id,
            data_space_id=row.data_space_id,
            previous_scope=previous,
            scope=row.scope,
        )
        await session.flush()
        return SkillView.model_validate(row)


# ---------------------------------------------------------------- suggestions (UC-03-07)


@router.get(
    "/v1/skill-suggestions",
    response_model=Page[SuggestionView],
    **endpoint("GET", "/v1/skill-suggestions", operation_id="listSkillSuggestions"),
)
async def list_suggestions(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(order_by("created_at", "updated_at", "user_count", default="created_at desc")),
    ],
    status: SuggestionStatus = "open",
) -> Page[SuggestionView]:
    s = skill.SkillSuggestion
    query = select(s).where(s.org_id == principal.org_id, s.data_space_id == data_space)
    query = query.where(s.status == status)
    query = keyset(
        query,
        columns={
            "created_at": s.created_at,
            "updated_at": s.updated_at,
            "user_count": s.user_count,
        },
        tiebreak=s.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    return Page(items=[_suggestion_view(r) for r in rows], next_page_token=token)


def _suggestion_view(row: skill.SkillSuggestion) -> SuggestionView:
    view = SuggestionView.model_validate(row)
    return view.model_copy(update={"pattern": rules.structure_only(row.pattern)})


async def _open_suggestion(
    session: AsyncSession, suggestion_id: uuid.UUID, principal: Principal, data_space: uuid.UUID
) -> skill.SkillSuggestion:
    row = await session.scalar(
        select(skill.SkillSuggestion)
        .where(
            skill.SkillSuggestion.id == suggestion_id,
            skill.SkillSuggestion.org_id == principal.org_id,
            skill.SkillSuggestion.data_space_id == data_space,
        )
        .with_for_update()
    )
    if row is None:
        raise Problem(404, "not_found", "Skill suggestion not found.")
    if row.status != "open":
        raise Problem(409, "invalid_state", f"The suggestion is already {row.status}.")
    return row


def _from_pattern(pattern: Any, key: str, default: Any, kind: type) -> Any:
    value = pattern.get(key) if isinstance(pattern, dict) else None
    return value if isinstance(value, kind) else default


@router.post(
    "/v1/skill-suggestions/{id}:accept",
    response_model=SkillView,
    **endpoint("POST", "/v1/skill-suggestions/{id}:accept", operation_id="acceptSkillSuggestion"),
)
async def accept_suggestion(
    suggestion_id: RowId, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> SkillView:
    async with transaction(ROLE, principal) as session:
        suggestion = await _open_suggestion(session, suggestion_id, principal, data_space)
        # The new skill belongs to the acceptor, who may not have written the sessions (BR-98).
        pattern = rules.structure_only(suggestion.pattern)
        category = _from_pattern(pattern, "category", "custom", str)
        name = _from_pattern(pattern, "name", "", str).strip()
        params_schema = _from_pattern(
            pattern, "params_schema", {"type": "object", "properties": {}}, dict
        )
        # UC-03-03 runs from here: a personal draft built from the step structure only (BR-98).
        row = skill.Skill(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=data_space,
            code=f"suggested-{suggestion.id.hex[-12:]}",
            name=name or "Kỹ năng đề xuất",
            category=category if category in rules.CATEGORIES else "custom",
            scope="personal",
            owner_id=principal.user_id,
            origin="agent_suggested",
            origin_ref=suggestion.id,
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(row)
        _emit_created(session, principal, row)
        await session.flush()
        version = skill.SkillVersion(
            id=uuid7(),
            org_id=principal.org_id,
            skill_id=row.id,
            version=1,
            params_schema=params_schema
            if not rules.schema_errors(params_schema)
            else {
                "type": "object",
                "properties": {},
            },
            steps=_from_pattern(pattern, "steps", [], list),
            status="draft",
        )
        session.add(version)
        _emit(
            session,
            principal,
            "skill_version",
            version.id,
            "skill_version.created",
            skill_version_id=version.id,
            skill_id=row.id,
            version=1,
            status="draft",
        )
        suggestion.status, suggestion.accepted_skill_id = "accepted", row.id
        suggestion.updated_by, suggestion.updated_at = principal.user_id, _now()
        _emit(
            session,
            principal,
            "skill_suggestion",
            suggestion.id,
            "skill_suggestion.accepted",
            skill_suggestion_id=suggestion.id,
            data_space_id=data_space,
            accepted_skill_id=row.id,
            status="accepted",
        )
        await session.flush()
        await session.refresh(row)
        return SkillView.model_validate(row)


@router.post(
    "/v1/skill-suggestions/{id}:dismiss",
    response_model=SuggestionView,
    **endpoint("POST", "/v1/skill-suggestions/{id}:dismiss", operation_id="dismissSkillSuggestion"),
)
async def dismiss_suggestion(
    suggestion_id: RowId, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> SuggestionView:
    async with transaction(ROLE, principal) as session:
        suggestion = await _open_suggestion(session, suggestion_id, principal, data_space)
        now = _now()
        suggestion.status, suggestion.dismissed_until = "dismissed", now + DISMISS_FOR
        suggestion.updated_by, suggestion.updated_at = principal.user_id, now
        # The nightly scan reads dismissed_until and skips the pattern until then.
        _emit(
            session,
            principal,
            "skill_suggestion",
            suggestion.id,
            "skill_suggestion.dismissed",
            skill_suggestion_id=suggestion.id,
            data_space_id=data_space,
            dismissed_until=suggestion.dismissed_until,
            status="dismissed",
        )
        await session.flush()
        return _suggestion_view(suggestion)


# ---------------------------------------------------------------- tools (UC-03-01, UC-13-02)

ToolLevel = Literal["read", "write_derived", "external_send", "system_action"]
# A3 names the level external_send where tab 3b and agent.tool_def say send_external.
_SEND_EXTERNAL = ("send_external", "external_send")


@router.get(
    "/v1/tools",
    response_model=Page[ToolView],
    **endpoint("GET", "/v1/tools", operation_id="listTools"),
)
async def list_tools(
    principal: CurrentPrincipal,
    level: ToolLevel | None = None,
) -> Page[ToolView]:
    # agent.tool_def is the system's register, the same for every organisation (G2.1, no org_id),
    # loaded by migration: one page of the active tools, by code. A draft or deprecated tool is
    # not one an agent may be given, so it is not listed.
    if level == "system_action":
        # No agent tool is system-level (tab 5 G2); tool_def's CHECK has no such level.
        return Page(items=[])
    t = agent.ToolDef
    query = select(t).where(t.status == "active").order_by(t.code)
    if level is not None:
        query = query.where(
            t.permission_level == (_SEND_EXTERNAL[0] if level == _SEND_EXTERNAL[1] else level)
        )
    async with transaction(ROLE, principal) as session:
        rows = (await session.scalars(query)).all()
    return Page(
        items=[
            ToolView.model_validate(
                {
                    "code": row.code,
                    "level": _SEND_EXTERNAL[1]
                    if row.permission_level == _SEND_EXTERNAL[0]
                    else row.permission_level,
                    "description": row.model_description,
                    "executed_by": row.owner_service,
                    "default_checkpoint": None
                    if row.default_checkpoint == "none"
                    else row.default_checkpoint,
                }
            )
            for row in rows
        ]
    )
