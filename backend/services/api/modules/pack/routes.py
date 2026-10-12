"""A2 group Gói ngành (P09): import, authoring, install, mapping, overrides, upgrades.

Packs and their versions are organisation-wide (A1.2); an install, its mappings, overrides and
upgrades belong to the Data Space named by X-Data-Space. Work that runs long or belongs to another
block is requested through pack.outbox in the same transaction (D1 rule 8): sample-data checks,
signing and export of a released version, mapping suggestions, calibration and upgrade test runs
(workflows pack_calibrate, pack_upgrade), the activation proposal (inbox) and the metric_version
of an approved override (context).
"""

from __future__ import annotations

import datetime
import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Path, Query, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import catalog
from libs.dataagent_db.models import pack as pk
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
from services.api.modules.pack import rules
from services.api.modules.pack.schemas import (
    Accepted,
    ApplyRequest,
    CalibrateRequest,
    CanonicalAttributeView,
    CanonicalEntityView,
    DecisionsPut,
    EntityMappingView,
    IndustryPackView,
    MappingsPut,
    MetricOverrideCreate,
    MetricOverrideView,
    PackCheckRunView,
    PackInstallCreate,
    PackInstallView,
    PackMetricView,
    PackSkillView,
    PackUpgradeView,
    PackVersionCreate,
    PackVersionDetail,
    PackVersionView,
    RollbackRequest,
    UpgradeRequest,
    ValidationRuleView,
)
from services.api.modules.period import closing_periods

router = APIRouter()
ROLE = "svc_pack"

PathId = Annotated[uuid.UUID, Path(alias="id")]
POLL_AFTER_MS = 2_000
MAX_PACK_BYTES = 200 * 1024 * 1024  # A1.7: 413 above 200 MB

# Versions a Data Space can install: imported with a verified signature (UC-09-01 step 5).
INSTALLABLE = ("available",)
# Versions whose metric codes are taken for good (BR-131).
PUBLISHED = ("released", "available")
# Install states in which mappings are (re)declared; a change after calibration means calibrating again.
MAPPABLE = ("mapping", "pending_activation", "active")
# Upgrades still in progress for an install: at most one at a time.
UPGRADE_OPEN = ("available", "merging", "needs_decision", "testing", "pending_approval")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _emit(
    session: AsyncSession,
    principal: Principal,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    event_type: str,
    **payload: Any,
) -> None:
    session.add(
        pk.Outbox(
            id=uuid7(),
            org_id=principal.org_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=jsonable_encoder(payload),
        )
    )


def _accepted(response: Response, location: str, accepted: Accepted) -> Accepted:
    response.headers["Location"] = location
    return accepted


# --- loading ------------------------------------------------------------------------------------


async def _version(
    session: AsyncSession, principal: Principal, version_id: uuid.UUID, *, lock: bool = False
) -> pk.PackVersion:
    query = select(pk.PackVersion).where(
        pk.PackVersion.id == version_id, pk.PackVersion.org_id == principal.org_id
    )
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise Problem(404, "not_found", "Pack version not found.")
    return row


async def _install(
    session: AsyncSession,
    principal: Principal,
    space: uuid.UUID,
    install_id: uuid.UUID,
    *,
    lock: bool = False,
) -> pk.PackInstall:
    query = select(pk.PackInstall).where(
        pk.PackInstall.id == install_id,
        pk.PackInstall.org_id == principal.org_id,
        pk.PackInstall.data_space_id == space,
    )
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise Problem(404, "not_found", "Pack install not found.")
    return row


async def _upgrade(
    session: AsyncSession, principal: Principal, space: uuid.UUID, upgrade_id: uuid.UUID
) -> tuple[pk.PackUpgrade, pk.PackInstall]:
    found = (
        await session.execute(
            select(pk.PackUpgrade, pk.PackInstall)
            .join(pk.PackInstall, pk.PackInstall.id == pk.PackUpgrade.pack_install_id)
            .where(
                pk.PackUpgrade.id == upgrade_id,
                pk.PackUpgrade.org_id == principal.org_id,
                pk.PackInstall.data_space_id == space,
            )
            # The install changes with the upgrade (apply, rollback): lock both.
            .with_for_update()
        )
    ).first()
    if found is None:
        raise Problem(404, "not_found", "Pack upgrade not found.")
    return found[0], found[1]


async def _metrics(session: AsyncSession, version_id: uuid.UUID) -> list[pk.PackMetric]:
    return list(
        (
            await session.scalars(
                select(pk.PackMetric)
                .where(pk.PackMetric.pack_version_id == version_id)
                .order_by(pk.PackMetric.code)
            )
        ).all()
    )


async def _attributes(
    session: AsyncSession, version_id: uuid.UUID
) -> list[tuple[pk.CanonicalAttribute, str]]:
    """The version's attributes with their entity's code ("entity.attribute" names them)."""
    rows = await session.execute(
        select(pk.CanonicalAttribute, pk.CanonicalEntity.code)
        .join(pk.CanonicalEntity, pk.CanonicalEntity.id == pk.CanonicalAttribute.entity_id)
        .where(pk.CanonicalEntity.pack_version_id == version_id)
    )
    return list(rows.tuples().all())


async def _load_content(
    session: AsyncSession,
    principal: Principal,
    version_id: uuid.UUID,
    content: rules.ContentSpec,
) -> None:
    """Writes a version's canonical model, metrics, rules and skills (UC-09-01 step 4).

    The DB keeps the rules of the content: one code per version, the data types, and a metric
    without legal basis must be internal (pack_metric CHECK, AC-09-05-05)."""
    org = principal.org_id
    entity_ids = [uuid7() for _ in content.entities]
    for entity, entity_id in zip(content.entities, entity_ids, strict=True):
        session.add(
            pk.CanonicalEntity(
                id=entity_id,
                org_id=org,
                pack_version_id=version_id,
                code=entity.code,
                name=entity.name,
                description=entity.description,
                key_attributes=entity.key_attributes,
            )
        )
    # The models carry no relationship, so the unit of work does not order an attribute after
    # its entity: the entities are written first.
    await session.flush()
    for entity, entity_id in zip(content.entities, entity_ids, strict=True):
        for attr in entity.attributes:
            session.add(
                pk.CanonicalAttribute(
                    id=uuid7(),
                    org_id=org,
                    entity_id=entity_id,
                    code=attr.code,
                    name=attr.name,
                    data_type=attr.data_type,
                    required=attr.required,
                    code_list=attr.code_list,
                    pii=attr.pii,
                )
            )
    for metric in content.metrics:
        session.add(
            pk.PackMetric(
                id=uuid7(),
                org_id=org,
                pack_version_id=version_id,
                **metric.model_dump(),
            )
        )
    for rule in content.rules:
        session.add(
            pk.ValidationRule(
                id=uuid7(),
                org_id=org,
                pack_version_id=version_id,
                code=rule.code,
                description=rule.description,
                expression=rule.expression,
                tolerance=rule.tolerance,
                severity=rule.severity,
            )
        )
    for skill in content.skills:
        session.add(
            pk.PackSkill(
                id=uuid7(),
                org_id=org,
                pack_version_id=version_id,
                kind=skill.kind,
                code=skill.code,
                name=skill.name,
                definition=skill.definition,
            )
        )


# --- packs and versions (UC-09-01, UC-09-05) ----------------------------------------------------


@router.get(
    "/v1/packs",
    response_model=Page[IndustryPackView],
    **endpoint("GET", "/v1/packs", operation_id="listPacks"),
)
async def list_packs(
    principal: CurrentPrincipal,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(
            order_by(
                "code", "name", "industry_code", "created_at", "updated_at", default="code asc"
            )
        ),
    ],
    industry_code: str | None = None,
    code: str | None = None,
) -> Page[IndustryPackView]:
    p = pk.IndustryPack
    query = select(p).where(p.org_id == principal.org_id)
    if industry_code:
        query = query.where(p.industry_code == industry_code)
    if code:
        query = query.where(p.code == code)
    query = keyset(
        query,
        columns={
            "code": p.code,
            "name": p.name,
            "industry_code": p.industry_code,
            "created_at": p.created_at,
            "updated_at": p.updated_at,
        },
        tiebreak=p.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    return Page(items=[IndustryPackView.model_validate(r) for r in rows], next_page_token=token)


async def _reject_import(principal: Principal, pack_code: str | None, reason: str) -> None:
    """E1 of UC-09-01: the refusal is a security warning the audit consumer records
    (AC-09-01-03). Its own transaction: the import's rolls back."""
    async with transaction(ROLE, principal) as session:
        existing = None
        if pack_code:
            existing = await session.scalar(
                select(pk.IndustryPack.id).where(
                    pk.IndustryPack.org_id == principal.org_id, pk.IndustryPack.code == pack_code
                )
            )
        _emit(
            session,
            principal,
            "industry_pack",
            existing or uuid7(),
            "pack.import_rejected",
            pack_code=pack_code,
            reason=reason,
            severity="security",
            imported_by=principal.user_id,
        )


@router.post(
    "/v1/packs:import",
    response_model=PackVersionView,
    status_code=201,
    **endpoint("POST", "/v1/packs:import", operation_id="importPack"),
)
async def import_pack(
    principal: CurrentPrincipal,
    file: Annotated[UploadFile, File(description="The signed pack file (UC-09-01)")],
) -> PackVersionView:
    if file.size is not None and file.size > MAX_PACK_BYTES:
        raise Problem(413, "payload_too_large", "A pack file is at most 200 MB.")
    data = await file.read(MAX_PACK_BYTES + 1)
    if len(data) > MAX_PACK_BYTES:
        raise Problem(413, "payload_too_large", "A pack file is at most 200 MB.")
    try:
        parsed = rules.read_pack_file(data, rules.trusted_keys(), rules.core_version())
    except rules.PackFileError as exc:
        if exc.code == "business_rule_violated":
            await _reject_import(principal, _pack_code(data), exc.code)
        raise Problem(422, exc.code, str(exc)) from exc
    manifest = parsed.manifest
    now = _now()
    async with transaction(ROLE, principal) as session:
        pack = await session.scalar(
            select(pk.IndustryPack)
            .where(
                pk.IndustryPack.org_id == principal.org_id,
                pk.IndustryPack.code == manifest.pack.code,
            )
            .with_for_update()
        )
        if pack is None:
            pack = pk.IndustryPack(
                id=uuid7(),
                org_id=principal.org_id,
                created_by=principal.user_id,
                updated_by=principal.user_id,
                **manifest.pack.model_dump(),
            )
            session.add(pack)
            await session.flush()
        version = pk.PackVersion(
            id=uuid7(),
            org_id=principal.org_id,
            pack_id=pack.id,
            version=manifest.version,
            manifest=parsed.raw_manifest,
            signature=parsed.signature,
            signature_verified_at=now,
            min_core_version=manifest.min_core_version,
            release_notes=manifest.release_notes,
            breaking_changes=manifest.breaking_changes,
            # The file itself goes to da-packs with the object-store client (not in the api yet).
            object_key="",
            status="available",
            released_at=manifest.released_at or now,
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(version)
        await session.flush()
        await _load_content(session, principal, version.id, manifest)
        released_at = version.released_at or now
        _emit(
            session,
            principal,
            "pack_version",
            version.id,
            "pack.version_published",
            pack_id=pack.id,
            pack_version_id=version.id,
            version=version.version,
            status=version.status,
            breaking_changes=len(manifest.breaking_changes),
            # BR-131, AC-09-01-07: a breaking change applies 30 days after its release at the earliest.
            applicable_from=(released_at + rules.BREAKING_NOTICE)
            if manifest.breaking_changes
            else released_at,
        )
        # 5a, AC-09-01-02: Data Spaces on an older version of this pack hear of the upgrade.
        installs = (
            await session.execute(
                select(pk.PackInstall.id, pk.PackInstall.data_space_id, pk.PackVersion.version)
                .join(pk.PackVersion, pk.PackVersion.id == pk.PackInstall.pack_version_id)
                .where(
                    pk.PackVersion.pack_id == pack.id,
                    pk.PackInstall.status.in_(("active", "pending_activation", "mapping")),
                )
            )
        ).all()
        for install_id, space_id, current in installs:
            if rules.version_key(current) < rules.version_key(version.version):
                _emit(
                    session,
                    principal,
                    "pack_install",
                    install_id,
                    "pack.upgrade_ready",
                    pack_install_id=install_id,
                    data_space_id=space_id,
                    pack_version_id=version.id,
                    version=version.version,
                )
        await session.flush()
        return PackVersionView.model_validate(version)


def _pack_code(data: bytes) -> str | None:
    """The pack code a refused file claims, for the security warning; None when unreadable."""
    try:
        code = json.loads(data)["manifest"]["pack"]["code"]
    except (ValueError, KeyError, TypeError):
        return None
    return str(code)[:200] if code else None


@router.get(
    "/v1/pack-versions/{id}",
    response_model=PackVersionDetail,
    **endpoint("GET", "/v1/pack-versions/{id}", operation_id="getPackVersion"),
)
async def get_pack_version(version_id: PathId, principal: CurrentPrincipal) -> PackVersionDetail:
    async with transaction(ROLE, principal) as session:
        version = await _version(session, principal, version_id)
        entities = (
            await session.scalars(
                select(pk.CanonicalEntity)
                .where(pk.CanonicalEntity.pack_version_id == version.id)
                .order_by(pk.CanonicalEntity.code)
            )
        ).all()
        attributes: dict[uuid.UUID, list[CanonicalAttributeView]] = {}
        for attr, _ in sorted(
            await _attributes(session, version.id), key=lambda pair: pair[0].code
        ):
            attributes.setdefault(attr.entity_id, []).append(
                CanonicalAttributeView.model_validate(attr)
            )
        metrics = await _metrics(session, version.id)
        checks = (
            await session.scalars(
                select(pk.ValidationRule)
                .where(pk.ValidationRule.pack_version_id == version.id)
                .order_by(pk.ValidationRule.code)
            )
        ).all()
        skills = (
            await session.scalars(
                select(pk.PackSkill)
                .where(pk.PackSkill.pack_version_id == version.id)
                .order_by(pk.PackSkill.kind, pk.PackSkill.code)
            )
        ).all()
        latest = await _latest_check(session, version.id)
        return PackVersionDetail(
            **PackVersionView.model_validate(version).model_dump(),
            entities=[
                CanonicalEntityView(
                    **CanonicalEntityView.model_validate(e).model_dump(exclude={"attributes"}),
                    attributes=attributes.get(e.id, []),
                )
                for e in entities
            ],
            metrics=[PackMetricView.model_validate(m) for m in metrics],
            rules=[ValidationRuleView.model_validate(r) for r in checks],
            skills=[PackSkillView.model_validate(s) for s in skills],
            latest_check_run=PackCheckRunView.model_validate(latest) if latest else None,
        )


async def _latest_check(session: AsyncSession, version_id: uuid.UUID) -> pk.PackCheckRun | None:
    row: pk.PackCheckRun | None = await session.scalar(
        select(pk.PackCheckRun)
        .where(pk.PackCheckRun.pack_version_id == version_id)
        .order_by(pk.PackCheckRun.created_at.desc(), pk.PackCheckRun.id.desc())
        .limit(1)
    )
    return row


@router.post(
    "/v1/packs/{id}/versions",
    response_model=PackVersionView,
    status_code=201,
    **endpoint("POST", "/v1/packs/{id}/versions", operation_id="createPackVersion"),
)
async def create_pack_version(
    body: PackVersionCreate, pack_id: PathId, principal: CurrentPrincipal
) -> PackVersionView:
    try:
        content = rules.ContentSpec.model_validate(body.manifest)
    except ValidationError as exc:
        raise Problem(
            422,
            "validation_failed",
            "The manifest content is invalid.",
            errors=[
                FieldError(
                    field="manifest." + ".".join(str(p) for p in e.get("loc", ())),
                    code=str(e.get("type", "invalid")),
                    message=str(e.get("msg", "")),
                )
                for e in exc.errors()
            ],
        ) from exc
    async with transaction(ROLE, principal) as session:
        pack = await session.scalar(
            select(pk.IndustryPack).where(
                pk.IndustryPack.id == pack_id, pk.IndustryPack.org_id == principal.org_id
            )
        )
        if pack is None:
            raise Problem(404, "not_found", "Pack not found.")
        version = pk.PackVersion(
            id=uuid7(),
            org_id=principal.org_id,
            pack_id=pack.id,
            version=body.version,
            manifest=body.manifest,
            # A draft is unsigned; :publish has it signed with the author's key (UC-09-05 step 4).
            signature="",
            min_core_version=body.min_core_version,
            release_notes=body.release_notes,
            breaking_changes=body.breaking_changes,
            object_key="",
            status="draft",
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(version)
        await session.flush()
        await _load_content(session, principal, version.id, content)
        _emit(
            session,
            principal,
            "pack_version",
            version.id,
            "pack_version.created",
            pack_id=pack.id,
            pack_version_id=version.id,
            version=version.version,
            status=version.status,
        )
        await session.flush()
        return PackVersionView.model_validate(version)


async def _static_checks(session: AsyncSession, version: pk.PackVersion) -> dict[str, Any]:
    """The checks that need no sample data (UC-09-05 step 3): legal basis, BR-131 code reuse,
    migration guides of breaking changes. Each entry lists what failed; empty means passed."""
    metrics = await _metrics(session, version.id)
    released = (
        await session.execute(
            select(pk.PackMetric.code, pk.PackMetric.expression, pk.PackMetric.unit)
            .join(pk.PackVersion, pk.PackVersion.id == pk.PackMetric.pack_version_id)
            .where(
                pk.PackVersion.pack_id == version.pack_id,
                pk.PackVersion.id != version.id,
                pk.PackVersion.status.in_(PUBLISHED),
            )
            .order_by(pk.PackVersion.created_at)
        )
    ).all()
    return {
        "legal_basis": rules.missing_legal_basis(
            [(m.code, m.legal_basis, m.is_internal) for m in metrics]
        ),
        "code_reuse": rules.reused_codes(
            {m.code: (m.expression, m.unit) for m in metrics},
            {code: (expression, unit) for code, expression, unit in released},
        ),
        "breaking_changes_without_guide": rules.breaking_without_guide(
            list(version.breaking_changes or [])
        ),
    }


def _static_failed(checks: dict[str, Any]) -> bool:
    return bool(
        checks["legal_basis"] or checks["code_reuse"] or checks["breaking_changes_without_guide"]
    )


@router.post(
    "/v1/pack-versions/{id}:validate",
    status_code=202,
    response_model=Accepted,
    **endpoint("POST", "/v1/pack-versions/{id}:validate", operation_id="validatePackVersion"),
)
async def validate_pack_version(
    version_id: PathId, principal: CurrentPrincipal, response: Response
) -> Accepted:
    run_id = uuid7()
    async with transaction(ROLE, principal) as session:
        version = await _version(session, principal, version_id, lock=True)
        if version.status != "draft":
            raise Problem(409, "invalid_state", f"The version is {version.status}, not a draft.")
        checks = await _static_checks(session, version)
        if _static_failed(checks):
            # E1, AC-09-05-06: the result is known now; no sample run is needed to refuse.
            session.add(
                pk.PackCheckRun(
                    id=run_id,
                    org_id=principal.org_id,
                    pack_version_id=version.id,
                    checks={**checks, "sample_datasets": "skipped", "sample_questions": "skipped"},
                    passed=False,
                    ran_by=principal.user_id,
                )
            )
            status = "failed"
        else:
            status = "queued"
        # The sample-data runs (3 datasets ≤ 0.01 %, sample questions not worse) are the
        # worker's; it writes pack_check_run with this id when they finish.
        _emit(
            session,
            principal,
            "pack_version",
            version.id,
            "pack_version.validation_requested",
            pack_version_id=version.id,
            pack_check_run_id=run_id,
            status=status,
            requested_by=principal.user_id,
        )
    return _accepted(
        response,
        f"/v1/pack-versions/{version_id}",
        Accepted(id=run_id, status=status, poll_after_ms=POLL_AFTER_MS),
    )


@router.post(
    "/v1/pack-versions/{id}:publish",
    response_model=PackVersionView,
    **endpoint("POST", "/v1/pack-versions/{id}:publish", operation_id="publishPackVersion"),
)
async def publish_pack_version(version_id: PathId, principal: CurrentPrincipal) -> PackVersionView:
    async with transaction(ROLE, principal) as session:
        version = await _version(session, principal, version_id, lock=True)
        if version.status != "draft":
            raise Problem(409, "invalid_state", f"The version is already {version.status}.")
        latest = await _latest_check(session, version.id)
        if latest is None or not latest.passed:
            raise Problem(
                409, "invalid_state", "The version has not passed its pre-release checks."
            )
        checks = await _static_checks(session, version)
        if checks["code_reuse"]:
            raise Problem(
                422,
                "business_rule_violated",
                "Released metric codes are given another definition: "
                + ", ".join(checks["code_reuse"])
                + " (BR-131).",
            )
        if checks["legal_basis"]:
            raise Problem(
                422,
                "business_rule_violated",
                "Metrics without legal basis or internal label: "
                + ", ".join(checks["legal_basis"])
                + " (BR-131).",
            )
        if checks["breaking_changes_without_guide"]:
            raise Problem(
                422,
                "business_rule_violated",
                "Every breaking change needs a migration guide (BR-131).",
            )
        now = _now()
        version.status = "released"
        version.released_at = now
        version.updated_by, version.updated_at = principal.user_id, now
        # Signing with the author's key (OpenBao Transit) and exporting the file to da-packs are
        # the worker's; it fills signature and object_key.
        _emit(
            session,
            principal,
            "pack_version",
            version.id,
            "pack_version.released",
            pack_id=version.pack_id,
            pack_version_id=version.id,
            version=version.version,
            status=version.status,
            pack_check_run_id=latest.id,
            released_by=principal.user_id,
        )
        await session.flush()
        return PackVersionView.model_validate(version)


# --- install and onboarding (UC-09-02) ----------------------------------------------------------


@router.post(
    "/v1/pack-installs",
    response_model=PackInstallView,
    status_code=201,
    **endpoint("POST", "/v1/pack-installs", operation_id="createPackInstall"),
)
async def create_pack_install(
    body: PackInstallCreate, principal: CurrentPrincipal, space: CurrentDataSpace
) -> PackInstallView:
    async with transaction(ROLE, principal) as session:
        version = await _version(session, principal, body.pack_version_id)
        if version.status not in INSTALLABLE:
            raise Problem(
                409, "invalid_state", f"The version is {version.status}; import it first."
            )
        install = pk.PackInstall(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=space,
            pack_version_id=version.id,
            status="mapping",
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(install)
        # A second live install in the Data Space hits the partial unique index: 409 conflict.
        await session.flush()
        _emit(
            session,
            principal,
            "pack_install",
            install.id,
            "pack_install.created",
            pack_install_id=install.id,
            data_space_id=space,
            pack_version_id=version.id,
            status=install.status,
        )
        await session.flush()
        return PackInstallView.model_validate(install)


@router.post(
    "/v1/pack-installs/{id}:suggest-mappings",
    status_code=202,
    response_model=Accepted,
    **endpoint(
        "POST", "/v1/pack-installs/{id}:suggest-mappings", operation_id="suggestPackMappings"
    ),
)
async def suggest_mappings(
    install_id: PathId, principal: CurrentPrincipal, space: CurrentDataSpace, response: Response
) -> Accepted:
    async with transaction(ROLE, principal) as session:
        install = await _install(session, principal, space, install_id)
        if install.status not in MAPPABLE:
            raise Problem(409, "invalid_state", f"The install is {install.status}.")
        # Step 2: the mapping assistant writes entity_mapping rows with origin suggested.
        _emit(
            session,
            principal,
            "pack_install",
            install.id,
            "pack_install.mapping_suggestion_requested",
            pack_install_id=install.id,
            data_space_id=space,
            pack_version_id=install.pack_version_id,
            requested_by=principal.user_id,
        )
    return _accepted(
        response,
        f"/v1/pack-installs/{install_id}/mappings",
        Accepted(id=install_id, status="queued", poll_after_ms=POLL_AFTER_MS),
    )


@router.put(
    "/v1/pack-installs/{id}/mappings",
    response_model=list[EntityMappingView],
    **endpoint("PUT", "/v1/pack-installs/{id}/mappings", operation_id="putPackMappings"),
)
async def put_mappings(
    body: MappingsPut, install_id: PathId, principal: CurrentPrincipal, space: CurrentDataSpace
) -> list[EntityMappingView]:
    async with transaction(ROLE, principal) as session:
        install = await _install(session, principal, space, install_id, lock=True)
        if install.status not in MAPPABLE:
            raise Problem(409, "invalid_state", f"The install is {install.status}.")
        attributes = {a.id: a for a, _ in await _attributes(session, install.pack_version_id)}
        wanted = {m.attribute_id for m in body.entity_mappings}
        if len(wanted) != len(body.entity_mappings):
            raise Problem(422, "validation_failed", "An attribute is mapped twice.")
        codes = body.code_mappings or []
        unknown = (wanted | {c.attribute_id for c in codes}) - set(attributes)
        if unknown:
            raise Problem(
                422, "validation_failed", "An attribute is not in the installed pack version."
            )
        for c in codes:
            attr = attributes[c.attribute_id]
            if attr.data_type != "code":
                raise Problem(
                    422, "validation_failed", f"Attribute {attr.code} does not take a code list."
                )
            allowed = _code_values(attr.code_list)
            if allowed and c.canonical_code not in allowed:
                raise Problem(
                    422,
                    "validation_failed",
                    f"{c.canonical_code} is not a code of attribute {attr.code}.",
                )
        datasets = {m.dataset_id for m in body.entity_mappings}
        if datasets:
            found = set(
                (
                    await session.scalars(
                        select(catalog.Dataset.id).where(
                            catalog.Dataset.id.in_(datasets),
                            catalog.Dataset.org_id == principal.org_id,
                            catalog.Dataset.data_space_id == space,
                            catalog.Dataset.deleted_at.is_(None),
                        )
                    )
                ).all()
            )
            if datasets - found:
                raise Problem(422, "validation_failed", "A dataset is not in this Data Space.")
        now = _now()
        # BR-37: a mapping is never overwritten; the current one closes and a new one opens.
        current = (
            await session.scalars(
                select(pk.EntityMapping).where(
                    pk.EntityMapping.pack_install_id == install.id,
                    pk.EntityMapping.valid_to.is_(None),
                )
            )
        ).all()
        by_attr = {m.attribute_id: m for m in current}
        for m in body.entity_mappings:
            old = by_attr.get(m.attribute_id)
            if old is not None and (old.dataset_id, old.column_expr) == (
                m.dataset_id,
                m.column_expr,
            ):
                # Confirming the same column: the mapping stays, now confirmed by a person.
                old.origin, old.confirmed_by = m.origin, principal.user_id
                old.updated_by, old.updated_at = principal.user_id, now
                continue
            if old is not None:
                old.valid_to, old.updated_by, old.updated_at = now, principal.user_id, now
                await session.flush()
            session.add(
                pk.EntityMapping(
                    id=uuid7(),
                    org_id=principal.org_id,
                    pack_install_id=install.id,
                    attribute_id=m.attribute_id,
                    dataset_id=m.dataset_id,
                    column_expr=m.column_expr,
                    confidence=m.confidence,
                    origin=m.origin,
                    valid_from=now,
                    confirmed_by=principal.user_id,
                    created_by=principal.user_id,
                    updated_by=principal.user_id,
                )
            )
        # PUT replaces the set: mappings not sent are closed.
        for attr_id, old in by_attr.items():
            if attr_id not in wanted:
                old.valid_to, old.updated_by, old.updated_at = now, principal.user_id, now
        if body.code_mappings is not None:
            open_codes = (
                await session.scalars(
                    select(pk.CodeMapping).where(
                        pk.CodeMapping.pack_install_id == install.id,
                        pk.CodeMapping.valid_to.is_(None),
                    )
                )
            ).all()
            keep = {(c.attribute_id, c.source_code, c.canonical_code) for c in codes}
            kept: set[tuple[uuid.UUID, str, str]] = set()
            for row in open_codes:
                key = (row.attribute_id, row.source_code, row.canonical_code)
                if key in keep:
                    kept.add(key)
                else:
                    row.valid_to, row.updated_by, row.updated_at = now, principal.user_id, now
            await session.flush()
            for c in codes:
                if (c.attribute_id, c.source_code, c.canonical_code) in kept:
                    continue
                session.add(
                    pk.CodeMapping(
                        id=uuid7(),
                        org_id=principal.org_id,
                        pack_install_id=install.id,
                        attribute_id=c.attribute_id,
                        source_code=c.source_code,
                        canonical_code=c.canonical_code,
                        valid_from=now,
                        created_by=principal.user_id,
                        updated_by=principal.user_id,
                    )
                )
        if install.status == "pending_activation":
            # The passed calibration was on other mappings: reconcile again before activating.
            install.status = "mapping"
        install.updated_by, install.updated_at = principal.user_id, now
        await session.flush()
        _emit(
            session,
            principal,
            "pack_install",
            install.id,
            "pack_install.mappings_confirmed",
            pack_install_id=install.id,
            data_space_id=space,
            status=install.status,
            entity_mappings=len(body.entity_mappings),
            code_mappings=len(codes),
        )
        rows = (
            await session.scalars(
                select(pk.EntityMapping)
                .where(
                    pk.EntityMapping.pack_install_id == install.id,
                    pk.EntityMapping.valid_to.is_(None),
                )
                .order_by(pk.EntityMapping.attribute_id)
            )
        ).all()
        return [EntityMappingView.model_validate(r) for r in rows]


def _code_values(code_list: Any) -> set[str]:
    """The canonical codes of a code list: ["1", "2"], [{"code": "1"}, …] or {"1": "label"}."""
    if isinstance(code_list, dict):
        return {str(k) for k in code_list}
    if isinstance(code_list, list):
        return {
            str(item.get("code")) if isinstance(item, dict) else str(item)
            for item in code_list
            if not isinstance(item, dict) or "code" in item
        }
    return set()


@router.post(
    "/v1/pack-installs/{id}:calibrate",
    status_code=202,
    response_model=Accepted,
    **endpoint("POST", "/v1/pack-installs/{id}:calibrate", operation_id="calibratePackInstall"),
)
async def calibrate_install(
    install_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    response: Response,
    body: CalibrateRequest = CalibrateRequest(),
) -> Accepted:
    async with transaction(ROLE, principal) as session:
        install = await _install(session, principal, space, install_id, lock=True)
        if install.status not in ("mapping", "pending_activation", "active"):
            raise Problem(409, "invalid_state", f"The install is {install.status}.")
        running = await session.scalar(
            select(pk.CalibrationRun.id).where(
                pk.CalibrationRun.pack_install_id == install.id,
                pk.CalibrationRun.status == "running",
            )
        )
        if running is not None:
            raise Problem(409, "invalid_state", "A calibration of this install is running.")
        mapped = await session.scalar(
            select(pk.EntityMapping.id)
            .where(
                pk.EntityMapping.pack_install_id == install.id,
                pk.EntityMapping.valid_to.is_(None),
            )
            .limit(1)
        )
        if mapped is None:
            raise Problem(409, "invalid_state", "Confirm the mappings before calibrating.")
        run = pk.CalibrationRun(
            id=uuid7(),
            org_id=principal.org_id,
            pack_install_id=install.id,
            trigger="onboarding" if install.status != "active" else "override",
            periods=list(body.periods or []),
            status="running",
        )
        session.add(run)
        if install.status != "active":
            install.status = "calibrating"
        install.updated_by, install.updated_at = principal.user_id, _now()
        await session.flush()
        # Workflow pack_calibrate (step 5): rules of the pack and metrics against filed numbers.
        _emit(
            session,
            principal,
            "calibration_run",
            run.id,
            "calibration_run.requested",
            calibration_run_id=run.id,
            pack_install_id=install.id,
            data_space_id=space,
            trigger=run.trigger,
            periods=run.periods,
            status=run.status,
        )
    return _accepted(
        response,
        f"/v1/pack-installs/{install_id}/mappings",
        Accepted(id=run.id, status="running", poll_after_ms=POLL_AFTER_MS),
    )


@router.post(
    "/v1/pack-installs/{id}:activate",
    response_model=PackInstallView,
    **endpoint("POST", "/v1/pack-installs/{id}:activate", operation_id="activatePackInstall"),
)
async def activate_install(
    install_id: PathId, principal: CurrentPrincipal, space: CurrentDataSpace
) -> PackInstallView:
    async with transaction(ROLE, principal) as session:
        install = await _install(session, principal, space, install_id, lock=True)
        if install.status not in ("mapping", "calibrating"):
            raise Problem(409, "invalid_state", f"The install is {install.status}.")
        run = await session.scalar(
            select(pk.CalibrationRun)
            .where(pk.CalibrationRun.pack_install_id == install.id)
            .order_by(pk.CalibrationRun.created_at.desc(), pk.CalibrationRun.id.desc())
            .limit(1)
        )
        if run is None or run.status == "running":
            raise Problem(
                422, "business_rule_violated", "The numbers are not reconciled yet (BR-36)."
            )
        blocking = await _blocking_differences(session, install, run)
        if blocking:
            raise Problem(
                422,
                "business_rule_violated",
                "Regulatory metrics differ from the filed numbers beyond tolerance without an "
                "approved override: " + ", ".join(blocking) + " (BR-36).",
            )
        install.status = "pending_activation"
        install.updated_by, install.updated_at = principal.user_id, _now()
        # Step 6: inbox opens the pack_activate proposal; its decision activates the install
        # (deferred while a period is closing, E1).
        _emit(
            session,
            principal,
            "pack_install",
            install.id,
            "pack_install.activation_requested",
            pack_install_id=install.id,
            data_space_id=space,
            pack_version_id=install.pack_version_id,
            calibration_run_id=run.id,
            proposal_kind="pack_activate",
            status=install.status,
            requested_by=principal.user_id,
        )
        await session.flush()
        return PackInstallView.model_validate(install)


async def _blocking_differences(
    session: AsyncSession, install: pk.PackInstall, run: pk.CalibrationRun
) -> list[str]:
    """BR-36: failed metric items of the reconciliation that are in a regulatory report and have
    no active override explaining them. Other metrics do not block (4a, AC-09-02-03)."""
    failed = set(
        (
            await session.scalars(
                select(pk.CalibrationItem.item_code).where(
                    pk.CalibrationItem.calibration_run_id == run.id,
                    pk.CalibrationItem.item_kind == "metric",
                    pk.CalibrationItem.passed.is_(False),
                )
            )
        ).all()
    )
    if not failed:
        return []
    regulatory = set(
        (
            await session.scalars(
                select(pk.PackMetric.code).where(
                    pk.PackMetric.pack_version_id == install.pack_version_id,
                    pk.PackMetric.in_regulatory_report.is_(True),
                    pk.PackMetric.code.in_(failed),
                )
            )
        ).all()
    )
    explained = set(
        (
            await session.scalars(
                select(pk.MetricOverride.pack_metric_code).where(
                    pk.MetricOverride.pack_install_id == install.id,
                    pk.MetricOverride.status == "active",
                )
            )
        ).all()
    )
    return sorted(regulatory - explained)


# --- metric overrides (UC-09-03) ----------------------------------------------------------------


@router.post(
    "/v1/metric-overrides",
    response_model=MetricOverrideView,
    status_code=201,
    **endpoint("POST", "/v1/metric-overrides", operation_id="createMetricOverride"),
)
async def create_metric_override(
    body: MetricOverrideCreate, principal: CurrentPrincipal, space: CurrentDataSpace
) -> MetricOverrideView:
    if not body.reason.strip():
        raise Problem(422, "validation_failed", "An override needs its reason (BR-129).")
    async with transaction(ROLE, principal) as session:
        install = await _install(session, principal, space, body.pack_install_id)
        if install.status in ("rolled_back", "uninstalled"):
            raise Problem(409, "invalid_state", f"The install is {install.status}.")
        metric = await session.scalar(
            select(pk.PackMetric.id).where(
                pk.PackMetric.pack_version_id == install.pack_version_id,
                pk.PackMetric.code == body.pack_metric_code,
            )
        )
        if metric is None:
            raise Problem(
                422, "validation_failed", "pack_metric_code is not a metric of the installed pack."
            )
        row = pk.MetricOverride(
            id=uuid7(),
            org_id=principal.org_id,
            pack_install_id=install.id,
            pack_metric_code=body.pack_metric_code,
            expression=body.expression,
            scope_condition=body.scope_condition,
            reason=body.reason.strip(),
            # Step 4: created to be approved; the pack is never edited (UC-09-03).
            status="pending_review",
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(row)
        await session.flush()
        _emit(
            session,
            principal,
            "metric_override",
            row.id,
            "metric_override.submitted",
            metric_override_id=row.id,
            pack_install_id=install.id,
            data_space_id=space,
            pack_metric_code=row.pack_metric_code,
            status=row.status,
        )
        await session.flush()
        return MetricOverrideView.model_validate(row)


@router.post(
    "/v1/metric-overrides/{id}:approve",
    response_model=MetricOverrideView,
    **endpoint("POST", "/v1/metric-overrides/{id}:approve", operation_id="approveMetricOverride"),
)
async def approve_metric_override(
    override_id: PathId, principal: CurrentPrincipal, space: CurrentDataSpace
) -> MetricOverrideView:
    async with transaction(ROLE, principal) as session:
        found = (
            await session.execute(
                select(pk.MetricOverride, pk.PackInstall)
                .join(pk.PackInstall, pk.PackInstall.id == pk.MetricOverride.pack_install_id)
                .where(
                    pk.MetricOverride.id == override_id,
                    pk.MetricOverride.org_id == principal.org_id,
                    pk.PackInstall.data_space_id == space,
                )
                .with_for_update(of=pk.MetricOverride)
            )
        ).first()
        if found is None:
            raise Problem(404, "not_found", "Metric override not found.")
        row, install = found[0], found[1]
        if row.status != "pending_review":
            raise Problem(409, "invalid_state", f"The override is {row.status}.")
        if row.created_by == principal.user_id:
            # BR-62: whoever wrote the override does not approve it.
            raise Problem(403, "forbidden", "The author cannot approve their own override.")
        regulatory = bool(
            await session.scalar(
                select(pk.PackMetric.in_regulatory_report).where(
                    pk.PackMetric.pack_version_id == install.pack_version_id,
                    pk.PackMetric.code == row.pack_metric_code,
                )
            )
        )
        now = _now()
        if row.approved_by is None:
            row.approved_by = principal.user_id
        elif regulatory:
            # Step 4, AC-09-03-07: a regulatory metric also needs the controller (AC06).
            if "compliance" not in principal.capabilities:
                raise Problem(
                    403,
                    "forbidden",
                    "A metric in a regulatory report also needs approval by compliance (AC06).",
                )
            if row.approved_by == principal.user_id:
                raise Problem(
                    422,
                    "business_rule_violated",
                    "The compliance approval must come from another person (BR-62).",
                )
            row.compliance_approved_by = principal.user_id
        else:
            raise Problem(409, "invalid_state", "The override is already approved.")
        row.updated_by, row.updated_at = principal.user_id, now
        if regulatory and row.compliance_approved_by is None:
            _emit(
                session,
                principal,
                "metric_override",
                row.id,
                "metric_override.step_approved",
                metric_override_id=row.id,
                pack_install_id=install.id,
                data_space_id=space,
                status=row.status,
                awaiting="compliance",
            )
            await session.flush()
            return MetricOverrideView.model_validate(row)
        # One active override per metric of an install: the previous one is superseded.
        previous = (
            await session.scalars(
                select(pk.MetricOverride).where(
                    pk.MetricOverride.pack_install_id == install.id,
                    pk.MetricOverride.pack_metric_code == row.pack_metric_code,
                    pk.MetricOverride.status == "active",
                    pk.MetricOverride.id != row.id,
                )
            )
        ).all()
        for old in previous:
            old.status, old.updated_by, old.updated_at = "superseded", principal.user_id, now
        await session.flush()
        row.status = "active"
        # Step 5: context publishes the metric_version of the override; its metric.published
        # makes the pack worker reconcile again (calibration_run trigger override).
        _emit(
            session,
            principal,
            "metric_override",
            row.id,
            "metric_override.approved",
            metric_override_id=row.id,
            pack_install_id=install.id,
            data_space_id=space,
            pack_metric_code=row.pack_metric_code,
            status=row.status,
            superseded=[o.id for o in previous],
        )
        await session.flush()
        return MetricOverrideView.model_validate(row)


# --- upgrades (UC-09-04) ------------------------------------------------------------------------


async def _no_closing_period(principal: Principal, space: uuid.UUID) -> None:
    # svc_pack reads no period table; the period module answers on its own connection.
    closing = await closing_periods(principal, space)
    if closing:
        raise Problem(
            423,
            "period_locked",
            "A period is closing: " + ", ".join(closing) + "; upgrade after it closes (BR-38).",
        )


def _upgrade_view(row: pk.PackUpgrade, *, dry_run: bool = False) -> PackUpgradeView:
    return PackUpgradeView(
        id=row.id,
        pack_install_id=row.pack_install_id,
        from_version_id=row.from_version_id,
        to_version_id=row.to_version_id,
        merge_result=row.merge_result,
        conflicts_open=row.conflicts_open,
        calibration_run_id=row.calibration_run_id,
        status=row.status,
        apply_from_period=row.apply_from_period,
        rollback_until=row.rollback_until,
        approved_by=row.approved_by,
        dry_run=dry_run,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post(
    "/v1/pack-installs/{id}:upgrade",
    response_model=PackUpgradeView,
    **endpoint("POST", "/v1/pack-installs/{id}:upgrade", operation_id="upgradePackInstall"),
)
async def upgrade_install(
    body: UpgradeRequest,
    install_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    response: Response,
    dry_run: Annotated[
        bool, Query(description="Compute the three-way merge without recording it")
    ] = False,
) -> PackUpgradeView:
    await _no_closing_period(principal, space)
    async with transaction(ROLE, principal) as session:
        install = await _install(session, principal, space, install_id, lock=True)
        if install.status != "active":
            raise Problem(409, "invalid_state", f"The install is {install.status}, not active.")
        current = await _version(session, principal, install.pack_version_id)
        target = await _version(session, principal, body.to_version_id)
        if target.pack_id != current.pack_id or target.status not in INSTALLABLE:
            raise Problem(
                422, "validation_failed", "to_version_id is not an imported version of this pack."
            )
        if rules.version_key(target.version) <= rules.version_key(current.version):
            raise Problem(
                422, "validation_failed", "to_version_id is not newer than the installed version."
            )
        if not dry_run:
            pending = await session.scalar(
                select(pk.PackUpgrade.id).where(
                    pk.PackUpgrade.pack_install_id == install.id,
                    pk.PackUpgrade.status.in_(UPGRADE_OPEN),
                )
            )
            if pending is not None:
                raise Problem(409, "invalid_state", "An upgrade of this install is in progress.")
        base = {m.code: m.expression for m in await _metrics(session, current.id)}
        new = {m.code: m.expression for m in await _metrics(session, target.id)}
        overrides = {
            o.pack_metric_code: o.expression
            for o in (
                await session.scalars(
                    select(pk.MetricOverride).where(
                        pk.MetricOverride.pack_install_id == install.id,
                        pk.MetricOverride.status == "active",
                    )
                )
            ).all()
        }
        base_attrs = {f"{e}.{a.code}": a.id for a, e in await _attributes(session, current.id)}
        new_attrs = {f"{e}.{a.code}" for a, e in await _attributes(session, target.id)}
        mapped_ids = set(
            (
                await session.scalars(
                    select(pk.EntityMapping.attribute_id).where(
                        pk.EntityMapping.pack_install_id == install.id,
                        pk.EntityMapping.valid_to.is_(None),
                    )
                )
            ).all()
        )
        mapped = {name for name, attr_id in base_attrs.items() if attr_id in mapped_ids}
        items = rules.merge_metrics(base, new, overrides) + rules.merge_mappings(
            set(base_attrs), new_attrs, mapped
        )
        conflicts = rules.open_conflicts(items)
        now = _now()
        row = pk.PackUpgrade(
            id=uuid7(),
            org_id=principal.org_id,
            pack_install_id=install.id,
            from_version_id=current.id,
            to_version_id=target.id,
            merge_result={"items": items},
            conflicts_open=conflicts,
            # Step 4 when a person must decide, else step 5: the test run of workflow pack_upgrade.
            status="needs_decision" if conflicts else "testing",
            created_by=principal.user_id,
            updated_by=principal.user_id,
            created_at=now,
            updated_at=now,
        )
        if dry_run:
            return _upgrade_view(row, dry_run=True)
        session.add(row)
        await session.flush()
        changed = [
            i["code"] for i in items if i["kind"] == "metric" and i["outcome"] != "keep_override"
        ]
        # BR-130: owners of agents using a changed metric are told 7 days before it applies.
        _emit(
            session,
            principal,
            "pack_upgrade",
            row.id,
            "pack_upgrade.created",
            pack_upgrade_id=row.id,
            pack_install_id=install.id,
            data_space_id=space,
            from_version_id=current.id,
            to_version_id=target.id,
            conflicts_open=conflicts,
            changed_metric_codes=changed,
            status=row.status,
        )
        await session.flush()
        response.status_code = 201
        return _upgrade_view(row)


@router.put(
    "/v1/pack-upgrades/{id}/decisions",
    response_model=PackUpgradeView,
    **endpoint("PUT", "/v1/pack-upgrades/{id}/decisions", operation_id="putPackUpgradeDecisions"),
)
async def put_upgrade_decisions(
    body: DecisionsPut, upgrade_id: PathId, principal: CurrentPrincipal, space: CurrentDataSpace
) -> PackUpgradeView:
    async with transaction(ROLE, principal) as session:
        row, install = await _upgrade(session, principal, space, upgrade_id)
        if row.status != "needs_decision":
            raise Problem(409, "invalid_state", f"The upgrade is {row.status}.")
        items = [dict(i) for i in (row.merge_result or {}).get("items", [])]
        conflicts = {(i["kind"], i["code"]): i for i in items if i.get("outcome") == "conflict"}
        for d in body.decisions:
            item = conflicts.get((d.kind, d.code))
            if item is None:
                raise Problem(
                    422,
                    "validation_failed",
                    f"{d.kind} {d.code} is not a conflict of this upgrade.",
                )
            item["decision"] = d.decision
            item["decided_by"] = str(principal.user_id)
        row.merge_result = {**(row.merge_result or {}), "items": items}
        row.conflicts_open = rules.open_conflicts(items)
        if row.conflicts_open == 0:
            row.status = "testing"
        row.updated_by, row.updated_at = principal.user_id, _now()
        _emit(
            session,
            principal,
            "pack_upgrade",
            row.id,
            "pack_upgrade.decided",
            pack_upgrade_id=row.id,
            pack_install_id=install.id,
            data_space_id=space,
            conflicts_open=row.conflicts_open,
            status=row.status,
        )
        await session.flush()
        return _upgrade_view(row)


@router.post(
    "/v1/pack-upgrades/{id}:apply",
    response_model=PackUpgradeView,
    **endpoint("POST", "/v1/pack-upgrades/{id}:apply", operation_id="applyPackUpgrade"),
)
async def apply_upgrade(
    upgrade_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    body: ApplyRequest = ApplyRequest(),
) -> PackUpgradeView:
    await _no_closing_period(principal, space)
    async with transaction(ROLE, principal) as session:
        row, install = await _upgrade(session, principal, space, upgrade_id)
        if row.conflicts_open > 0:
            raise Problem(
                409, "invalid_state", f"{row.conflicts_open} conflict(s) still wait for a decision."
            )
        if row.status != "pending_approval":
            raise Problem(
                409,
                "invalid_state",
                f"The upgrade is {row.status}; it is applied after its test run passes.",
            )
        if row.created_by == principal.user_id:
            raise Problem(403, "forbidden", "Whoever started the upgrade does not approve it.")
        target = await _version(session, principal, row.to_version_id)
        now = _now()
        if target.breaking_changes and (target.released_at or now) + rules.BREAKING_NOTICE > now:
            raise Problem(
                422,
                "business_rule_violated",
                "The version has breaking changes announced less than 30 days ago (BR-131).",
            )
        items = list((row.merge_result or {}).get("items", []))
        # "Theo gói mới": the organisation's override of that metric stops at the upgrade.
        take_new = {
            i["code"]
            for i in items
            if i.get("kind") == "metric" and i.get("decision") == "take_new"
        }
        superseded: list[uuid.UUID] = []
        if take_new:
            for o in (
                await session.scalars(
                    select(pk.MetricOverride).where(
                        pk.MetricOverride.pack_install_id == install.id,
                        pk.MetricOverride.status == "active",
                        pk.MetricOverride.pack_metric_code.in_(take_new),
                    )
                )
            ).all():
                o.status, o.updated_by, o.updated_at = "superseded", principal.user_id, now
                superseded.append(o.id)
        row.merge_result = {
            **(row.merge_result or {}),
            "superseded_override_ids": [str(i) for i in superseded],
        }
        row.status = "applied"
        row.approved_by = principal.user_id
        row.apply_from_period = body.apply_from_period
        row.rollback_until = now + rules.ROLLBACK_WINDOW
        row.updated_by, row.updated_at = principal.user_id, now
        install.previous_pack_version_id = install.pack_version_id
        install.pack_version_id = row.to_version_id
        install.updated_by, install.updated_at = principal.user_id, now
        # Step 6: closed periods keep their numbers; context and period apply from the next one.
        _emit(
            session,
            principal,
            "pack_upgrade",
            row.id,
            "pack_upgrade.applied",
            pack_upgrade_id=row.id,
            pack_install_id=install.id,
            data_space_id=space,
            from_version_id=row.from_version_id,
            to_version_id=row.to_version_id,
            apply_from_period=row.apply_from_period,
            rollback_until=row.rollback_until,
            superseded_override_ids=superseded,
            status=row.status,
        )
        await session.flush()
        return _upgrade_view(row)


@router.post(
    "/v1/pack-upgrades/{id}:rollback",
    response_model=PackUpgradeView,
    **endpoint("POST", "/v1/pack-upgrades/{id}:rollback", operation_id="rollbackPackUpgrade"),
)
async def rollback_upgrade(
    body: RollbackRequest, upgrade_id: PathId, principal: CurrentPrincipal, space: CurrentDataSpace
) -> PackUpgradeView:
    reason = body.reason.strip()
    if not reason:
        raise Problem(422, "validation_failed", "A rollback needs its reason.")
    async with transaction(ROLE, principal) as session:
        row, install = await _upgrade(session, principal, space, upgrade_id)
        now = _now()
        if row.status != "applied":
            raise Problem(409, "invalid_state", f"The upgrade is {row.status}, not applied.")
        if row.rollback_until is None or row.rollback_until <= now:
            raise Problem(409, "invalid_state", "The 30-day rollback window has passed.")
        if install.pack_version_id != row.to_version_id:
            raise Problem(409, "invalid_state", "The install has moved on since this upgrade.")
        # 6a: the overrides stay as they were before the upgrade.
        restored = [
            uuid.UUID(i) for i in (row.merge_result or {}).get("superseded_override_ids", [])
        ]
        if restored:
            for o in (
                await session.scalars(
                    select(pk.MetricOverride).where(
                        pk.MetricOverride.id.in_(restored),
                        pk.MetricOverride.status == "superseded",
                    )
                )
            ).all():
                o.status, o.updated_by, o.updated_at = "active", principal.user_id, now
        install.pack_version_id = row.from_version_id
        install.previous_pack_version_id = None
        install.updated_by, install.updated_at = principal.user_id, now
        row.status = "rolled_back"
        # No column holds the reason; the merge record keeps it with who and when.
        row.merge_result = {
            **(row.merge_result or {}),
            "rollback": {"reason": reason, "by": str(principal.user_id), "at": now.isoformat()},
        }
        row.updated_by, row.updated_at = principal.user_id, now
        _emit(
            session,
            principal,
            "pack_upgrade",
            row.id,
            "pack_upgrade.rolled_back",
            pack_upgrade_id=row.id,
            pack_install_id=install.id,
            data_space_id=space,
            pack_version_id=row.from_version_id,
            restored_override_ids=restored,
            status=row.status,
        )
        await session.flush()
        return _upgrade_view(row)
