"""A2 group "Nguồn và danh mục": sources, scans, the dataset catalogue and the certification of
consolidated dataset versions (UC-04-01..05, UC-14-05).

Every create and status change writes its event to catalog.outbox in the same transaction (D1
rule 8). Work that reaches the source itself (connection test, scan, schedule in Temporal) is the
connector worker's: the route records the request and its event and answers 202 (A1.6).
Certification events are written by the trigger on catalog.dataset_version_certification, not
here, so there is exactly one per history row.
"""

from __future__ import annotations

import datetime
import uuid
import zoneinfo
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import catalog, org
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import (
    Order,
    Page,
    PageRequest,
    encode_page_token,
    invalid_page_token,
    keyset,
    next_page,
    order_by,
    page_request,
)
from services.api.kernel.problems import FieldError, Problem
from services.api.modules.catalog import rules
from services.api.modules.catalog.schemas import (
    Accepted,
    CertificationView,
    CertifiedVersion,
    CertifyRequest,
    ColumnProfileView,
    ColumnView,
    DatasetDetail,
    DatasetPatch,
    DatasetVersionView,
    DatasetView,
    RevokeRequest,
    ScanRequest,
    ScanRunView,
    ScheduleUpdate,
    SecretReference,
    SourceCreate,
    SourcePatch,
    SourceSchedule,
    SourceView,
)

router = APIRouter()
ROLE = "svc_catalog"
PathId = Annotated[uuid.UUID, Path(alias="id")]
POLL_AFTER_MS = 2_000
NEXT_RUNS = 5
# The organisation's default zone (org.organization.time_zone default) when no calendar is set.
DEFAULT_ZONE = "Asia/Ho_Chi_Minh"
# A source is scanned while it can be read (UC-04-02 precondition: the source is active).
SCANNABLE = ("active", "needs_fix")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _event(
    principal: Principal,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    event_type: str,
    **payload: Any,
) -> catalog.Outbox:
    return catalog.Outbox(
        id=uuid7(),
        org_id=principal.org_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        payload={k: str(v) if isinstance(v, uuid.UUID) else v for k, v in payload.items()},
    )


def _secret_ref(ref: SecretReference, principal: Principal, field: str) -> str:
    try:
        return rules.check_secret_ref(ref.secret_ref, principal.org_id)
    except rules.SecretRefOutsideOrg as exc:
        raise Problem(
            422,
            "validation_failed",
            "The secret reference is not a path of your organisation in OpenBao (BR-14).",
            errors=[FieldError(field=f"{field}.secret_ref", code="secret_ref", message=str(exc))],
        ) from exc


# --- sources ------------------------------------------------------------------------------------


async def _source(
    session: AsyncSession,
    principal: Principal,
    space: uuid.UUID,
    source_id: uuid.UUID,
    *,
    lock: bool = False,
) -> catalog.Source:
    query = select(catalog.Source).where(
        catalog.Source.id == source_id,
        catalog.Source.org_id == principal.org_id,
        catalog.Source.data_space_id == space,
        catalog.Source.deleted_at.is_(None),
    )
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise Problem(404, "not_found", "Source not found.")
    return row


async def _check_calendar(
    session: AsyncSession, principal: Principal, calendar_id: uuid.UUID | None
) -> None:
    if calendar_id is None:
        return
    found = await session.scalar(
        select(org.WorkCalendar.id).where(
            org.WorkCalendar.id == calendar_id, org.WorkCalendar.org_id == principal.org_id
        )
    )
    if found is None:
        raise Problem(
            422,
            "validation_failed",
            "work_calendar_id is not a work calendar of your organisation.",
            errors=[FieldError(field="work_calendar_id", code="not_found", message="Unknown.")],
        )


@router.get(
    "/v1/sources",
    response_model=Page[SourceView],
    **endpoint("GET", "/v1/sources", operation_id="listSources"),
)
async def list_sources(
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(
            order_by(
                "created_at", "updated_at", "name", "kind", "status", default="created_at desc"
            )
        ),
    ],
    status: str | None = None,
    kind: str | None = None,
) -> Page[SourceView]:
    src = catalog.Source
    query = select(src).where(
        src.org_id == principal.org_id, src.data_space_id == space, src.deleted_at.is_(None)
    )
    if status:
        query = query.where(src.status == status)
    if kind:
        query = query.where(src.kind == kind)
    query = keyset(
        query,
        columns={
            "created_at": src.created_at,
            "updated_at": src.updated_at,
            "name": src.name,
            "kind": src.kind,
            "status": src.status,
        },
        tiebreak=src.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    return Page(
        items=[SourceView.of(r) for r in rows],
        next_page_token=token,
    )


@router.post(
    "/v1/sources",
    status_code=201,
    response_model=SourceView,
    **endpoint("POST", "/v1/sources", operation_id="createSource"),
)
async def create_source(
    body: SourceCreate, principal: CurrentPrincipal, space: CurrentDataSpace
) -> SourceView:
    if body.mode == "federated" and body.kind not in rules.QUERYABLE_KINDS:
        # UC-04-01 step 3: an API, file, document or CDC source is ingest only.
        raise Problem(
            422,
            "business_rule_violated",
            f"A {body.kind} source cannot be queried in place; use mode ingest (UC-04-01).",
        )
    secret_ref = _secret_ref(body.credentials, principal, "credentials") if body.credentials else ""
    writeback_ref = (
        _secret_ref(body.writeback_credentials, principal, "writeback_credentials")
        if body.writeback_credentials
        else None
    )
    values: dict[str, Any] = {
        "name": body.name.strip(),
        "kind": body.kind,
        "host": body.host,
        "port": body.port,
        "database_name": body.database_name,
        "mode": body.mode or rules.default_mode(body.kind),
        "freshness_sla": body.freshness_sla,
        "scan_window": body.scan_window,
        "work_calendar_id": body.work_calendar_id,
    }
    # Unset optional columns keep their DDL defaults (BR-99: 4 concurrent queries, …).
    for name in ("authority_level", "max_concurrency", "query_timeout_s", "scan_schemas"):
        value = getattr(body, name)
        if value is not None:
            values[name] = value
    async with transaction(ROLE, principal) as session:
        await _check_calendar(session, principal, body.work_calendar_id)
        # Saved as draft: nothing is read from the source before its connection test passes
        # (UC-04-01 step 2); the duplicate name in the Data Space is the UNIQUE's 409.
        row = catalog.Source(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=space,
            secret_ref=secret_ref,
            writeback_secret_ref=writeback_ref,
            status="draft",
            created_by=principal.user_id,
            updated_by=principal.user_id,
            **values,
        )
        session.add(row)
        session.add(
            _event(
                principal,
                "source",
                row.id,
                "source.created",
                source_id=row.id,
                data_space_id=space,
                kind=row.kind,
                mode=row.mode,
                status=row.status,
            )
        )
        await session.flush()
        await session.refresh(row)
        return SourceView.of(row)


@router.patch(
    "/v1/sources/{id}",
    response_model=SourceView,
    **endpoint("PATCH", "/v1/sources/{id}", operation_id="updateSource"),
)
async def update_source(
    body: SourcePatch, source_id: PathId, principal: CurrentPrincipal, space: CurrentDataSpace
) -> SourceView:
    sent = body.model_fields_set
    for name in ("name", "authority_level", "max_concurrency", "query_timeout_s", "scan_schemas"):
        if name in sent and getattr(body, name) is None:
            raise Problem(422, "validation_failed", f"{name} cannot be null.")
    if "credentials" in sent and body.credentials is None:
        raise Problem(422, "validation_failed", "credentials cannot be removed, only replaced.")
    new_ref = _secret_ref(body.credentials, principal, "credentials") if body.credentials else None
    async with transaction(ROLE, principal) as session:
        row = await _source(session, principal, space, source_id, lock=True)
        previous = row.status
        changed: list[str] = []
        for name in (
            "name",
            "authority_level",
            "freshness_sla",
            "max_concurrency",
            "query_timeout_s",
            "scan_schemas",
            "scan_window",
        ):
            if name in sent:
                value = getattr(body, name)
                if name == "name":
                    value = value.strip()
                if getattr(row, name) != value:
                    setattr(row, name, value)
                    changed.append(name)
        if body.status is not None and body.status != row.status:
            refused = rules.status_change(
                row.status, body.status, tested=row.last_checked_at is not None
            )
            if refused:
                raise Problem(409, "invalid_state", refused)
            row.status = body.status
            changed.append("status")
        if new_ref is not None and new_ref != row.secret_ref:
            if row.status == "archived":
                raise Problem(409, "invalid_state", "An archived source cannot change account.")
            row.secret_ref = new_ref
            # Another account may hold other privileges (UC-04-01 E1): nothing is read with it
            # until it passes a connection test again.
            row.status = "draft"
            row.status_reason = "credentials_changed"
            row.last_checked_at = None
            row.db_version = None
            changed.append("credentials")
        if changed:
            row.updated_at, row.updated_by = _now(), principal.user_id
            session.add(
                _event(
                    principal,
                    "source",
                    row.id,
                    "source.updated",
                    source_id=row.id,
                    data_space_id=row.data_space_id,
                    fields=sorted(changed),
                    status=row.status,
                    previous_status=previous,
                )
            )
        await session.flush()
        await session.refresh(row)
        return SourceView.of(row)


@router.post(
    "/v1/sources/{id}:test",
    status_code=202,
    response_model=Accepted,
    **endpoint("POST", "/v1/sources/{id}:test", operation_id="testSource"),
)
async def test_source(
    source_id: PathId, principal: CurrentPrincipal, space: CurrentDataSpace, response: Response
) -> Accepted:
    async with transaction(ROLE, principal) as session:
        row = await _source(session, principal, space, source_id, lock=True)
        if row.status == "archived":
            raise Problem(409, "invalid_state", "An archived source is not tested.")
        previous = row.status
        # A source in use stays usable while it is re-checked; one never passed is "testing".
        if row.status in ("draft", "needs_fix"):
            row.status = "testing"
            row.updated_at, row.updated_by = _now(), principal.user_id
        # The connector worker opens the connection with the account at secret_ref and checks
        # TLS, SELECT, no write privilege, latency and version (UC-04-01 step 2); it writes
        # status, status_reason, db_version and last_checked_at back on the source.
        session.add(
            _event(
                principal,
                "source",
                row.id,
                "source.test_requested",
                source_id=row.id,
                data_space_id=row.data_space_id,
                kind=row.kind,
                status=row.status,
                previous_status=previous,
                requested_by=principal.user_id,
            )
        )
    response.headers["Location"] = f"/v1/sources?status={row.status}"
    return Accepted(id=row.id, status=row.status, poll_after_ms=POLL_AFTER_MS)


async def _skip_days(
    session: AsyncSession, principal: Principal, calendar_id: uuid.UUID | None
) -> tuple[datetime.tzinfo, Any]:
    """The calendar's zone and a predicate for its non-working days (UC-04-05 step 2)."""
    if calendar_id is None:
        return zoneinfo.ZoneInfo(DEFAULT_ZONE), lambda _: False
    calendar = await session.scalar(
        select(org.WorkCalendar).where(org.WorkCalendar.id == calendar_id)
    )
    if calendar is None:
        return zoneinfo.ZoneInfo(DEFAULT_ZONE), lambda _: False
    try:
        zone: datetime.tzinfo = zoneinfo.ZoneInfo(calendar.time_zone)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        zone = zoneinfo.ZoneInfo(DEFAULT_ZONE)
    weekend = set(calendar.weekend_days or [])
    today = _now().date()
    overrides = {
        d.day: d.kind
        for d in (
            await session.scalars(
                select(org.WorkCalendarDay).where(
                    org.WorkCalendarDay.calendar_id == calendar.id,
                    org.WorkCalendarDay.day >= today - datetime.timedelta(days=1),
                )
            )
        ).all()
    }

    def skip(day: datetime.date) -> bool:
        kind = overrides.get(day)
        if kind == "holiday":
            return True
        if kind == "working_override":
            return False
        return day.isoweekday() in weekend

    return zone, skip


def _cron(expression: str | None, field: str) -> rules.Cron | None:
    if not expression:
        return None
    try:
        return rules.parse_cron(expression)
    except rules.CronError as exc:
        raise Problem(
            422,
            "validation_failed",
            f"{field} is not a valid cron expression.",
            errors=[FieldError(field=field, code="cron", message=str(exc))],
        ) from exc


@router.put(
    "/v1/sources/{id}/scan-schedule",
    response_model=SourceSchedule,
    **endpoint("PUT", "/v1/sources/{id}/scan-schedule", operation_id="setSourceScanSchedule"),
)
async def set_scan_schedule(
    body: ScheduleUpdate, source_id: PathId, principal: CurrentPrincipal, space: CurrentDataSpace
) -> SourceSchedule:
    scan = _cron(body.scan_cron, "scan_cron")
    profile = _cron(body.profile_cron, "profile_cron")
    async with transaction(ROLE, principal) as session:
        row = await _source(session, principal, space, source_id, lock=True)
        if row.status == "archived":
            raise Problem(409, "invalid_state", "An archived source has no schedule.")
        await _check_calendar(session, principal, body.work_calendar_id)
        row.scan_cron = body.scan_cron or None
        row.profile_cron = body.profile_cron or None
        row.scan_window = body.scan_window
        row.work_calendar_id = body.work_calendar_id
        row.updated_at, row.updated_by = _now(), principal.user_id
        # The connector worker turns this into the Temporal schedule (UC-04-05 step 4).
        session.add(
            _event(
                principal,
                "source",
                row.id,
                "source.scan_schedule_changed",
                source_id=row.id,
                data_space_id=row.data_space_id,
                scan_cron=row.scan_cron,
                profile_cron=row.profile_cron,
                scan_window=row.scan_window,
                work_calendar_id=row.work_calendar_id,
            )
        )
        zone, skip = await _skip_days(session, principal, row.work_calendar_id)
        await session.flush()
        await session.refresh(row)
        now = _now()
        return SourceSchedule(
            **SourceView.of(row).model_dump(),
            next_scans=rules.next_runs(scan, now, NEXT_RUNS, zone=zone, skip_day=skip)
            if scan
            else [],
            next_profiles=rules.next_runs(profile, now, NEXT_RUNS, zone=zone, skip_day=skip)
            if profile
            else [],
        )


@router.post(
    "/v1/sources/{id}/scans",
    status_code=202,
    response_model=Accepted,
    **endpoint("POST", "/v1/sources/{id}/scans", operation_id="startSourceScan"),
)
async def start_scan(
    source_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    response: Response,
    body: ScanRequest = ScanRequest(),
) -> Accepted:
    async with transaction(ROLE, principal) as session:
        source = await _source(session, principal, space, source_id)
        if source.status not in SCANNABLE:
            raise Problem(
                409,
                "invalid_state",
                f"The source is {source.status}; only a source that passed its test is scanned.",
            )
        # BR-103: a second running scan of the same kind is the partial UNIQUE's 409 conflict.
        run = catalog.ScanRun(
            id=uuid7(),
            org_id=principal.org_id,
            source_id=source.id,
            kind=body.kind,
            trigger="manual",
        )
        session.add(run)
        await session.flush()
        session.add(
            _event(
                principal,
                "scan_run",
                run.id,
                "scan_run.created",
                scan_run_id=run.id,
                source_id=source.id,
                data_space_id=source.data_space_id,
                kind=run.kind,
                trigger=run.trigger,
                tables=body.tables,
            )
        )
    response.headers["Location"] = f"/v1/scan-runs/{run.id}"
    return Accepted(id=run.id, status="running", poll_after_ms=POLL_AFTER_MS)


@router.get(
    "/v1/scan-runs/{id}",
    response_model=ScanRunView,
    **endpoint("GET", "/v1/scan-runs/{id}", operation_id="getScanRun"),
)
async def get_scan_run(
    run_id: PathId, principal: CurrentPrincipal, space: CurrentDataSpace
) -> ScanRunView:
    async with transaction(ROLE, principal) as session:
        run = await session.scalar(
            select(catalog.ScanRun)
            .join(catalog.Source, catalog.Source.id == catalog.ScanRun.source_id)
            .where(
                catalog.ScanRun.id == run_id,
                catalog.ScanRun.org_id == principal.org_id,
                catalog.Source.data_space_id == space,
                catalog.Source.deleted_at.is_(None),
            )
        )
        if run is None:
            raise Problem(404, "not_found", "Scan run not found.")
        return ScanRunView.model_validate(run, from_attributes=True)


# --- datasets -----------------------------------------------------------------------------------


def _visible_datasets(principal: Principal, space: uuid.UUID) -> Select[tuple[catalog.Dataset]]:
    ds = catalog.Dataset
    return select(ds).where(
        ds.org_id == principal.org_id,
        ds.data_space_id == space,
        ds.deleted_at.is_(None),
        # BR-111: a personal table is its owner's only.
        or_(ds.scope == "data_space", ds.created_by == principal.user_id),
    )


async def _dataset(
    session: AsyncSession,
    principal: Principal,
    space: uuid.UUID,
    dataset_id: uuid.UUID,
    *,
    lock: bool = False,
) -> catalog.Dataset:
    query = _visible_datasets(principal, space).where(catalog.Dataset.id == dataset_id)
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise Problem(404, "not_found", "Dataset not found.")
    return row


@router.get(
    "/v1/datasets",
    response_model=Page[DatasetView],
    **endpoint("GET", "/v1/datasets", operation_id="listDatasets"),
)
async def list_datasets(
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(
            order_by(
                "created_at",
                "updated_at",
                "table_name",
                "schema_name",
                default="created_at desc",
            )
        ),
    ],
    source_id: uuid.UUID | None = None,
    enabled: bool | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    origin: str | None = None,
) -> Page[DatasetView]:
    ds = catalog.Dataset
    query = _visible_datasets(principal, space)
    if source_id is not None:
        query = query.where(ds.source_id == source_id)
    if enabled is not None:
        query = query.where(ds.enabled.is_(enabled))
    if origin:
        query = query.where(ds.origin == origin)
    if q and q.strip():
        like = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.where(
            or_(
                ds.table_name.ilike(like, escape="\\"),
                ds.schema_name.ilike(like, escape="\\"),
                ds.display_name.ilike(like, escape="\\"),
                ds.description.ilike(like, escape="\\"),
            )
        )
    query = keyset(
        query,
        columns={
            "created_at": ds.created_at,
            "updated_at": ds.updated_at,
            "table_name": ds.table_name,
            "schema_name": ds.schema_name,
        },
        tiebreak=ds.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    return Page(
        items=[DatasetView.model_validate(r, from_attributes=True) for r in rows],
        next_page_token=token,
    )


async def _columns(session: AsyncSession, dataset_id: uuid.UUID) -> list[catalog.ColumnMeta]:
    return list(
        (
            await session.scalars(
                select(catalog.ColumnMeta)
                .where(
                    catalog.ColumnMeta.dataset_id == dataset_id,
                    catalog.ColumnMeta.removed_in_version.is_(None),
                )
                .order_by(catalog.ColumnMeta.ordinal, catalog.ColumnMeta.name)
            )
        ).all()
    )


async def _detail(session: AsyncSession, row: catalog.Dataset) -> DatasetDetail:
    columns = await _columns(session, row.id)
    profiles: dict[uuid.UUID, catalog.ColumnProfile] = {}
    if columns:
        cp = catalog.ColumnProfile
        latest = (
            select(cp)
            .where(cp.column_id.in_([c.id for c in columns]))
            .order_by(cp.column_id, cp.created_at.desc(), cp.id.desc())
            .distinct(cp.column_id)
        )
        profiles = {p.column_id: p for p in (await session.scalars(latest)).all()}
    views = []
    for column in columns:
        view = ColumnView.model_validate(column, from_attributes=True)
        profile = profiles.get(column.id)
        if profile is not None:
            pv = ColumnProfileView.model_validate(profile, from_attributes=True)
            if column.pii_label:
                # UC-04-02 step 4: no value of a personal-data column leaves the catalogue.
                pv = pv.model_copy(
                    update={
                        "top_values": rules.mask_profile_values(pv.top_values),
                        "min_value": None,
                        "max_value": None,
                    }
                )
            view.profile = pv
        views.append(view)
    return DatasetDetail(
        **DatasetView.model_validate(row, from_attributes=True).model_dump(), columns=views
    )


@router.get(
    "/v1/datasets/{id}",
    response_model=DatasetDetail,
    **endpoint("GET", "/v1/datasets/{id}", operation_id="getDataset"),
)
async def get_dataset(
    dataset_id: PathId, principal: CurrentPrincipal, space: CurrentDataSpace
) -> DatasetDetail:
    async with transaction(ROLE, principal) as session:
        row = await _dataset(session, principal, space, dataset_id)
        return await _detail(session, row)


@router.patch(
    "/v1/datasets/{id}",
    response_model=DatasetDetail,
    **endpoint("PATCH", "/v1/datasets/{id}", operation_id="updateDataset"),
)
async def update_dataset(
    body: DatasetPatch, dataset_id: PathId, principal: CurrentPrincipal, space: CurrentDataSpace
) -> DatasetDetail:
    sent = body.model_fields_set
    for name in ("enabled", "description_status"):
        if name in sent and getattr(body, name) is None:
            raise Problem(422, "validation_failed", f"{name} cannot be null.")
    async with transaction(ROLE, principal) as session:
        row = await _dataset(session, principal, space, dataset_id, lock=True)
        columns = await _columns(session, row.id)
        by_id = {c.id: c for c in columns}
        names = {c.name for c in columns}
        changed: list[str] = []
        if "steward_id" in sent and body.steward_id is not None:
            active = await session.scalar(
                select(org.AppUser.id).where(
                    org.AppUser.id == body.steward_id,
                    org.AppUser.org_id == principal.org_id,
                    org.AppUser.status == "active",
                )
            )
            if active is None:
                raise Problem(422, "validation_failed", "steward_id is not an active user.")
        for name in ("business_date_col", "knowledge_time_col"):
            value = getattr(body, name)
            if name in sent and value is not None and value not in names:
                raise Problem(
                    422,
                    "validation_failed",
                    f"{name} must name a current column of the table.",
                    errors=[FieldError(field=name, code="unknown_column", message=value)],
                )
        for name in (
            "enabled",
            "steward_id",
            "display_name",
            "description",
            "description_status",
            "business_date_col",
            "knowledge_time_col",
        ):
            if name in sent and getattr(row, name) != getattr(body, name):
                setattr(row, name, getattr(body, name))
                changed.append(name)
        reopened: list[str] = []
        for patch in body.columns or []:
            column = by_id.get(patch.id)
            if column is None:
                raise Problem(
                    422,
                    "validation_failed",
                    "columns[].id must be a current column of this table.",
                    errors=[FieldError(field="columns.id", code="unknown_column", message="")],
                )
            fields = patch.model_fields_set - {"id"}
            for name in ("description_status", "pii_confirmed"):
                if name in fields and getattr(patch, name) is None:
                    raise Problem(422, "validation_failed", f"columns[].{name} cannot be null.")
            touched = False
            relabelled = "pii_label" in fields and column.pii_label != patch.pii_label
            for name in fields:
                if getattr(column, name) != getattr(patch, name):
                    setattr(column, name, getattr(patch, name))
                    touched = True
            if relabelled and "pii_confirmed" not in fields:
                # A new label has not been confirmed by anyone yet.
                column.pii_confirmed = False
            if touched:
                column.updated_at, column.updated_by = _now(), principal.user_id
                if column.pii_label and not column.pii_confirmed:
                    reopened.append(column.name)
                if "columns" not in changed:
                    changed.append("columns")
        # Checked when this change enables the table or leaves one of its columns labelled and
        # unconfirmed: confirming the masking one column at a time stays possible.
        if row.enabled and ("enabled" in changed or reopened):
            unconfirmed = [c.name for c in columns if c.pii_label and not c.pii_confirmed]
            if unconfirmed:
                # UC-04-04 step 3: a table enters the context only after its personal-data
                # columns' masking is confirmed.
                raise Problem(
                    422,
                    "business_rule_violated",
                    "Confirm the masking of the personal-data columns before enabling the table: "
                    + ", ".join(sorted(unconfirmed)),
                )
        if changed:
            row.updated_at, row.updated_by = _now(), principal.user_id
            # The context layer adds or drops an enabled table (UC-04-04 step 4, BR-102).
            session.add(
                _event(
                    principal,
                    "dataset",
                    row.id,
                    "dataset.updated",
                    dataset_id=row.id,
                    data_space_id=row.data_space_id,
                    source_id=row.source_id,
                    fields=sorted(changed),
                    enabled=row.enabled,
                    steward_id=row.steward_id,
                )
            )
        await session.flush()
        await session.refresh(row)
        return await _detail(session, row)


@router.get(
    "/v1/datasets/{id}/versions",
    response_model=Page[DatasetVersionView],
    **endpoint("GET", "/v1/datasets/{id}/versions", operation_id="listDatasetVersions"),
)
async def list_dataset_versions(
    dataset_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
) -> Page[DatasetVersionView]:
    dv = catalog.DatasetVersion
    query = select(dv).where(dv.dataset_id == dataset_id, dv.org_id == principal.org_id)
    if page.cursor:
        try:
            query = query.where(dv.version < int(page.cursor["version"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise invalid_page_token() from exc
    query = query.order_by(dv.version.desc()).limit(page.page_size + 1)
    async with transaction(ROLE, principal) as session:
        await _dataset(session, principal, space, dataset_id)
        rows = list((await session.scalars(query)).all())
    more = len(rows) > page.page_size
    rows = rows[: page.page_size]
    return Page(
        items=[DatasetVersionView.model_validate(r, from_attributes=True) for r in rows],
        next_page_token=encode_page_token({"version": rows[-1].version} if more else None),
    )


# --- certification of consolidated versions (BR-156) --------------------------------------------


async def _certifiable(
    session: AsyncSession, principal: Principal, space: uuid.UUID, version_id: uuid.UUID
) -> tuple[catalog.DatasetVersion, catalog.Dataset]:
    """The version, locked, once the caller is shown to be its Data Space's steward."""
    found = (
        await session.execute(
            _visible_datasets(principal, space)
            .add_columns(catalog.DatasetVersion)
            .join(catalog.DatasetVersion, catalog.DatasetVersion.dataset_id == catalog.Dataset.id)
            .where(catalog.DatasetVersion.id == version_id)
            .with_for_update(of=catalog.DatasetVersion)
        )
    ).first()
    if found is None:
        raise Problem(404, "not_found", "Dataset version not found.")
    dataset, version = found
    stewards = (
        await session.execute(
            select(org.DataSpace.steward_id, org.DataSpace.backup_steward_id).where(
                org.DataSpace.id == dataset.data_space_id
            )
        )
    ).first()
    # BR-156: the data steward (AC03) of the Data Space holding the dataset certifies; the
    # steward capability alone is not enough.
    if stewards is None or principal.user_id not in (stewards[0], stewards[1]):
        raise Problem(
            403,
            "policy_denied",
            "Only the Data Space's data steward or backup steward certifies its datasets (BR-156).",
        )
    return version, dataset


async def _record(
    session: AsyncSession,
    principal: Principal,
    version: catalog.DatasetVersion,
    action: str,
    note: str | None = None,
    reason: str | None = None,
) -> CertifiedVersion:
    entry = catalog.DatasetVersionCertification(
        id=uuid7(),
        org_id=principal.org_id,
        dataset_version_id=version.id,
        action=action,
        actor_id=principal.user_id,
        note=note,
        reason=reason,
    )
    session.add(entry)
    # The trigger applies the row to the version and writes the outbox event.
    await session.flush()
    await session.refresh(entry)
    await session.refresh(version)
    return CertifiedVersion(
        **DatasetVersionView.model_validate(version, from_attributes=True).model_dump(),
        certification=CertificationView.model_validate(entry, from_attributes=True),
    )


@router.post(
    "/v1/dataset-versions/{id}:certify",
    response_model=CertifiedVersion,
    **endpoint("POST", "/v1/dataset-versions/{id}:certify", operation_id="certifyDatasetVersion"),
)
async def certify_version(
    version_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    body: CertifyRequest = CertifyRequest(),
) -> CertifiedVersion:
    note = (body.note or "").strip() or None
    async with transaction(ROLE, principal) as session:
        version, dataset = await _certifiable(session, principal, space, version_id)
        if version.created_by == principal.user_id:
            # Four eyes: whoever last edited the consolidation spec does not certify its output.
            raise Problem(
                403,
                "policy_denied",
                "The creator of a dataset version cannot certify it (BR-156).",
            )
        if dataset.origin != "consolidated":
            raise Problem(
                422,
                "business_rule_violated",
                "Only versions of a consolidated dataset are certified (BR-156).",
            )
        if version.certified_at is not None:
            raise Problem(409, "invalid_state", "The version is already certified.")
        return await _record(session, principal, version, "certify", note=note)


@router.post(
    "/v1/dataset-versions/{id}:revoke-certification",
    response_model=CertifiedVersion,
    **endpoint(
        "POST",
        "/v1/dataset-versions/{id}:revoke-certification",
        operation_id="revokeDatasetVersionCertification",
    ),
)
async def revoke_certification(
    body: RevokeRequest, version_id: PathId, principal: CurrentPrincipal, space: CurrentDataSpace
) -> CertifiedVersion:
    async with transaction(ROLE, principal) as session:
        version, _ = await _certifiable(session, principal, space, version_id)
        if version.certified_at is None:
            raise Problem(409, "invalid_state", "The version is not certified.")
        return await _record(session, principal, version, "revoke", reason=body.reason)
