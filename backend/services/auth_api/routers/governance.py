from typing import Any
from fastapi import APIRouter, Depends, Header, HTTPException, Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from libs.basemodel.services_databaseconnector.postgres_orm.auth_api_orm import (
    AuthPlatformSettingORM,
    KeyRotationORM,
    SecretVaultORM,
)
from libs.shared.auth import require_role
try:
    from services.auth_api.domain.secrets import TRANSIT_KEY_TYPES, TRANSIT_MOUNT, build_transit_key_name
    from services.auth_api.infrastructure.config import log
    from services.auth_api.infrastructure.db import get_session
    from services.auth_api.infrastructure.vault import get_vault
    from services.auth_api.schemas import SignPayload, VerifyPayload
    from services.auth_api.services.governance import (
        GOVERNANCE_KEYS,
        enforce_vault_not_in_panic_mode,
        get_governance_setting,
    )
except ModuleNotFoundError:
    from auth_api.domain.secrets import TRANSIT_KEY_TYPES, TRANSIT_MOUNT, build_transit_key_name
    from auth_api.infrastructure.config import log
    from auth_api.infrastructure.db import get_session
    from auth_api.infrastructure.vault import get_vault
    from auth_api.schemas import SignPayload, VerifyPayload
    from auth_api.services.governance import (
        GOVERNANCE_KEYS,
        enforce_vault_not_in_panic_mode,
        get_governance_setting,
    )

router = APIRouter()


def _upsert_setting(session: Any, key: str, value: str) -> None:
    stmt = (
        pg_insert(AuthPlatformSettingORM)
        .values(key=key, value=value)
        .on_conflict_do_update(
            index_elements=[AuthPlatformSettingORM.key],
            set_={"value": value},
        )
    )
    session.execute(stmt)


@router.get("/api/auth/secrets/governance")
def get_governance(
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> dict[str, Any]:
    with get_session() as session:
        return {key: get_governance_setting(session, key) for key in GOVERNANCE_KEYS}


@router.put("/api/auth/secrets/governance")
def set_governance(
    body: dict[str, Any],
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> dict[str, Any]:
    with get_session() as session:
        for key in GOVERNANCE_KEYS:
            if key in body:
                _upsert_setting(session, key, "true" if body[key] else "false")
        return {key: get_governance_setting(session, key) for key in GOVERNANCE_KEYS}


@router.post("/api/auth/secrets/panic", status_code=200)
def trigger_panic(
    x_tenant_id:  str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> dict[str, Any]:
    revoked = []
    with get_session() as session:
        rows = session.scalars(
            select(SecretVaultORM).where(
                SecretVaultORM.tenant_id == x_tenant_id,
                SecretVaultORM.is_active.is_(True),
            )
        ).all()
        for row in rows:
            row.is_active = False
            session.add(
                KeyRotationORM(
                    secret_id=row.id,
                    triggered_by="PANIC",
                    actor_id=user["user_id"],
                    old_version=row.version,
                    new_version=row.version,
                    status="SUCCESS",
                )
            )
            revoked.append(row.key_name)
        _upsert_setting(session, "vault_panic_mode", "true")

    log.warning("vault_panic_triggered", actor=user["user_id"], revoked_count=len(revoked))
    return {"revoked": revoked, "count": len(revoked)}


@router.get("/api/auth/secrets/hsm/status")
def hsm_status(
    x_tenant_id:  str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
    _panic: None = Depends(enforce_vault_not_in_panic_mode),
) -> dict[str, Any]:
    vault = get_vault()
    keys_out = []
    try:
        list_resp = vault.secrets.transit.list_keys(mount_point=TRANSIT_MOUNT)
        key_names = (list_resp or {}).get("data", {}).get("keys", [])
        prefix = x_tenant_id[:8] + "-"
        for name in key_names:
            if not name.startswith(prefix):
                continue
            try:
                info = vault.secrets.transit.read_key(name=name, mount_point=TRANSIT_MOUNT)
                d = info["data"]
                keys_out.append({
                    "name":           name,
                    "type":           d.get("type", "unknown"),
                    "latest_version": d.get("latest_version", 1),
                    "min_version":    d.get("min_decryption_version", 1),
                })
            except Exception:
                pass
    except Exception as e:
        # 404 = no keys yet, not a real error
        if "404" not in str(e) and "no keys" not in str(e).lower():
            log.warning("transit_list_failed", error=str(e))

    try:
        health = vault.sys.read_health_status(method="GET")
        sealed = health.get("sealed", False) if isinstance(health, dict) else False
    except Exception:
        sealed = False

    return {
        "transit_keys":  keys_out,
        "key_count":     len(keys_out),
        "openbao_sealed": sealed,
        "mount":         f"{TRANSIT_MOUNT}/",
    }

@router.post("/api/auth/secrets/{secret_id}/sign")
def sign_data(
    secret_id:    str = Path(...),
    body:         SignPayload | None = None,
    x_tenant_id:  str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
    _panic: None = Depends(enforce_vault_not_in_panic_mode),
) -> dict[str, Any]:
    if not body or not body.data:
        raise HTTPException(status_code=422, detail="data (base64) is required")

    with get_session() as session:
        row = session.scalar(
            select(SecretVaultORM).where(
                SecretVaultORM.id == secret_id,
                SecretVaultORM.tenant_id == x_tenant_id,
                SecretVaultORM.is_active.is_(True),
            )
        )

    if not row:
        raise HTTPException(status_code=404, detail="Secret not found or inactive")
    if row.key_type not in TRANSIT_KEY_TYPES:
        raise HTTPException(status_code=422, detail="sign is only available for Transit-managed key types")

    t_key = build_transit_key_name(x_tenant_id, row.key_name)
    try:
        vault = get_vault()
        resp  = vault.secrets.transit.sign_data(
            name=t_key, hash_input=body.data, hash_algorithm="sha2-256",
            mount_point=TRANSIT_MOUNT,
        )
        sig = resp["data"]["signature"]
        ver = resp["data"].get("key_version", 1)
    except Exception as e:
        log.warning("transit_sign_failed", error=str(e))
        raise HTTPException(status_code=502, detail="Transit sign operation failed")

    return {"signature": sig, "key_version": ver, "key_name": row.key_name}


@router.post("/api/auth/secrets/{secret_id}/verify")
def verify_data(
    secret_id:    str = Path(...),
    body:         VerifyPayload | None = None,
    x_tenant_id:  str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
    _panic: None = Depends(enforce_vault_not_in_panic_mode),
) -> dict[str, Any]:
    if not body or not body.data or not body.signature:
        raise HTTPException(status_code=422, detail="data and signature are required")

    with get_session() as session:
        row = session.scalar(
            select(SecretVaultORM).where(
                SecretVaultORM.id == secret_id,
                SecretVaultORM.tenant_id == x_tenant_id,
            )
        )

    if not row:
        raise HTTPException(status_code=404, detail="Secret not found")
    if row.key_type not in TRANSIT_KEY_TYPES:
        raise HTTPException(status_code=422, detail="verify is only available for Transit-managed key types")

    t_key = build_transit_key_name(x_tenant_id, row.key_name)
    try:
        vault = get_vault()
        resp  = vault.secrets.transit.verify_signed_data(
            name=t_key, hash_input=body.data, signature=body.signature,
            hash_algorithm="sha2-256", mount_point=TRANSIT_MOUNT,
        )
        valid = resp["data"]["valid"]
    except Exception as e:
        log.warning("transit_verify_failed", error=str(e))
        raise HTTPException(status_code=502, detail="Transit verify operation failed")

    return {"valid": valid, "key_name": row.key_name}


@router.get("/api/auth/secrets/pii-log")
def secrets_pii_log(
    x_tenant_id:  str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> list[dict[str, Any]]:
    with get_session() as session:
        rows = session.execute(
            select(KeyRotationORM, SecretVaultORM)
            .join(SecretVaultORM, SecretVaultORM.id == KeyRotationORM.secret_id)
            .where(
                SecretVaultORM.tenant_id == x_tenant_id,
                KeyRotationORM.triggered_by == "REVEAL",
            )
            .order_by(KeyRotationORM.created_at.desc())
            .limit(50)
        ).all()
        return [
            {
                "id":            str(rotation.id),
                "key_name":      secret.key_name,
                "key_type":      secret.key_type,
                "actor_id":      rotation.actor_id,
                "version":       rotation.old_version,
                "access_reason": rotation.access_reason,
                "time":          rotation.created_at.isoformat() if rotation.created_at else None,
            }
            for rotation, secret in rows
        ]


@router.get("/api/auth/secrets/audit-log")
def secrets_audit_log(
    x_tenant_id:  str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> list[dict[str, Any]]:
    with get_session() as session:
        rows = session.execute(
            select(KeyRotationORM, SecretVaultORM)
            .join(SecretVaultORM, SecretVaultORM.id == KeyRotationORM.secret_id)
            .where(SecretVaultORM.tenant_id == x_tenant_id)
            .order_by(KeyRotationORM.created_at.desc())
            .limit(20)
        ).all()
        return [
            {
                "id":           str(rotation.id),
                "event":        rotation.triggered_by,
                "key_name":     secret.key_name,
                "actor_id":     rotation.actor_id,
                "old_version":  rotation.old_version,
                "new_version":  rotation.new_version,
                "status":       rotation.status,
                "error":        rotation.error,
                "time":         rotation.created_at.isoformat() if rotation.created_at else None,
            }
            for rotation, secret in rows
        ]
