import hmac
from typing import Any
from datetime import UTC, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Path
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from libs.basemodel.services_databaseconnector.postgres_orm.auth_api_orm import APIKeyORM
from libs.shared.db_context import get_tenant_session
from libs.shared.auth import gateway_key, require_role
try:
    from services.auth_api.domain.api_keys import (
        SCOPE_ROLES,
        VALID_SCOPES,
        api_key_matches,
        format_api_key,
        generate_api_key,
        hash_api_key,
        key_prefix_of,
    )
    from services.auth_api.infrastructure.db import get_session, orm_to_dict
    from services.auth_api.schemas import APIKeyCreateInput, APIKeyVerifyInput
except ModuleNotFoundError:
    from auth_api.domain.api_keys import (
        SCOPE_ROLES,
        VALID_SCOPES,
        api_key_matches,
        format_api_key,
        generate_api_key,
        hash_api_key,
        key_prefix_of,
    )
    from auth_api.infrastructure.db import get_session, orm_to_dict
    from auth_api.schemas import APIKeyCreateInput, APIKeyVerifyInput

router = APIRouter()


@router.get("/api/auth/api-keys")
def list_api_keys(
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> list[dict[str, Any]]:
    with get_session(x_tenant_id) as session:
        rows = session.scalars(
            select(APIKeyORM)
            .where(APIKeyORM.tenant_id == x_tenant_id)
            .order_by(APIKeyORM.created_at.desc())
        ).all()
        return [format_api_key(orm_to_dict(r)) for r in rows]


@router.get("/api/auth/api-keys/list-via-rls")
def list_api_keys_via_rls(
    db: Session = Depends(get_tenant_session),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> list[dict[str, Any]]:
    """RLS-only variant — same data, no WHERE clause in SQL.

    PoC for the rollout in [P0 #2]: trust Postgres RLS to scope rows by
    tenant rather than relying on every developer to remember `WHERE
    tenant_id = ...`. The dep `get_tenant_session` opens an ORM session as
    `app_user` and SETs `app.tenant_id` from the X-Tenant-Id header. The
    SELECT below would return ALL tenants' rows on a superuser connection;
    on app_user it returns only the caller's.

    When all routes have migrated to this pattern, the legacy WHERE-based
    sibling above can be retired.
    """
    rows = db.scalars(select(APIKeyORM).order_by(APIKeyORM.created_at.desc())).all()
    return [format_api_key(orm_to_dict(r)) for r in rows]


@router.post("/api/auth/api-keys", status_code=201)
def create_api_key(
    body: APIKeyCreateInput,
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> dict[str, Any]:
    if body.scope not in VALID_SCOPES:
        raise HTTPException(status_code=422, detail=f"Invalid scope: {body.scope}")

    raw_key, prefix = generate_api_key(body.scope)
    key_hash = hash_api_key(raw_key)
    expires_at = None
    if body.expires_at:
        try:
            expires_at = datetime.fromisoformat(body.expires_at).replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid expires_at format")

    with get_session(x_tenant_id) as session:
        row = APIKeyORM(
            tenant_id=x_tenant_id,
            created_by=user["user_id"],
            name=body.name,
            key_hash=key_hash,
            key_prefix=prefix,
            scope=body.scope,
            expires_at=expires_at,
        )
        session.add(row)
        session.flush()
        session.refresh(row)
        return {**format_api_key(orm_to_dict(row)), "raw_key": raw_key}


@router.post("/api/auth/api-keys/{key_id}/revoke")
def revoke_api_key(
    key_id: str = Path(...),
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> dict[str, Any]:
    with get_session(x_tenant_id) as session:
        row = session.scalar(
            select(APIKeyORM).where(
                APIKeyORM.id == key_id,
                APIKeyORM.tenant_id == x_tenant_id,
                APIKeyORM.revoked_at.is_(None),
            )
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Key not found or already revoked")
        row.revoked_at = datetime.now(timezone.utc)
        session.flush()
        return format_api_key(orm_to_dict(row))


@router.post("/api/auth/api-keys/{key_id}/rotate", status_code=201)
def rotate_api_key(
    key_id: str = Path(...),
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> dict[str, Any]:
    with get_session(x_tenant_id) as session:
        old = session.scalar(
            select(APIKeyORM)
            .where(
                APIKeyORM.id == key_id,
                APIKeyORM.tenant_id == x_tenant_id,
                APIKeyORM.revoked_at.is_(None),
            )
            .with_for_update()
        )
        if old is None:
            raise HTTPException(status_code=404, detail="Key not found or already revoked")
        old.revoked_at = datetime.now(timezone.utc)
        raw_key, prefix = generate_api_key(old.scope)
        new_row = APIKeyORM(
            tenant_id=x_tenant_id,
            created_by=user["user_id"],
            name=old.name,
            key_hash=hash_api_key(raw_key),
            key_prefix=prefix,
            scope=old.scope,
            expires_at=old.expires_at,
            rotated_from=old.id,
        )
        session.add(new_row)
        session.flush()
        session.refresh(new_row)
        return {**format_api_key(orm_to_dict(new_row)), "raw_key": raw_key}


# last_used_at is display-only; writing it on every verification would turn
# each Kong cache miss into a row update.
_LAST_USED_RESOLUTION = timedelta(minutes=1)


@router.post("/internal/api-keys/verify")
def verify_api_key(
    body: APIKeyVerifyInput,
    x_gateway_key: str = Header(default="", alias="X-Gateway-Key"),
) -> dict[str, Any]:
    """Who a presented API key acts as — called by Kong's aeroflow-jwks plugin.

    Not under /api/auth, so no Kong route reaches it; the gateway key proves the
    caller is the plugin. Without a configured gateway key nothing can prove
    that, so API keys stay disabled rather than open.
    """
    expected = gateway_key()
    if not expected or not hmac.compare_digest(expected, x_gateway_key):
        raise HTTPException(status_code=401, detail="Gateway key required")

    prefix = key_prefix_of(body.key)
    if prefix is None:
        raise HTTPException(status_code=401, detail="Invalid API key")

    now = datetime.now(UTC)
    # The key is what names its tenant, so the lookup cannot be scoped first:
    # auth_api_key_candidates is the SECURITY DEFINER read across tenants.
    with get_session() as session:
        candidates = session.execute(
            text("SELECT * FROM auth_api_key_candidates(:prefix)"), {"prefix": prefix}
        ).all()
    # The prefix has four random letters, so two live keys can share one.
    row = next((r for r in candidates if api_key_matches(body.key, r.key_hash)), None)
    if row is None or row.scope not in SCOPE_ROLES:
        raise HTTPException(status_code=401, detail="Invalid API key")
    if row.last_used_at is None or now - row.last_used_at >= _LAST_USED_RESOLUTION:
        with get_session(str(row.tenant_id)) as session:
            session.execute(
                update(APIKeyORM).where(APIKeyORM.id == row.id).values(last_used_at=now)
            )
    return {
        "key_id": str(row.id),
        "user_id": f"api-key:{row.id}",
        "user_name": row.name,
        "tenant_id": str(row.tenant_id),
        "scope": row.scope,
        "roles": [SCOPE_ROLES[row.scope]],
    }
