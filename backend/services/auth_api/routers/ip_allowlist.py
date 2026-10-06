from typing import Any
import ipaddress

from fastapi import APIRouter, Depends, Header, HTTPException, Path
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from libs.basemodel.services_databaseconnector.postgres_orm.auth_api_orm import (
    AuthPlatformSettingORM,
    IPAllowlistORM,
)
from libs.shared.auth import require_role
from services.auth_api.domain.ip_allowlists import format_ip_allowlist
from services.auth_api.infrastructure.db import get_session, orm_to_dict
from services.auth_api.schemas import IPAllowlistCreateInput
from services.auth_api.services.kong_sync import get_ip_allowlist_mode, sync_kong_ip_restrictions

_UNIQUE_VIOLATION = "23505"

router = APIRouter()


@router.get("/api/auth/ip-allowlist/config")
def get_ip_config(
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> dict[str, Any]:
    return {"mode": get_ip_allowlist_mode()}


@router.put("/api/auth/ip-allowlist/config")
def set_ip_config(
    body: dict[str, Any],
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> dict[str, Any]:
    mode = body.get("mode", "allowlist")
    if mode not in ("allowlist", "allow_all"):
        raise HTTPException(status_code=422, detail="mode must be 'allowlist' or 'allow_all'")

    with get_session() as session:
        stmt = (
            pg_insert(AuthPlatformSettingORM)
            .values(key="ip_allowlist_mode", value=mode)
            .on_conflict_do_update(
                index_elements=[AuthPlatformSettingORM.key],
                set_={"value": mode},
            )
        )
        session.execute(stmt)

    sync_kong_ip_restrictions(x_tenant_id)
    return {"mode": mode}


# ═══════════════════════════════════════════════════════════════════════════════
# IP ALLOWLIST CRUD
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/api/auth/ip-allowlists")
def list_ip_allowlists(
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> list[dict[str, Any]]:
    with get_session(x_tenant_id) as session:
        rows = session.scalars(
            select(IPAllowlistORM)
            .where(IPAllowlistORM.tenant_id == x_tenant_id)
            .order_by(IPAllowlistORM.created_at.desc())
        ).all()
        return [format_ip_allowlist(orm_to_dict(r)) for r in rows]


@router.post("/api/auth/ip-allowlists", status_code=201)
def create_ip_rule(
    body: IPAllowlistCreateInput,
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> dict[str, Any]:
    try:
        ipaddress.ip_network(body.cidr, strict=False)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid CIDR: {body.cidr}")

    try:
        with get_session(x_tenant_id) as session:
            row = IPAllowlistORM(
                tenant_id=x_tenant_id,
                cidr=body.cidr,
                label=body.label,
                is_active=body.is_active,
                created_by=user["user_id"],
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            result = format_ip_allowlist(orm_to_dict(row))
    except IntegrityError as exc:
        # Only a unique violation is "already exists"; a foreign-key failure (unknown
        # tenant) must not be reported as a duplicate CIDR.
        if getattr(exc.orig, "pgcode", None) != _UNIQUE_VIOLATION:
            raise
        raise HTTPException(status_code=409, detail="CIDR already exists for this tenant")

    sync_kong_ip_restrictions(x_tenant_id)
    return result


@router.patch("/api/auth/ip-allowlists/{rule_id}/toggle")
def toggle_ip_rule(
    rule_id: str = Path(...),
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> dict[str, Any]:
    with get_session(x_tenant_id) as session:
        row = session.scalar(
            select(IPAllowlistORM).where(
                IPAllowlistORM.id == rule_id,
                IPAllowlistORM.tenant_id == x_tenant_id,
            )
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Rule not found")
        row.is_active = not row.is_active
        session.flush()
        result = format_ip_allowlist(orm_to_dict(row))

    sync_kong_ip_restrictions(x_tenant_id)
    return result


@router.delete("/api/auth/ip-allowlists/{rule_id}", status_code=204)
def delete_ip_rule(
    rule_id: str = Path(...),
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> None:
    with get_session(x_tenant_id) as session:
        row = session.scalar(
            select(IPAllowlistORM).where(
                IPAllowlistORM.id == rule_id,
                IPAllowlistORM.tenant_id == x_tenant_id,
            )
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Rule not found")
        session.delete(row)

    sync_kong_ip_restrictions(x_tenant_id)
