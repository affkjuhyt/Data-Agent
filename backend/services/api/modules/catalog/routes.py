"""A2 group "Nguồn và danh mục": sources, scans, the dataset catalogue and the certification of
consolidated dataset versions (UC-04-01..05, UC-14-05).

Every create and status change writes its event to catalog.outbox in the same transaction (D1
rule 8). Work that reaches the source itself (connection test, scan, schedule in Temporal) is the
connector worker's: the route records the request and its event and answers 202 (A1.6).
Certification events are written by the trigger on catalog.dataset_version_certification, not
here, so there is exactly one per history row.
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs import cron, quality_vocab
from libs.dataagent_db import uuid7
from libs.dataagent_db.models import catalog, org
from libs.source_drivers import registry
from services.api.catalogue import endpoint
from services.api.kernel.approval import two_people
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
from services.api.modules.audit import record_self_approval
from services.api.modules.catalog import rules
from services.api.modules.catalog.public import dataset_visible, working_days
from services.api.modules.catalog.rest_try import (
    WAIT_SECONDS,
    RequestRefused,
    RestTries,
    TriesBusy,
    check_rest_key,
    checked_request,
    rest_tries,
)
from services.api.modules.catalog.schemas import (
    Accepted,
    ActivateRequest,
    ApiConfigPut,
    ApiConfigView,
    CertificationView,
    CertifiedVersion,
    CertifyRequest,
    ColumnProfileView,
    ColumnStatsView,
    ColumnView,
    ConnectorForm,
    ConnectorView,
    ContractPut,
    ContractRule,
    ContractView,
    CursorCandidateView,
    DatasetDeriveRequest,
    DatasetDetail,
    DatasetPatch,
    DatasetVersionView,
    DatasetView,
    DerivedDatasetAccepted,
    DerivedTableCreate,
    DerivedTableView,
    FreshnessSla,
    RestTryRequest,
    RestTryResult,
    RevokeRequest,
    RoleMapping,
    ScanRequest,
    ScanRunView,
    ScheduleUpdate,
    SecretReference,
    SecretRefView,
    SnapshotView,
    SourceCreate,
    SourcePatch,
    SourceSchedule,
    SourceView,
)
from services.api.modules.catalog.snapshot_links import snapshot_links
from services.api.modules.governance.public import dataset_has_access_policies
from services.api.modules.org import capabilities_of

router = APIRouter()
ROLE = "svc_catalog"
PathId = Annotated[uuid.UUID, Path(alias="id")]
POLL_AFTER_MS = 2_000
NEXT_RUNS = 5
# A source is scanned while it can be read (UC-04-02 precondition: the source is active).
SCANNABLE = ("active", "needs_fix")

# DF-12: one card per kind a driver reads (libs/source_drivers/registry.py), then the connectors
# with a path of their own (REST, files). The picker offered Snowflake, which nothing could read.
CONNECTORS: tuple[ConnectorView, ...] = (
    *(
        ConnectorView(
            code=d.kind,
            name=d.name,
            category=d.category,
            subtitle=d.subtitle,
            connected_count=0,
            badge=None,
            form=ConnectorForm(database_label=d.database_label, default_port=d.default_port),
            prep_notes=list(d.prep_notes),
            sample_sql=d.sample_sql,
        )
        for d in registry.REGISTRY.values()
    ),
    ConnectorView(
        code="rest_api",
        name="REST API",
        category="app",
        subtitle="Bằng HTTP hoặc MCP",
        connected_count=0,
        badge="new",
        form=None,
        prep_notes=["Lưu API key trong OpenBao; không nhập secret trực tiếp vào Data Agent."],
        sample_sql=None,
    ),
    ConnectorView(
        code="file",
        name="Tệp CSV / thư mục",
        category="file",
        subtitle="Tải tệp theo lịch",
        connected_count=0,
        badge="upload",
        form=None,
        prep_notes=["Chọn thư mục hoặc tải tệp ở bước tiếp theo."],
        sample_sql=None,
    ),
)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


@router.get(
    "/v1/connectors",
    response_model=Page[ConnectorView],
    **endpoint("GET", "/v1/connectors", operation_id="listConnectors"),
)
async def list_connectors(principal: CurrentPrincipal) -> Page[ConnectorView]:
    """Return the safe connector catalogue and, per kind, the connected sources: those of the
    open Data Space when the caller names one (the organisation's otherwise), not drafts or
    archived ones. Counting the whole organisation, drafts included, put "Đã kết nối · 3" over
    a Data Space with no source."""
    source = catalog.Source
    connected = [
        source.org_id == principal.org_id,
        source.deleted_at.is_(None),
        source.status.not_in(("draft", "archived")),
    ]
    if principal.data_space_id is not None:
        connected.append(source.data_space_id == principal.data_space_id)
    async with transaction(ROLE, principal) as session:
        counts = dict(
            (
                await session.execute(
                    select(source.kind, func.count(source.id))
                    .where(*connected)
                    .group_by(source.kind)
                )
            )
            .tuples()
            .all()
        )
    return Page(
        items=[
            item.model_copy(update={"connected_count": counts.get(item.code, 0)})
            for item in CONNECTORS
        ],
        next_page_token="",
    )


@router.post(
    "/v1/connectors/rest:try",
    response_model=RestTryResult,
    **endpoint("POST", "/v1/connectors/rest:try", operation_id="tryRestConnector"),
)
async def try_rest_connector(
    body: RestTryRequest,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    tries: Annotated[RestTries | None, Depends(rest_tries)],
) -> RestTryResult:
    """One real call of the request, made by the connector worker and answered here; nothing is
    written. The worker reads the key from OpenBao and calls a public https address only, with
    no redirect followed."""
    try:
        request = checked_request(body.request)
    except RequestRefused as exc:
        raise Problem(422, "validation_failed", str(exc)) from exc
    try:
        # The key goes to a host the caller types: only a REST key of this Data Space may.
        check_rest_key(request["secret_ref"], principal.org_id, space)
    except RequestRefused as exc:
        raise Problem(422, "validation_failed", str(exc)) from exc
    if tries is None:
        raise Problem(503, "source_unavailable", "Chưa cấu hình Redis cho lần gọi thử.")
    job = {
        "id": str(uuid7()),
        "org_id": str(principal.org_id),
        "data_space_id": str(space),
        "requested_by": str(principal.user_id),
        "request": request,
    }
    try:
        answer = await tries.run(job, WAIT_SECONDS)
    except TriesBusy as exc:
        raise Problem(503, "source_unavailable", "Đang có nhiều lần gọi thử; thử lại sau.") from exc
    if answer is None:
        raise Problem(
            503, "source_unavailable", "Không có worker nào nhận lần gọi thử; thử lại sau."
        )
    if "ok" in answer:
        return RestTryResult.model_validate(answer["ok"])
    if "refused" in answer:
        raise Problem(422, "business_rule_violated", str(answer["refused"]))
    raise Problem(503, "source_unavailable", str(answer.get("failed") or "Gọi thử không được."))


@router.get(
    "/v1/secret-refs",
    response_model=Page[SecretRefView],
    **endpoint("GET", "/v1/secret-refs", operation_id="listSecretRefs"),
)
async def list_secret_refs(
    principal: CurrentPrincipal, space: CurrentDataSpace
) -> Page[SecretRefView]:
    """List labels for references already used in the selected Data Space.

    OpenBao is deliberately not queried here: the API only exposes a reference that can be
    selected by the connection form, never credentials or arbitrary vault paths.
    """
    source = catalog.Source
    async with transaction(ROLE, principal) as session:
        rows = (
            await session.execute(
                select(source.secret_ref, func.min(source.name))
                .where(
                    source.org_id == principal.org_id,
                    source.data_space_id == space,
                    source.deleted_at.is_(None),
                    source.secret_ref != "",
                )
                .group_by(source.secret_ref)
                .order_by(source.secret_ref)
            )
        ).all()
    return Page(
        items=[SecretRefView(ref=ref, label=str(label)) for ref, label in rows],
        next_page_token="",
    )


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


def _checked_ref(ref: str, principal: Principal, field: str) -> str:
    try:
        return rules.check_secret_ref(ref, principal.org_id)
    except rules.SecretRefOutsideOrg as exc:
        raise Problem(
            422,
            "validation_failed",
            "The secret reference is not a path of your organisation in OpenBao (BR-14).",
            errors=[FieldError(field=f"{field}.secret_ref", code="secret_ref", message=str(exc))],
        ) from exc


async def _credentials_path(
    cred: SecretReference, principal: Principal, source_id: uuid.UUID, field: str
) -> str:
    """The OpenBao path to store. A typed account is written first; a path is only checked."""
    if cred.secret_ref is not None:
        return _checked_ref(cred.secret_ref, principal, field)
    from services.api.modules.catalog.vault import SecretStoreError, put_account

    try:
        return await asyncio.to_thread(
            put_account,
            principal.org_id,
            source_id,
            cred.username or "",
            cred.password or "",
            writeback=field == "writeback_credentials",
        )
    except SecretStoreError as exc:
        raise Problem(503, "source_unavailable", "The secret store is unavailable.") from exc


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


@router.get(
    "/v1/sources/{id}",
    response_model=SourceView,
    **endpoint("GET", "/v1/sources/{id}", operation_id="getSource"),
)
async def get_source(
    source_id: PathId, principal: CurrentPrincipal, space: CurrentDataSpace
) -> SourceView:
    # Where :test's Location points (A3, 12/10/2026): the outcome the connector worker wrote.
    async with transaction(ROLE, principal) as session:
        return SourceView.of(await _source(session, principal, space, source_id))


def _api_config(row: catalog.Source) -> ApiConfigView:
    config = row.conn_options if isinstance(row.conn_options, dict) else {}
    saved_value = config.get("api")
    saved: dict[str, Any] = saved_value if isinstance(saved_value, dict) else {}
    request_value = saved.get("request")
    request: dict[str, Any] = request_value if isinstance(request_value, dict) else {}
    mapping_value = saved.get("mapping")
    mapping: list[dict[str, Any]] = (
        [item for item in mapping_value if isinstance(item, dict)]
        if isinstance(mapping_value, list)
        else []
    )
    health_value = saved.get("health")
    health: dict[str, Any] | None = health_value if isinstance(health_value, dict) else None
    return ApiConfigView(
        source_id=row.id,
        name=row.name,
        request=request,
        mapping=mapping,
        health=health,
    )


@router.get(
    "/v1/sources/{id}/api-config",
    response_model=ApiConfigView,
    **endpoint("GET", "/v1/sources/{id}/api-config", operation_id="getSourceApiConfig"),
)
async def get_source_api_config(
    source_id: PathId, principal: CurrentPrincipal, space: CurrentDataSpace
) -> ApiConfigView:
    async with transaction(ROLE, principal) as session:
        row = await _source(session, principal, space, source_id)
        if row.kind != "rest_api":
            raise Problem(422, "business_rule_violated", "The source is not a REST API source.")
        return _api_config(row)


@router.put(
    "/v1/sources/{id}/api-config",
    response_model=ApiConfigView,
    **endpoint("PUT", "/v1/sources/{id}/api-config", operation_id="putSourceApiConfig"),
)
async def put_source_api_config(
    body: ApiConfigPut,
    source_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
) -> ApiConfigView:
    if not str(body.request.get("url", "")).startswith("https://"):
        raise Problem(422, "validation_failed", "request.url must start with https://.")
    async with transaction(ROLE, principal) as session:
        row = await _source(session, principal, space, source_id, lock=True)
        if row.kind != "rest_api":
            raise Problem(422, "business_rule_violated", "The source is not a REST API source.")
        ref = body.request.get("secret_ref")
        if ref:
            # Every run of the source sends this key, as the try does: the same rule.
            try:
                check_rest_key(ref, principal.org_id, row.data_space_id)
            except RequestRefused as exc:
                raise Problem(422, "validation_failed", str(exc)) from exc
        options = dict(row.conn_options) if isinstance(row.conn_options, dict) else {}
        options["api"] = {"request": body.request, "mapping": body.mapping}
        row.conn_options = options
        row.updated_by = principal.user_id
        await session.flush()
        await session.refresh(row)
        return _api_config(row)


@router.post(
    "/v1/sources",
    status_code=201,
    response_model=SourceView,
    **endpoint("POST", "/v1/sources", operation_id="createSource"),
)
async def create_source(
    body: SourceCreate, principal: CurrentPrincipal, space: CurrentDataSpace
) -> SourceView:
    if body.kind not in rules.REGISTRABLE_KINDS:
        # DF-12: nothing reads it, so it is not registered (the picker does not offer it).
        raise Problem(
            422,
            "validation_failed",
            f"No connector reads a {body.kind} source yet.",
            errors=[
                FieldError(field="kind", code="unsupported", message="no driver for this kind")
            ],
        )
    if body.mode == "federated" and body.kind not in rules.QUERYABLE_KINDS:
        # UC-04-01 step 3: an API, file, document or CDC source is ingest only.
        raise Problem(
            422,
            "business_rule_violated",
            f"A {body.kind} source cannot be queried in place; use mode ingest (UC-04-01).",
        )
    source_id = uuid7()
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
        # After the calendar check, so a rejected body does not store an account. The path
        # itself is a new version (vault.account_ref), never a path a source already uses.
        secret_ref = (
            await _credentials_path(body.credentials, principal, source_id, "credentials")
            if body.credentials
            else ""
        )
        writeback_ref = (
            await _credentials_path(
                body.writeback_credentials, principal, source_id, "writeback_credentials"
            )
            if body.writeback_credentials
            else None
        )
        # Saved as draft: nothing is read from the source before its connection test passes
        # (UC-04-01 step 2); the duplicate name in the Data Space is the UNIQUE's 409.
        row = catalog.Source(
            id=source_id,
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
    for name in (
        "name",
        "authority_level",
        "max_concurrency",
        "query_timeout_s",
        "scan_schemas",
        "writeback_tables",
    ):
        if name in sent and getattr(body, name) is None:
            raise Problem(422, "validation_failed", f"{name} cannot be null.")
    if "credentials" in sent and body.credentials is None:
        raise Problem(422, "validation_failed", "credentials cannot be removed, only replaced.")
    if "role_mapping" in sent and body.role_mapping is None:
        raise Problem(422, "validation_failed", "role_mapping cannot be null; send {} to clear it.")
    # A path is only checked. A typed account is stored after _source locks the row, on a new
    # version path, so a caller who cannot see the source never overwrites its account.
    checked_ref = (
        _checked_ref(body.credentials.secret_ref, principal, "credentials")
        if body.credentials is not None and body.credentials.secret_ref is not None
        else None
    )
    async with transaction(ROLE, principal) as session:
        row = await _source(session, principal, space, source_id, lock=True)
        new_ref = checked_ref
        if body.credentials is not None and body.credentials.secret_ref is None:
            if row.status == "archived":
                raise Problem(409, "invalid_state", "An archived source cannot change account.")
            new_ref = await _credentials_path(body.credentials, principal, source_id, "credentials")
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
            "writeback_tables",
        ):
            if name in sent:
                value = getattr(body, name)
                if name == "name":
                    value = value.strip()
                elif name == "writeback_tables":
                    value = sorted(set(value))
                if getattr(row, name) != value:
                    setattr(row, name, value)
                    changed.append(name)
        if body.role_mapping is not None:
            mapping = await _role_mapping(session, principal, row, body.role_mapping)
            if mapping != (row.role_mapping or {}):
                row.role_mapping = mapping
                changed.append("role_mapping")
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


async def _role_mapping(
    session: AsyncSession, principal: Principal, row: catalog.Source, body: RoleMapping
) -> dict[str, Any]:
    """AE-17: the mapping as stored, {} when it names no one. Only a PostgreSQL source sets a role
    per transaction (SET LOCAL ROLE); every user and group named is of this organisation."""
    users = {str(k): v for k, v in body.users.items()}
    groups = {str(k): v for k, v in body.groups.items()}
    if not users and not groups:
        return {}
    if row.kind != "postgresql":
        raise _invalid_field(
            "role_mapping", f"A role mapping is applied on a PostgreSQL source, not {row.kind}."
        )
    if users:
        known = set(
            (
                await session.scalars(
                    select(org.AppUser.id).where(
                        org.AppUser.org_id == principal.org_id, org.AppUser.id.in_(body.users)
                    )
                )
            ).all()
        )
        if len(known) != len(users):
            raise _invalid_field("role_mapping", "A user in role_mapping.users is not of this org.")
    if groups:
        known = set(
            (
                await session.scalars(
                    select(org.UserGroup.id).where(
                        org.UserGroup.org_id == principal.org_id, org.UserGroup.id.in_(body.groups)
                    )
                )
            ).all()
        )
        if len(known) != len(groups):
            raise _invalid_field(
                "role_mapping", "A group in role_mapping.groups is not of this org."
            )
    return {"users": users, "groups": groups}


def _invalid_field(field: str, detail: str) -> Problem:
    return Problem(
        422,
        "validation_failed",
        detail,
        errors=[FieldError(field=field, code="invalid", message=detail)],
    )


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
    response.headers["Location"] = f"/v1/sources/{row.id}"
    return Accepted(id=row.id, status=row.status, poll_after_ms=POLL_AFTER_MS)


@router.post(
    "/v1/sources/{id}:activate",
    response_model=SourceView,
    **endpoint("POST", "/v1/sources/{id}:activate", operation_id="activateSource"),
)
async def activate_source(
    body: ActivateRequest,
    source_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
) -> SourceView:
    """Enable the tables selected after the initial structure scan."""
    async with transaction(ROLE, principal) as session:
        source = await _source(session, principal, space, source_id, lock=True)
        if source.status not in SCANNABLE:
            raise Problem(409, "invalid_state", "The source must pass its connection test first.")
        # UC-04-04 E1: one source may serve several Data Spaces, each enabling its own tables, so
        # a table may be assigned elsewhere; but only where the caller administers the data too,
        # or this would be a way to put tables into a Data Space they have no right over.
        for target in sorted({t.data_space_id for t in body.tables if t.data_space_id != space}):
            held = await capabilities_of(principal.org_id, principal.user_id, target)
            if not held or "admin_data" not in held:
                raise Problem(
                    403,
                    "forbidden",
                    "tables[].data_space_id names a Data Space whose data you do not administer.",
                )
        owners = {t.owner_id for t in body.tables}
        if owners:
            active = set(
                (
                    await session.scalars(
                        select(org.AppUser.id).where(
                            org.AppUser.id.in_(owners),
                            org.AppUser.org_id == principal.org_id,
                            org.AppUser.status == "active",
                        )
                    )
                ).all()
            )
            if owners - active:
                raise Problem(422, "validation_failed", "tables[].owner_id is not an active user.")
        names = {item.name for item in body.tables}
        datasets = (
            await session.scalars(
                select(catalog.Dataset)
                .where(
                    catalog.Dataset.org_id == principal.org_id,
                    catalog.Dataset.source_id == source.id,
                    catalog.Dataset.data_space_id == space,
                    catalog.Dataset.deleted_at.is_(None),
                )
                .with_for_update()
            )
        ).all()
        # UC-04-04 step 3, as PATCH /v1/datasets/{id} holds it: a table enters the context only
        # once its personal-data columns' masking is confirmed.
        unconfirmed = []
        for dataset in datasets:
            if f"{dataset.schema_name}.{dataset.table_name}" in names and not dataset.enabled:
                pending = [
                    c.name
                    for c in await _columns(session, dataset.id)
                    if c.pii_label and not c.pii_confirmed
                ]
                if pending:
                    unconfirmed.append(
                        f"{dataset.schema_name}.{dataset.table_name} ({', '.join(sorted(pending))})"
                    )
        if unconfirmed:
            raise Problem(
                422,
                "business_rule_violated",
                "Confirm the masking of the personal-data columns before enabling: "
                + "; ".join(sorted(unconfirmed)),
            )
        for dataset in datasets:
            selected = f"{dataset.schema_name}.{dataset.table_name}" in names
            assignment = next(
                (
                    item
                    for item in body.tables
                    if item.name == f"{dataset.schema_name}.{dataset.table_name}"
                ),
                None,
            )
            dataset.enabled = selected
            if selected and assignment is not None:
                dataset.data_space_id = assignment.data_space_id
                dataset.steward_id = assignment.owner_id
                dataset.updated_by = principal.user_id
        # An empty schedule is none, as PUT /v1/sources/{id}/scan-schedule stores it.
        source.scan_cron = body.schema_scan or None
        source.profile_cron = body.profiling or None
        source.updated_at, source.updated_by = _now(), principal.user_id
        session.add(
            _event(
                principal,
                "source",
                source.id,
                "source.activated",
                source_id=source.id,
                data_space_id=source.data_space_id,
                tables=sorted(names),
            )
        )
        await session.flush()
        await session.refresh(source)
        return SourceView.of(source)


def _cron(expression: str | None, field: str) -> str | None:
    if not expression:
        return None
    try:
        return cron.check(expression)
    except cron.CronError as exc:
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
        zone, skip = await working_days(session, row.work_calendar_id)
        await session.flush()
        await session.refresh(row)
        now = _now()
        return SourceSchedule(
            **SourceView.of(row).model_dump(),
            next_scans=cron.next_runs(scan, now, NEXT_RUNS, zone, skip_day=skip) if scan else [],
            next_profiles=cron.next_runs(profile, now, NEXT_RUNS, zone, skip_day=skip)
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
                # The relationships the scan infers are proposed in this person's name, so they
                # reach a reviewer (worker_agent/semantic_review.relationship_created).
                requested_by=principal.user_id,
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
    return select(catalog.Dataset).where(dataset_visible(principal, space))


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
        facts = await _list_facts(session, rows)
    items = []
    for r in rows:
        view = DatasetView.model_validate(r, from_attributes=True)
        view.certified, view.column_count = facts.get(r.id, (False, 0))
        items.append(view)
    return Page(items=items, next_page_token=token)


async def _list_facts(
    session: AsyncSession, rows: list[catalog.Dataset]
) -> dict[uuid.UUID, tuple[bool, int]]:
    """Per dataset of one page: is its version in use certified, and how many columns are left.
    Two grouped reads for the page, not one per dataset."""
    ids = [r.id for r in rows]
    if not ids:
        return {}
    dv, cm = catalog.DatasetVersion, catalog.ColumnMeta
    certified = set(
        (
            await session.scalars(
                select(dv.dataset_id)
                .join(catalog.Dataset, catalog.Dataset.id == dv.dataset_id)
                .where(
                    dv.dataset_id.in_(ids),
                    dv.version == catalog.Dataset.current_version,
                    dv.certified_at.is_not(None),
                )
            )
        ).all()
    )
    counts = dict(
        (
            await session.execute(
                select(cm.dataset_id, func.count())
                .where(cm.dataset_id.in_(ids), cm.removed_in_version.is_(None))
                .group_by(cm.dataset_id)
            )
        )
        .tuples()
        .all()
    )
    return {i: (i in certified, int(counts.get(i, 0))) for i in ids}


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
                        "quantiles": None,
                        "histogram": None,
                        # A pattern names no value, but its lengths and separators are the
                        # value's: the steward reads it in the scan, not every analyst here.
                        "value_patterns": None,
                        "pattern_hint": None,
                    }
                )
            view.profile = pv
        views.append(view)
    facts = await _list_facts(session, [row])
    certified, count = facts.get(row.id, (False, 0))
    candidates = (
        [
            CursorCandidateView(**asdict(c))
            for c in registry.cursor_candidates([v.model_dump() for v in views])
        ]
        # A source's own table: an ingest job's workspace copy is not read with a cursor.
        if row.source_id is not None and row.origin == "source" and row.schema_name != "workspace"
        else []
    )
    return DatasetDetail(
        **DatasetView.model_validate(row, from_attributes=True).model_dump()
        | {"certified": certified, "column_count": count},
        columns=views,
        can_select=row.can_select if row.source_id is not None else None,
        cursor_candidates=candidates,
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


@router.get(
    "/v1/datasets/{id}/columns/{column}",
    response_model=ColumnStatsView,
    **endpoint("GET", "/v1/datasets/{id}/columns/{column}", operation_id="getDatasetColumnStats"),
)
async def get_dataset_column_stats(
    dataset_id: PathId,
    column: Annotated[str, Path(min_length=1, max_length=200)],
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
) -> ColumnStatsView:
    async with transaction(ROLE, principal) as session:
        dataset = await _dataset(session, principal, space, dataset_id)
        meta = await session.scalar(
            select(catalog.ColumnMeta).where(
                catalog.ColumnMeta.dataset_id == dataset.id,
                catalog.ColumnMeta.name == column,
                catalog.ColumnMeta.removed_in_version.is_(None),
            )
        )
        if meta is None:
            raise Problem(404, "not_found", "Column not found.")
        if meta.pii_label:
            raise Problem(403, "masked_column", "Personal-data columns are not profiled.")
        profile = await session.scalar(
            select(catalog.ColumnProfile)
            .where(catalog.ColumnProfile.column_id == meta.id)
            .order_by(catalog.ColumnProfile.created_at.desc(), catalog.ColumnProfile.id.desc())
        )
    if profile is None:
        raise Problem(404, "not_found", "Column profile not found.")
    # DF-10: a number's histogram (20 equal bins over [lo, hi]) and its quantiles, when the scan
    # read them; otherwise the bars are the most frequent values' counts, tallest first.
    quantiles = profile.quantiles if isinstance(profile.quantiles, dict) else {}
    median = quantiles.get("p50")
    hist = profile.histogram if isinstance(profile.histogram, dict) else {}
    bins = [float(b) for b in hist.get("bins") or [] if isinstance(b, int | float)]
    if bins and max(bins) > 0:
        tallest_bin = max(bins)
        return ColumnStatsView(
            name=meta.name,
            type_label=meta.normalized_type or meta.data_type,
            null_ratio=float(profile.null_ratio or 0),
            distinct=int(profile.distinct_est or 0),
            min=profile.min_value or "—",
            max=profile.max_value or "—",
            median="—" if median is None else str(median),
            histogram=[{"height": b / tallest_bin, "flagged": False} for b in bins],
            buckets_text=(
                f"{len(bins)} khoảng đều từ {hist.get('lo')} đến {hist.get('hi')} "
                f"(quét {profile.sample_rows} dòng)"
            ),
        )
    top = [
        t
        for t in (profile.top_values if isinstance(profile.top_values, list) else [])
        if isinstance(t, dict) and isinstance(t.get("count"), int | float)
    ]
    tallest = max((float(t["count"]) for t in top), default=0.0)
    return ColumnStatsView(
        name=meta.name,
        type_label=meta.normalized_type or meta.data_type,
        null_ratio=float(profile.null_ratio or 0),
        distinct=int(profile.distinct_est or 0),
        min=profile.min_value or "—",
        max=profile.max_value or "—",
        median="chưa tính" if median is None else str(median),
        histogram=(
            [{"height": float(t["count"]) / tallest, "flagged": False} for t in top]
            if tallest > 0
            else []
        ),
        buckets_text=(
            "Giá trị phổ biến: " + " · ".join(str(t.get("value")) for t in top[:6])
            if top
            else f"Chưa đủ dữ liệu để lập biểu đồ (quét {profile.sample_rows} dòng)"
        ),
    )


@router.post(
    "/v1/datasets/{id}:derive",
    response_model=DerivedDatasetAccepted,
    status_code=201,
    **endpoint("POST", "/v1/datasets/{id}:derive", operation_id="deriveDataset"),
)
async def derive_dataset(
    body: DatasetDeriveRequest,
    dataset_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
) -> DerivedDatasetAccepted:
    """Register a filtered/deduplicated derived table and let the worker materialize it."""
    async with transaction(ROLE, principal) as session:
        source = await _dataset(session, principal, space, dataset_id)
        derived_id = uuid7()
        table_name = f"derived_{str(derived_id).replace('-', '')[:20]}"
        row = catalog.Dataset(
            id=derived_id,
            org_id=principal.org_id,
            source_id=None,
            data_space_id=space,
            origin="derived",
            schema_name="derived",
            table_name=table_name,
            object_kind="table",
            display_name=f"{source.display_name or source.table_name} · dẫn xuất",
            description=f"Từ {source.schema_name}.{source.table_name}",
            enabled=False,
            steward_id=principal.user_id,
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(row)
        session.add(
            _event(
                principal,
                "dataset",
                row.id,
                "dataset.derive_requested",
                dataset_id=row.id,
                source_dataset_id=source.id,
                data_space_id=space,
                filters=body.filters,
                drop_duplicates=body.drop_duplicates,
                requested_by=principal.user_id,
            )
        )
        await session.flush()
    # The table is built by worker-agent (derived.py): its rows are not known yet.
    return DerivedDatasetAccepted(id=row.id, name=row.display_name or row.table_name)


@router.post(
    "/v1/derived-tables",
    response_model=DerivedTableView,
    status_code=201,
    **endpoint("POST", "/v1/derived-tables", operation_id="createDerivedTable"),
)
async def create_derived_table(
    body: DerivedTableCreate, principal: CurrentPrincipal, space: CurrentDataSpace
) -> DerivedTableView:
    """Register a SQL-derived dataset; materialisation is owned by the catalog worker."""
    if body.data_space_id != space:
        raise Problem(404, "not_found", "Data Space not found.")
    schema_name, table_name = body.name.split(".", 1)
    derived_id = uuid7()
    async with transaction(ROLE, principal) as session:
        row = catalog.Dataset(
            id=derived_id,
            org_id=principal.org_id,
            source_id=None,
            data_space_id=space,
            origin="derived",
            schema_name=schema_name,
            table_name=table_name,
            object_kind="table",
            display_name=body.name,
            description="Bảng dẫn xuất từ SQL",
            enabled=False,
            steward_id=principal.user_id,
            scope="personal" if body.access == "owner_only" else "data_space",
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(row)
        session.add(
            _event(
                principal,
                "dataset",
                derived_id,
                "dataset.sql_derived_requested",
                dataset_id=derived_id,
                data_space_id=space,
                write_mode=body.write_mode,
                refresh=body.refresh,
                access=body.access,
                sql=body.sql,
                requested_by=principal.user_id,
            )
        )
        await session.flush()
    return DerivedTableView(
        id=derived_id,
        name=body.name,
        data_space_id=space,
        write_mode=body.write_mode,
        refresh=body.refresh,
        access=body.access,
        version=1,
    )


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
        ds = catalog.DatasetSnapshot
        snapshots = {
            s.version: s
            for s in (
                await session.scalars(
                    select(ds).where(
                        ds.dataset_id == dataset_id, ds.version.in_([r.version for r in rows])
                    )
                )
            ).all()
        }
    items = []
    for r in rows:
        view = DatasetVersionView.model_validate(r, from_attributes=True)
        found = snapshots.get(r.version)
        if found is not None:
            view.snapshot = SnapshotView.model_validate(found, from_attributes=True)
        items.append(view)
    return Page(
        items=items,
        next_page_token=encode_page_token({"version": rows[-1].version} if more else None),
    )


@router.get(
    "/v1/datasets/{id}/versions/{version}/snapshot",
    response_class=RedirectResponse,
    status_code=302,
    **endpoint(
        "GET", "/v1/datasets/{id}/versions/{version}/snapshot", operation_id="getDatasetSnapshot"
    ),
)
async def get_dataset_snapshot(
    dataset_id: PathId,
    version: Annotated[int, Path(ge=1)],
    request: Request,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
) -> RedirectResponse:
    """DF-05: the version's Parquet file, as a short signed link; 404 when it was skipped (too
    many rows, store down) or is not taken yet.

    The file is the version as written, every column and row: policies apply when it is read
    (libs/snapshot.reader), not here. So only a data administrator gets it, only for a dataset no
    access policy shapes and no column of which is labelled personal (security review of
    c75d5f67), and every link handed out is in the audit trail."""
    ds = catalog.DatasetSnapshot
    async with transaction(ROLE, principal) as session:
        await _dataset(session, principal, space, dataset_id)
        found = await session.scalar(
            select(ds).where(ds.dataset_id == dataset_id, ds.version == version)
        )
        personal = await session.scalar(
            select(catalog.ColumnMeta.id)
            .where(
                catalog.ColumnMeta.dataset_id == dataset_id,
                catalog.ColumnMeta.pii_label.is_not(None),
            )
            .limit(1)
        )
    if found is None or found.status != "ok" or not found.object_key:
        raise Problem(
            404,
            "not_found",
            "This version has no snapshot file" + (" (skipped)." if found is not None else " yet."),
        )
    if personal is not None or await dataset_has_access_policies(principal, dataset_id):
        raise Problem(
            403,
            "policy_denied",
            "This dataset has personal columns or access policies: its snapshot is read through "
            "the query path, which applies them, and is not handed out as a file.",
        )
    links = getattr(request.app.state, "snapshot_links", None) or snapshot_links()
    link = await links.sign(found.object_key)
    if link is None:
        raise Problem(503, "source_unavailable", "Snapshot storage is not configured.")
    async with transaction(ROLE, principal) as session:
        session.add(
            _event(
                principal,
                "dataset",
                dataset_id,
                "audit.requested",
                action="dataset.snapshot_downloaded",
                object_kind="dataset_snapshot",
                object_id=found.id,
                actor_id=principal.user_id,
                client_id=principal.client_id,
                before=None,
                after={"dataset_id": str(dataset_id), "version": version, "sha256": found.sha256},
            )
        )
    return RedirectResponse(link, status_code=302)


# --- dataset contract (DF-09) --------------------------------------------------------------------

CONTRACT_CHANGED = "dataset.contract_changed"
_GX_NAMES = {e.name: kind for kind, e in quality_vocab.EXPECTATIONS.items()}


def _contract_tag(version: int) -> str:
    """The contract's own ETag: its version, so the kernel's If-Match check (kernel/etag.py) and
    the handler's lock compare the same thing."""
    return f'W/"contract-{version}"'


def _seen_version(if_match: str) -> int:
    tag = if_match.strip().removeprefix("W/").strip('"')
    try:
        return int(tag.removeprefix("contract-"))
    except ValueError as exc:
        raise Problem(
            412, "precondition_failed", "If-Match is not a contract version read here."
        ) from exc


def _contract_view(
    dataset_id: uuid.UUID, row: catalog.DatasetContract | None, current_hash: str | None
) -> ContractView:
    if row is None:
        return ContractView(
            dataset_id=dataset_id, status="none", version=0, schema_hash_current=current_hash
        )
    return ContractView.model_validate(
        {
            "dataset_id": dataset_id,
            "status": row.status,
            "version": row.version,
            "schema_hash_expected": row.schema_hash_expected,
            "schema_hash_current": current_hash,
            "freshness_sla": FreshnessSla(**row.freshness_sla) if row.freshness_sla else None,
            "required_rules": [ContractRule(**r) for r in row.required_rules or []],
            "broken_reason": row.broken_reason,
            "updated_at": row.updated_at,
            "updated_by": row.updated_by,
        }
    )


async def _schema_hash_now(session: AsyncSession, dataset_id: uuid.UUID) -> str | None:
    dv = catalog.DatasetVersion
    found: str | None = await session.scalar(
        select(dv.schema_hash)
        .where(dv.dataset_id == dataset_id, dv.valid_to.is_(None))
        .order_by(dv.version.desc())
        .limit(1)
    )
    return found


def _checked_rules(rules: list[ContractRule]) -> list[dict[str, Any]]:
    """Each required rule names a GX expectation the platform checks, on the columns it needs."""
    out = []
    for index, rule in enumerate(rules):
        kind = _GX_NAMES.get(rule.expectation)
        where = f"required_rules[{index}]"
        if kind is None:
            raise Problem(
                422,
                "validation_failed",
                f"{rule.expectation} is not an expectation the platform checks.",
                errors=[FieldError(field=where, code="invalid", message="unknown expectation")],
            )
        if kind == "compound_unique" and len(rule.columns or []) < 2:
            raise Problem(
                422,
                "validation_failed",
                "A compound key names two columns or more.",
                errors=[FieldError(field=where, code="invalid", message="columns")],
            )
        if quality_vocab.EXPECTATIONS[kind].column and not rule.column:
            raise Problem(
                422,
                "validation_failed",
                f"{rule.expectation} names a column.",
                errors=[FieldError(field=where, code="invalid", message="column")],
            )
        out.append(rule.model_dump(exclude_none=True))
    return out


@router.get(
    "/v1/datasets/{id}/contract",
    response_model=ContractView,
    **endpoint("GET", "/v1/datasets/{id}/contract", operation_id="getDatasetContract"),
)
async def get_dataset_contract(
    dataset_id: PathId, response: Response, principal: CurrentPrincipal, space: CurrentDataSpace
) -> ContractView:
    """DF-09: the dataset's contract, `none` before one is written; its ETag is its version."""
    async with transaction(ROLE, principal) as session:
        await _dataset(session, principal, space, dataset_id)
        row = await session.scalar(
            select(catalog.DatasetContract).where(catalog.DatasetContract.dataset_id == dataset_id)
        )
        view = _contract_view(dataset_id, row, await _schema_hash_now(session, dataset_id))
    response.headers["ETag"] = _contract_tag(view.version)
    return view


@router.put(
    "/v1/datasets/{id}/contract",
    response_model=ContractView,
    **endpoint("PUT", "/v1/datasets/{id}/contract", operation_id="putDatasetContract"),
)
async def put_dataset_contract(
    dataset_id: PathId,
    body: ContractPut,
    response: Response,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    if_match: Annotated[str | None, Header()] = None,
) -> ContractView:
    """DF-09: write the contract, holding If-Match to the version read (428 without it, 412 when
    it moved: OCC, invariant 10). Activating it is a second person's: whoever wrote what is being
    activated does not, unless the organisation allows self-approval (recorded)."""
    if not if_match:
        raise Problem(
            428,
            "precondition_required",
            "Send If-Match with the contract version you read (GET …/contract).",
        )
    seen = _seen_version(if_match)
    rules = _checked_rules(body.required_rules)
    sla = body.freshness_sla.model_dump() if body.freshness_sla else {}
    expected = (body.schema_hash_expected or "").strip() or None
    now = _now()
    async with transaction(ROLE, principal) as session:
        await _dataset(session, principal, space, dataset_id)
        row = await session.scalar(
            select(catalog.DatasetContract)
            .where(catalog.DatasetContract.dataset_id == dataset_id)
            .with_for_update()
        )
        if (row.version if row else 0) != seen:
            raise Problem(
                412, "precondition_failed", "The contract changed since you read it; read it again."
            )
        changed = row is None or (
            row.schema_hash_expected != expected
            or (row.freshness_sla or {}) != sla
            or list(row.required_rules or []) != rules
        )
        self_approved = False
        if body.status == "active":
            # The maker of what goes active: the caller when they change it now, else its writer.
            maker = principal.user_id if changed or row is None else row.updated_by
            self_approved = two_people(
                principal,
                maker,
                message="Whoever wrote a contract does not activate it; another steward does.",
                code="policy_denied",
            )
        if row is None:
            row = catalog.DatasetContract(
                id=uuid7(),
                org_id=principal.org_id,
                dataset_id=dataset_id,
                version=1,
                created_by=principal.user_id,
            )
            session.add(row)
        else:
            row.version += 1
        if changed:
            row.schema_hash_expected = expected
            row.freshness_sla = sla
            row.required_rules = rules
            row.updated_by = principal.user_id
        row.status = body.status
        row.broken_reason = None
        row.updated_at = now
        session.add(
            _event(
                principal,
                "dataset",
                dataset_id,
                CONTRACT_CHANGED,
                dataset_id=dataset_id,
                data_space_id=space,
                version=row.version,
                status=row.status,
                changed=changed,
            )
        )
        await session.flush()
        view = _contract_view(dataset_id, row, await _schema_hash_now(session, dataset_id))
    if self_approved:
        await record_self_approval(
            principal, step="dataset_contract.activate", object_kind="dataset", object_id=dataset_id
        )
    response.headers["ETag"] = _contract_tag(view.version)
    return view


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
        # Four eyes: whoever last edited the consolidation spec does not certify its output.
        self_approved = two_people(
            principal,
            version.created_by,
            message="The creator of a dataset version cannot certify it (BR-156).",
            code="policy_denied",
        )
        if dataset.origin != "consolidated":
            raise Problem(
                422,
                "business_rule_violated",
                "Only versions of a consolidated dataset are certified (BR-156).",
            )
        if version.certified_at is not None:
            raise Problem(409, "invalid_state", "The version is already certified.")
        certified = await _record(session, principal, version, "certify", note=note)
        if self_approved:
            await record_self_approval(
                principal,
                step="dataset_version.certify",
                object_kind="dataset_version",
                object_id=version.id,
            )
        return certified


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
