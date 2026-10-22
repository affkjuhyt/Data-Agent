"""Chính sách (UC-11-02, UC-07-08, UC-11-08): access policies on a table (row, column, mask) and
behaviour policies of agents and the system, with simulation, backtest and activation.

The Policy engine that rewrites SQL runs in the query path (BR-45); here a policy is declared,
shown rewritten for one person before it is switched on (the DB refuses an active access policy
never simulated), backtested by a worker over past queries, and activated. Simulation is the
deterministic rewrite, not a query: the api has no warehouse connection.
"""

from __future__ import annotations

import datetime
import re
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import catalog, governance, org
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.problems import FieldError, Problem

router = APIRouter()
ROLE = "svc_governance"
POLL_AFTER_MS = 2_000
PolicyId = Annotated[uuid.UUID, Path(alias="id")]
# `:user.<attribute>` in a row condition names an attribute synced from the directory.
_USER_ATTRIBUTE = re.compile(r":user\.([A-Za-z_][A-Za-z0-9_]*)")
# Attributes of org.app_user itself; the rest come from app_user.attributes.
_USER_COLUMNS = {"id", "username", "department"}


class _View(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AppliesTo(BaseModel):
    """Who the policy applies to; all empty means everyone.

    `actors` narrows it to who runs the query: "agent" when the agent answers or runs (so what
    the policy hides never reaches a model, though the person may still read it themselves),
    "person" when people read data directly (explorer, tiles); empty is both. The worker's query
    path applies it (worker_agent/query/scope.py)."""

    role_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    group_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    user_ids: list[uuid.UUID] = Field(default_factory=list, max_length=1_000)
    actors: list[Literal["agent", "person"]] = Field(default_factory=list, max_length=2)


class AccessDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_space_id: uuid.UUID
    dataset_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["row", "column", "mask"]
    applies_to: AppliesTo = Field(default_factory=AppliesTo)
    condition_expr: str | None = Field(default=None, max_length=2_000)
    columns: list[str] | None = Field(default=None, max_length=200)
    mask_method: Literal["hash", "partial", "last4", "null"] | None = None


class BehaviorDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["system", "org", "data_space", "agent", "user"]
    data_space_id: uuid.UUID | None = None
    subject_id: uuid.UUID | None = None
    kind: Literal["block", "mask", "require_approval", "auto_handle", "allow", "budget"]
    description: str = Field(min_length=1, max_length=2_000)
    condition: dict[str, Any] = Field(default_factory=dict)
    action: dict[str, Any] = Field(default_factory=dict)
    exceptions: list[Any] = Field(default_factory=list, max_length=100)
    review_at: datetime.date | None = None


class PolicyCreate(BaseModel):
    type: Literal["access", "behavior"]
    definition: dict[str, Any]


class PolicySimulate(BaseModel):
    as_user_id: uuid.UUID
    sample_sql: str | None = Field(default=None, max_length=20_000)


class PolicyBacktestRequest(BaseModel):
    window_days: int = Field(default=7, ge=1, le=90)


class PolicyDisable(BaseModel):
    reason: str = Field(min_length=1, max_length=2_000)


class PermissionSimulate(BaseModel):
    dataset_id: uuid.UUID
    role_ids: list[uuid.UUID] | None = Field(default=None, max_length=2)
    user_ids: list[uuid.UUID] | None = Field(default=None, max_length=2)


class AccessPolicyView(_View):
    type: Literal["access"] = "access"
    id: uuid.UUID
    data_space_id: uuid.UUID
    dataset_id: uuid.UUID
    name: str
    kind: str
    applies_to: Any
    condition_expr: str | None
    columns: list[str] | None
    mask_method: str | None
    version: int
    status: str
    simulated_at: datetime.datetime | None
    activated_at: datetime.datetime | None
    activated_by: uuid.UUID | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class BehaviorPolicyView(_View):
    type: Literal["behavior"] = "behavior"
    id: uuid.UUID
    scope: str
    data_space_id: uuid.UUID | None
    subject_id: uuid.UUID | None
    kind: str
    description: str
    condition: Any
    action: Any
    exceptions: Any
    origin: str
    origin_ref: uuid.UUID | None
    status: str
    review_at: datetime.date | None
    override_count: int
    approved_by: uuid.UUID | None
    approved_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


PolicyView = AccessPolicyView | BehaviorPolicyView


class BacktestResultView(_View):
    """The newest backtest of a policy; the counts are the worker's, zero until it has run.
    detail_ref is a storage path and stays inside."""

    id: uuid.UUID
    window_from: datetime.datetime
    window_to: datetime.datetime
    matched_count: int
    auto_handled_count: int
    blocked_count: int
    changed_result_count: int
    affected_agent_ids: list[uuid.UUID]
    created_at: datetime.datetime


class AccessPolicyDetail(AccessPolicyView):
    latest_backtest: BacktestResultView | None = None


class BehaviorPolicyDetail(BehaviorPolicyView):
    latest_backtest: BacktestResultView | None = None


PolicyDetail = AccessPolicyDetail | BehaviorPolicyDetail


class SimulationView(BaseModel):
    policy_id: uuid.UUID
    as_user_id: uuid.UUID
    applies: bool
    reason: str
    before_sql: str | None = None
    after_sql: str | None = None
    row_condition: str | None = None
    hidden_columns: list[str] = Field(default_factory=list)
    masked_columns: dict[str, str] = Field(default_factory=dict)


class BacktestAccepted(BaseModel):
    id: uuid.UUID
    status: str
    poll_after_ms: int


class TableView(BaseModel):
    """One table as one role or one person would read it."""

    subject_kind: Literal["role", "user"]
    subject_id: uuid.UUID
    visible_columns: list[str]
    hidden_columns: list[str]
    masked_columns: dict[str, str]
    row_conditions: list[str]
    policy_ids: list[uuid.UUID]


class PermissionSimulation(BaseModel):
    dataset_id: uuid.UUID
    views: list[TableView]


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _invalid(field: str, message: str) -> Problem:
    return Problem(
        422,
        "validation_failed",
        message,
        errors=[FieldError(field=field, code="invalid", message=message)],
    )


def _event(
    session: AsyncSession,
    principal: Principal,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    event_type: str,
    **payload: Any,
) -> None:
    session.add(
        governance.Outbox(
            id=uuid7(),
            org_id=principal.org_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload={k: str(v) if isinstance(v, uuid.UUID) else v for k, v in payload.items()},
        )
    )


def _in_scope(
    row: governance.AccessPolicy | governance.BehaviorPolicy, principal: Principal, *, write: bool
) -> None:
    """A Data Space's policy is reached only under that Data Space's header (an admin without a
    header reaches every one); an organisation-wide policy is read by anyone allowed here but
    changed only by an admin."""
    if row.data_space_id is None:
        if write and "admin" not in principal.capabilities:
            raise Problem(403, "forbidden", "Organisation-wide policies are changed by an admin.")
        return
    space = principal.data_space_id
    if row.data_space_id == space:
        return
    if space is None and "admin" in principal.capabilities:
        return
    raise Problem(404, "not_found", "Policy not found.")


async def _policy(
    session: AsyncSession,
    principal: Principal,
    policy_id: uuid.UUID,
    *,
    lock: bool = False,
    write: bool = True,
) -> governance.AccessPolicy | governance.BehaviorPolicy:
    """The policy of either kind, in the caller's scope (_in_scope); ids are uuid7, so one id
    names one row. Every route of a policy goes through here."""
    ap, bp = governance.AccessPolicy, governance.BehaviorPolicy
    access = select(ap).where(ap.id == policy_id, ap.org_id == principal.org_id)
    found: governance.AccessPolicy | governance.BehaviorPolicy | None = await session.scalar(
        access.with_for_update() if lock else access
    )
    if found is None:
        behavior = select(bp).where(bp.id == policy_id, bp.org_id == principal.org_id)
        found = await session.scalar(behavior.with_for_update() if lock else behavior)
    if found is None:
        raise Problem(404, "not_found", "Policy not found.")
    _in_scope(found, principal, write=write)
    return found


def _view(row: governance.AccessPolicy | governance.BehaviorPolicy) -> PolicyView:
    if isinstance(row, governance.AccessPolicy):
        return AccessPolicyView.model_validate(row)
    return BehaviorPolicyView.model_validate(row)


async def _known_attributes(session: AsyncSession, principal: Principal) -> set[str]:
    """The user attributes the directory sync has written for this organisation (UC-11-02 E1)."""
    rows = (
        await session.scalars(
            select(org.AppUser.attributes).where(
                org.AppUser.org_id == principal.org_id, org.AppUser.kind == "human"
            )
        )
    ).all()
    names = set(_USER_COLUMNS)
    for attributes in rows:
        if isinstance(attributes, dict):
            names.update(attributes)
    return names


async def _dataset_columns(
    session: AsyncSession, principal: Principal, data_space: uuid.UUID, dataset_id: uuid.UUID
) -> set[str]:
    found = await session.scalar(
        select(catalog.Dataset.id).where(
            catalog.Dataset.id == dataset_id,
            catalog.Dataset.org_id == principal.org_id,
            catalog.Dataset.data_space_id == data_space,
            catalog.Dataset.deleted_at.is_(None),
        )
    )
    if found is None:
        raise _invalid("definition.dataset_id", "The dataset is not in that Data Space.")
    return set(
        (
            await session.scalars(
                select(catalog.ColumnMeta.name).where(
                    catalog.ColumnMeta.dataset_id == dataset_id,
                    catalog.ColumnMeta.removed_in_version.is_(None),
                )
            )
        ).all()
    )


async def _check_access(
    session: AsyncSession, principal: Principal, definition: AccessDefinition
) -> None:
    space = await session.scalar(
        select(org.DataSpace.id).where(
            org.DataSpace.id == definition.data_space_id,
            org.DataSpace.org_id == principal.org_id,
            org.DataSpace.deleted_at.is_(None),
        )
    )
    if space is None:
        raise _invalid("definition.data_space_id", "Data Space not found.")
    columns = await _dataset_columns(
        session, principal, definition.data_space_id, definition.dataset_id
    )
    if definition.kind == "row":
        if not definition.condition_expr:
            raise _invalid("definition.condition_expr", "A row policy needs condition_expr.")
        named = set(_USER_ATTRIBUTE.findall(definition.condition_expr))
        unknown = named - await _known_attributes(session, principal)
        if unknown:
            # UC-11-02 E1: a condition on an attribute the directory does not hold is not saved.
            raise _invalid(
                "definition.condition_expr",
                f"No user has the attribute(s) {sorted(unknown)} from the directory.",
            )
    else:
        if not definition.columns:
            raise _invalid("definition.columns", f"A {definition.kind} policy names columns.")
        missing = set(definition.columns) - columns
        if missing:
            raise _invalid("definition.columns", f"Not columns of the dataset: {sorted(missing)}.")
        if definition.kind == "mask" and definition.mask_method is None:
            raise _invalid("definition.mask_method", "A mask policy needs mask_method.")


# ---------------------------------------------------------------- list, create


@router.get(
    "/v1/policies",
    response_model=list[AccessPolicyView] | list[BehaviorPolicyView],
    **endpoint("GET", "/v1/policies", operation_id="listPolicies"),
)
async def list_policies(
    principal: CurrentPrincipal,
    type: Literal["access", "behavior"] = "behavior",
    scope: str | None = None,
    status: str | None = None,
) -> list[AccessPolicyView] | list[BehaviorPolicyView]:
    space = principal.data_space_id
    async with transaction(ROLE, principal) as session:
        if type == "access":
            # Row conditions and the columns they hide say who sees what: the people who keep
            # a Data Space's data read them, not every asker (UC-11-02 actors AC05, AC03).
            if not principal.capabilities & {"admin", "steward"}:
                raise Problem(403, "forbidden", "Access policies are read by admin or steward.")
            ap = governance.AccessPolicy
            query = select(ap).where(ap.org_id == principal.org_id)
            # A table's policies are its Data Space's: without the header, an admin sees all.
            if space is not None:
                query = query.where(ap.data_space_id == space)
            elif "admin" not in principal.capabilities:
                return []
            if status:
                query = query.where(ap.status == status)
            rows = (await session.scalars(query.order_by(ap.created_at.desc()))).all()
            return [AccessPolicyView.model_validate(r) for r in rows]
        bp = governance.BehaviorPolicy
        listed = select(bp).where(bp.org_id == principal.org_id)
        if space is not None:
            listed = listed.where(or_(bp.data_space_id.is_(None), bp.data_space_id == space))
        elif "admin" not in principal.capabilities:
            listed = listed.where(bp.data_space_id.is_(None))
        if scope:
            listed = listed.where(bp.scope == scope)
        if status:
            listed = listed.where(bp.status == status)
        behaviors = (await session.scalars(listed.order_by(bp.created_at.desc()))).all()
        return [BehaviorPolicyView.model_validate(r) for r in behaviors]


@router.post(
    "/v1/policies",
    status_code=201,
    response_model=PolicyView,
    **endpoint("POST", "/v1/policies", operation_id="createPolicy"),
)
async def create_policy(body: PolicyCreate, principal: CurrentPrincipal) -> PolicyView:
    try:
        definition: AccessDefinition | BehaviorDefinition = (
            AccessDefinition.model_validate(body.definition)
            if body.type == "access"
            else BehaviorDefinition.model_validate(body.definition)
        )
    except ValidationError as exc:
        raise Problem(
            422,
            "validation_failed",
            "definition does not match its type.",
            errors=[
                FieldError(
                    field="definition." + ".".join(str(p) for p in e["loc"]),
                    code=e["type"],
                    message=e["msg"],
                )
                for e in exc.errors()
            ],
        ) from exc
    stamp = _now()
    async with transaction(ROLE, principal) as session:
        row: governance.AccessPolicy | governance.BehaviorPolicy
        if isinstance(definition, AccessDefinition):
            await _check_access(session, principal, definition)
            row = governance.AccessPolicy(
                id=uuid7(),
                org_id=principal.org_id,
                **definition.model_dump(mode="json", exclude={"applies_to"}),
                applies_to=definition.applies_to.model_dump(mode="json"),
                status="draft",
                created_by=principal.user_id,
                updated_by=principal.user_id,
                created_at=stamp,
                updated_at=stamp,
            )
        else:
            if definition.scope == "data_space" and definition.data_space_id is None:
                raise _invalid("definition.data_space_id", "A data_space policy names one.")
            if definition.scope in ("agent", "user") and definition.subject_id is None:
                raise _invalid("definition.subject_id", f"A {definition.scope} policy names one.")
            row = governance.BehaviorPolicy(
                id=uuid7(),
                org_id=principal.org_id,
                **definition.model_dump(mode="json"),
                origin="manual",
                status="draft",
                created_by=principal.user_id,
                updated_by=principal.user_id,
                created_at=stamp,
                updated_at=stamp,
            )
        session.add(row)
        await session.flush()
        _event(
            session,
            principal,
            row.__tablename__,
            row.id,
            "policy.created",
            policy_id=row.id,
            policy_type=body.type,
            status=row.status,
        )
        await session.refresh(row)
        return _view(row)


# ---------------------------------------------------------------- read one, disable


def _readable(
    row: governance.AccessPolicy | governance.BehaviorPolicy, principal: Principal
) -> None:
    """GET /v1/policies' rule for one row, on top of the scope: access policies say who sees
    what, and are read by admin or steward."""
    if isinstance(row, governance.AccessPolicy) and not principal.capabilities & {
        "admin",
        "steward",
    }:
        raise Problem(403, "forbidden", "Access policies are read by admin or steward.")


@router.get(
    "/v1/policies/{id}",
    response_model=PolicyDetail,
    **endpoint("GET", "/v1/policies/{id}", operation_id="getPolicy"),
)
async def get_policy(policy_id: PolicyId, principal: CurrentPrincipal) -> PolicyDetail:
    async with transaction(ROLE, principal) as session:
        row = await _policy(session, principal, policy_id, write=False)
        _readable(row, principal)
        backtest = await session.scalar(
            select(governance.PolicyBacktest)
            .where(
                governance.PolicyBacktest.policy_id == row.id,
                governance.PolicyBacktest.org_id == principal.org_id,
            )
            .order_by(
                governance.PolicyBacktest.created_at.desc(), governance.PolicyBacktest.id.desc()
            )
            .limit(1)
        )
        latest = BacktestResultView.model_validate(backtest) if backtest else None
        if isinstance(row, governance.AccessPolicy):
            return AccessPolicyDetail.model_validate(row).model_copy(
                update={"latest_backtest": latest}
            )
        return BehaviorPolicyDetail.model_validate(row).model_copy(
            update={"latest_backtest": latest}
        )


@router.post(
    "/v1/policies/{id}:disable",
    response_model=PolicyView,
    **endpoint("POST", "/v1/policies/{id}:disable", operation_id="disablePolicy"),
)
async def disable_policy(
    policy_id: PolicyId, body: PolicyDisable, principal: CurrentPrincipal
) -> PolicyView:
    reason = body.reason.strip()
    if not reason:
        raise _invalid("reason", "reason is required.")
    stamp = _now()
    async with transaction(ROLE, principal) as session:
        row = await _policy(session, principal, policy_id, lock=True)
        # An active policy is switched off and a draft is dropped; both end disabled. One that is
        # already disabled or has expired (BR-65) has nothing left to stop.
        if row.status not in ("draft", "active"):
            raise Problem(409, "invalid_state", f"The policy is already {row.status}.")
        previous, row.status = row.status, "disabled"
        row.updated_by, row.updated_at = principal.user_id, stamp
        kind = "access" if isinstance(row, governance.AccessPolicy) else "behavior"
        # The query path reloads policies on this event, as on policy.activated.
        _event(
            session,
            principal,
            row.__tablename__,
            row.id,
            "policy.disabled",
            policy_id=row.id,
            policy_type=kind,
            dataset_id=getattr(row, "dataset_id", None),
            data_space_id=row.data_space_id,
            previous_status=previous,
            status=row.status,
        )
        # The reason is the audit record's (A1.8), not part of the domain event.
        _event(
            session,
            principal,
            row.__tablename__,
            row.id,
            "audit.requested",
            action="policy.disable",
            object_kind=row.__tablename__,
            object_id=row.id,
            actor_id=principal.user_id,
            client_id=principal.client_id,
            before={"status": previous},
            after={"status": row.status, "reason": reason},
        )
        await session.flush()
        await session.refresh(row)
        return _view(row)


# ---------------------------------------------------------------- simulate, backtest, activate


def _applies(
    applies_to: Any,
    user_id: uuid.UUID,
    roles: set[str],
    groups: set[str],
    actor: str = "agent",
) -> bool:
    spec = applies_to if isinstance(applies_to, dict) else {}
    actors = set(map(str, spec.get("actors") or []))
    if actors and actor not in actors:
        return False
    role_ids = set(map(str, spec.get("role_ids") or []))
    group_ids = set(map(str, spec.get("group_ids") or []))
    user_ids = set(map(str, spec.get("user_ids") or []))
    if not (role_ids or group_ids or user_ids):
        return True
    return str(user_id) in user_ids or bool(role_ids & roles) or bool(group_ids & groups)


async def _subject(
    session: AsyncSession, principal: Principal, user_id: uuid.UUID
) -> tuple[org.AppUser, set[str], set[str]]:
    user = await session.scalar(
        select(org.AppUser).where(org.AppUser.id == user_id, org.AppUser.org_id == principal.org_id)
    )
    if user is None:
        raise _invalid("as_user_id", "User not found in this organisation.")
    roles = {
        str(r)
        for r in (
            await session.scalars(
                select(org.UserRole.role_id).where(org.UserRole.user_id == user_id)
            )
        ).all()
    }
    groups = {
        str(g)
        for g in (
            await session.scalars(
                select(org.UserGroupMember.group_id).where(org.UserGroupMember.user_id == user_id)
            )
        ).all()
    }
    return user, roles, groups


def _bind_user(condition: str, user: org.AppUser) -> str:
    """The row condition as the engine would apply it for this person; a missing attribute
    matches nothing (fails closed)."""
    attributes = user.attributes if isinstance(user.attributes, dict) else {}

    def value(match: re.Match[str]) -> str:
        name = match.group(1)
        found = getattr(user, name, None) if name in _USER_COLUMNS else attributes.get(name)
        if found is None:
            return "NULL"
        return "'" + str(found).replace("'", "''") + "'"

    return _USER_ATTRIBUTE.sub(value, condition)


_MASKS = {
    "hash": "md5({c}::text)",
    "partial": "left({c}::text, 2) || '***'",
    "last4": "'****' || right({c}::text, 4)",
    "null": "NULL",
}


@router.post(
    "/v1/policies/{id}:simulate",
    response_model=SimulationView,
    **endpoint("POST", "/v1/policies/{id}:simulate", operation_id="simulatePolicy"),
)
async def simulate_policy(
    policy_id: PolicyId, body: PolicySimulate, principal: CurrentPrincipal
) -> SimulationView:
    async with transaction(ROLE, principal) as session:
        row = await _policy(session, principal, policy_id, lock=True)
        user, roles, groups = await _subject(session, principal, body.as_user_id)
        if isinstance(row, governance.BehaviorPolicy):
            applies = (
                row.scope in ("system", "org")
                or (row.scope == "user" and row.subject_id == user.id)
                or (row.scope == "agent" and row.subject_id == user.id)
                or row.scope == "data_space"
            )
            return SimulationView(
                policy_id=row.id,
                as_user_id=user.id,
                applies=applies,
                reason=f"{row.scope} policy, {row.kind}",
            )
        # A3's PolicySimulate names no actor: the person is simulated as the agent answers them,
        # the stricter reading (a policy for the agent only applies, one for people only does not).
        actors = set(map(str, (row.applies_to or {}).get("actors") or []))
        applies = _applies(row.applies_to, user.id, roles, groups, "agent")
        view = SimulationView(
            policy_id=row.id,
            as_user_id=user.id,
            applies=applies,
            reason="applies when the agent answers this person, not when they read data themselves"
            if applies and actors == {"agent"}
            else "applies to this person"
            if applies
            else "applies when the person reads data themselves, not to the agent"
            if actors == {"person"}
            else "not among whom it applies to",
            before_sql=body.sample_sql,
        )
        if applies:
            if row.kind == "row" and row.condition_expr:
                view.row_condition = _bind_user(row.condition_expr, user)
            elif row.kind == "column":
                view.hidden_columns = list(row.columns or [])
            elif row.kind == "mask" and row.mask_method:
                view.masked_columns = {
                    c: _MASKS[row.mask_method].format(c=c) for c in row.columns or []
                }
        if body.sample_sql:
            view.after_sql = _rewritten(body.sample_sql, view)
        # The DB refuses an access policy switched on without a simulation (UC-11-02 step 3).
        row.simulated_at = _now()
        row.updated_by = principal.user_id
        row.updated_at = row.simulated_at
        return view


def _rewritten(sql: str, view: SimulationView) -> str:
    """A readable rendering of the rewrite: the person's predicate wraps the sample query, and
    hidden or masked columns are named. The engine's own rewrite is on the parsed plan."""
    out = f"SELECT * FROM ({sql.rstrip().rstrip(';')}) AS q"
    if view.row_condition:
        out += f" WHERE {view.row_condition}"
    notes = [f"-- hidden: {c}" for c in view.hidden_columns]
    notes += [f"-- masked: {c} AS {expr}" for c, expr in view.masked_columns.items()]
    return "\n".join([out, *notes])


@router.post(
    "/v1/policies/{id}:backtest",
    status_code=202,
    response_model=BacktestAccepted,
    **endpoint("POST", "/v1/policies/{id}:backtest", operation_id="backtestPolicy"),
)
async def backtest_policy(
    policy_id: PolicyId,
    principal: CurrentPrincipal,
    response: Response,
    body: PolicyBacktestRequest | None = None,
) -> BacktestAccepted:
    request = body or PolicyBacktestRequest()
    stamp = _now()
    async with transaction(ROLE, principal) as session:
        row = await _policy(session, principal, policy_id)
        kind = "access" if isinstance(row, governance.AccessPolicy) else "behavior"
        # The worker replays the window's queries under the policy and fills the counts
        # (UC-11-02 step 4); the row is the place it writes them.
        backtest = governance.PolicyBacktest(
            id=uuid7(),
            org_id=principal.org_id,
            policy_kind=kind,
            policy_id=row.id,
            window_from=stamp - datetime.timedelta(days=request.window_days),
            window_to=stamp,
            created_at=stamp,
        )
        session.add(backtest)
        await session.flush()
        _event(
            session,
            principal,
            "policy_backtest",
            backtest.id,
            "policy.backtest_requested",
            policy_id=row.id,
            policy_kind=kind,
            backtest_id=backtest.id,
            window_from=backtest.window_from.isoformat(),
            window_to=backtest.window_to.isoformat(),
            requested_by=str(principal.user_id),
        )
    response.headers["Location"] = f"/v1/policy-backtests/{backtest.id}"
    return BacktestAccepted(id=backtest.id, status="queued", poll_after_ms=POLL_AFTER_MS)


@router.post(
    "/v1/policies/{id}:activate",
    response_model=PolicyView,
    **endpoint("POST", "/v1/policies/{id}:activate", operation_id="activatePolicy"),
)
async def activate_policy(policy_id: PolicyId, principal: CurrentPrincipal) -> PolicyView:
    stamp = _now()
    async with transaction(ROLE, principal) as session:
        row = await _policy(session, principal, policy_id, lock=True)
        if row.status not in ("draft", "disabled"):
            raise Problem(409, "invalid_state", f"A {row.status} policy is not activated.")
        if isinstance(row, governance.AccessPolicy):
            if row.simulated_at is None:
                raise Problem(
                    422, "business_rule_violated", "Simulate the policy before switching it on."
                )
            latest_backtest = await session.scalar(
                select(governance.PolicyBacktest)
                .where(
                    governance.PolicyBacktest.org_id == principal.org_id,
                    governance.PolicyBacktest.policy_id == row.id,
                    governance.PolicyBacktest.policy_kind == "access",
                )
                .order_by(
                    governance.PolicyBacktest.created_at.desc(),
                    governance.PolicyBacktest.id.desc(),
                )
                .limit(1)
            )
            if latest_backtest and latest_backtest.changed_result_count:
                _event(
                    session,
                    principal,
                    row.__tablename__,
                    row.id,
                    "policy.impact_warning",
                    policy_id=row.id,
                    data_space_id=row.data_space_id,
                    changed_result_count=latest_backtest.changed_result_count,
                    affected_agent_ids=[
                        str(value) for value in latest_backtest.affected_agent_ids or []
                    ],
                )
            row.activated_at, row.activated_by = stamp, principal.user_id
        else:
            row.approved_at, row.approved_by = stamp, principal.user_id
        row.status = "active"
        row.updated_by, row.updated_at = principal.user_id, stamp
        # The query path reloads policies on this event; owners of scheduled reports the
        # policy changes are told by the report service (UC-11-02 4a).
        _event(
            session,
            principal,
            row.__tablename__,
            row.id,
            "policy.activated",
            policy_id=row.id,
            policy_type="access" if isinstance(row, governance.AccessPolicy) else "behavior",
            dataset_id=getattr(row, "dataset_id", None),
            data_space_id=row.data_space_id,
            status=row.status,
        )
        await session.flush()
        await session.refresh(row)
        return _view(row)


# ---------------------------------------------------------------- permissions (UC-11-08)


@router.post(
    "/v1/permissions:simulate",
    response_model=PermissionSimulation,
    **endpoint("POST", "/v1/permissions:simulate", operation_id="simulatePermissions"),
)
async def simulate_permissions(
    body: PermissionSimulate, principal: CurrentPrincipal
) -> PermissionSimulation:
    subjects: list[tuple[Literal["role", "user"], uuid.UUID]] = [
        ("role", r) for r in body.role_ids or []
    ] + [("user", u) for u in body.user_ids or []]
    if not subjects:
        raise _invalid("role_ids", "Name one or two roles or people to compare.")
    if len(subjects) > 2:
        raise _invalid("role_ids", "At most two roles or people are compared.")
    async with transaction(ROLE, principal) as session:
        dataset = await session.scalar(
            select(catalog.Dataset).where(
                catalog.Dataset.id == body.dataset_id,
                catalog.Dataset.org_id == principal.org_id,
                catalog.Dataset.deleted_at.is_(None),
            )
        )
        if dataset is None:
            raise _invalid("dataset_id", "Dataset not found.")
        columns = sorted(
            await _dataset_columns(session, principal, dataset.data_space_id, dataset.id)
        )
        policies = (
            await session.scalars(
                select(governance.AccessPolicy).where(
                    governance.AccessPolicy.org_id == principal.org_id,
                    governance.AccessPolicy.dataset_id == dataset.id,
                    governance.AccessPolicy.status == "active",
                )
            )
        ).all()
        views = []
        for kind, subject_id in subjects:
            if kind == "user":
                user, roles, groups = await _subject(session, principal, subject_id)
            else:
                role = await session.scalar(
                    select(org.Role.id).where(
                        org.Role.id == subject_id, org.Role.org_id == principal.org_id
                    )
                )
                if role is None:
                    raise _invalid("role_ids", "Role not found in this organisation.")
                user, roles, groups = None, {str(subject_id)}, set()
            hidden: set[str] = set()
            masked: dict[str, str] = {}
            conditions: list[str] = []
            used: list[uuid.UUID] = []
            for p in policies:
                if not _applies(p.applies_to, subject_id, roles, groups):
                    continue
                used.append(p.id)
                # BR-137: policies on one table combine by intersection, never loosening.
                if p.kind == "column":
                    hidden.update(p.columns or [])
                elif p.kind == "mask" and p.mask_method:
                    for c in p.columns or []:
                        masked.setdefault(c, p.mask_method)
                elif p.kind == "row" and p.condition_expr:
                    conditions.append(
                        _bind_user(p.condition_expr, user) if user else p.condition_expr
                    )
            views.append(
                TableView(
                    subject_kind=kind,
                    subject_id=subject_id,
                    visible_columns=[c for c in columns if c not in hidden],
                    hidden_columns=sorted(hidden),
                    masked_columns={c: m for c, m in masked.items() if c not in hidden},
                    row_conditions=conditions,
                    policy_ids=used,
                )
            )
    return PermissionSimulation(dataset_id=body.dataset_id, views=views)
