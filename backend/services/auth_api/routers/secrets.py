from typing import Any
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query

from libs.shared.auth import get_current_user, require_role
try:
    from services.auth_api.domain.secrets import (
        TENANT_MANAGEABLE_KEY_TYPES,
        TRANSIT_ALGORITHM_MAP,
        TRANSIT_KEY_TYPES,
        TRANSIT_MOUNT,
        VALID_KEY_TYPES,
        VAULT_MOUNT,
        build_transit_key_name,
        build_vault_path,
        format_secret,
    )
    from services.auth_api.infrastructure.config import log
    from services.auth_api.infrastructure.db import get_db
    from services.auth_api.infrastructure.vault import get_vault
    from services.auth_api.schemas import SecretCreateInput
    from services.auth_api.services.governance import (
        enforce_vault_not_in_panic_mode,
        get_governance_setting,
    )
except ModuleNotFoundError:
    from auth_api.domain.secrets import (
        TENANT_MANAGEABLE_KEY_TYPES,
        TRANSIT_ALGORITHM_MAP,
        TRANSIT_KEY_TYPES,
        TRANSIT_MOUNT,
        VALID_KEY_TYPES,
        VAULT_MOUNT,
        build_transit_key_name,
        build_vault_path,
        format_secret,
    )
    from auth_api.infrastructure.config import log
    from auth_api.infrastructure.db import get_db
    from auth_api.infrastructure.vault import get_vault
    from auth_api.schemas import SecretCreateInput
    from auth_api.services.governance import (
        enforce_vault_not_in_panic_mode,
        get_governance_setting,
    )

router = APIRouter()


def _is_platform_admin(user: dict[str, Any]) -> bool:
    return "platform-admin" in user["roles"]


def _is_tenant_admin_for(user: dict[str, Any], x_tenant_id: str) -> bool:
    # tenant-admin is only ever effective within the caller's own tenant —
    # require_role() doesn't check this itself (see auth_core.require_role),
    # so every call site that admits tenant-admin must check it manually,
    # same pattern as approvals.py's _can_approve().
    return "tenant-admin" in user["roles"] and user["tenant_id"] == x_tenant_id


@router.get("/api/auth/secrets")
def list_secrets(
    x_tenant_id:  str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(get_current_user),
    _panic: None = Depends(enforce_vault_not_in_panic_mode),
) -> list[dict[str, Any]]:
    is_platform_admin = _is_platform_admin(user)
    if not is_platform_admin and not _is_tenant_admin_for(user, x_tenant_id):
        raise HTTPException(status_code=403, detail="Required role: platform-admin or tenant-admin")

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if is_platform_admin:
                cur.execute(
                    "SELECT id, key_name, key_type, algorithm, realm, version, is_active, "
                    "rotation_due_at, last_rotated_at, created_by, created_at "
                    "FROM secrets_vault WHERE tenant_id = %s ORDER BY created_at DESC",
                    (x_tenant_id,),
                )
            else:
                cur.execute(
                    "SELECT id, key_name, key_type, algorithm, realm, version, is_active, "
                    "rotation_due_at, last_rotated_at, created_by, created_at "
                    "FROM secrets_vault WHERE tenant_id = %s AND key_type = ANY(%s) ORDER BY created_at DESC",
                    (x_tenant_id, list(TENANT_MANAGEABLE_KEY_TYPES)),
                )
            return [format_secret(r) for r in cur.fetchall()]
    finally:
        conn.close()


@router.post("/api/auth/secrets", status_code=201)
def create_secret(
    body: SecretCreateInput,
    x_tenant_id:  str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(get_current_user),
    _panic: None = Depends(enforce_vault_not_in_panic_mode),
) -> dict[str, Any]:
    if body.key_type not in VALID_KEY_TYPES:
        raise HTTPException(status_code=422, detail=f"Invalid key_type: {body.key_type}")
    if body.key_type not in TRANSIT_KEY_TYPES and not body.value:
        raise HTTPException(status_code=422, detail="value is required for this key type")

    is_platform_admin = _is_platform_admin(user)
    if body.key_type in TENANT_MANAGEABLE_KEY_TYPES:
        allowed = is_platform_admin or _is_tenant_admin_for(user, x_tenant_id)
    else:
        allowed = is_platform_admin
    if not allowed:
        raise HTTPException(status_code=403, detail=f"Required role: platform-admin{' or tenant-admin' if body.key_type in TENANT_MANAGEABLE_KEY_TYPES else ''}")

    vault = get_vault()
    path = build_vault_path(x_tenant_id, body.key_name)

    if body.key_type in TRANSIT_KEY_TYPES:
        t_key = build_transit_key_name(x_tenant_id, body.key_name)
        t_type = TRANSIT_ALGORITHM_MAP.get(body.algorithm, "rsa-4096")
        try:
            vault.secrets.transit.create_key(name=t_key, key_type=t_type, exportable=False, mount_point=TRANSIT_MOUNT)
        except Exception as e:
            err = str(e)
            if "already exists" not in err.lower():
                log.warning("transit_create_failed", error=err)
                raise HTTPException(status_code=502, detail="Failed to create Transit key")
        try:
            vault.secrets.kv.v2.create_or_update_secret(
                mount_point=VAULT_MOUNT, path=path,
                secret={"transit_key": t_key, "managed": "transit", "key_type": body.key_type, "algorithm": body.algorithm},
            )
        except Exception as e:
            log.warning("openbao_write_failed", error=str(e))
            raise HTTPException(status_code=502, detail="Failed to store secret metadata in vault")
        openbao_path = f"transit/keys/{t_key}"
    else:
        try:
            vault.secrets.kv.v2.create_or_update_secret(
                mount_point=VAULT_MOUNT, path=path,
                secret={"value": body.value, "key_type": body.key_type, "algorithm": body.algorithm},
            )
        except Exception as e:
            log.warning("openbao_write_failed", error=str(e))
            raise HTTPException(status_code=502, detail="Failed to store secret in vault")
        openbao_path = f"{VAULT_MOUNT}/data/{path}"

    rotation_due = datetime.now(timezone.utc) + timedelta(days=body.rotation_days)

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO secrets_vault (tenant_id, key_name, key_type, algorithm, realm, "
                "openbao_path, version, rotation_due_at, created_by) "
                "VALUES (%s,%s,%s,%s,%s,%s,1,%s,%s) "
                "RETURNING id, key_name, key_type, algorithm, realm, version, is_active, "
                "rotation_due_at, last_rotated_at, created_by, created_at",
                (x_tenant_id, body.key_name, body.key_type, body.algorithm,
                 body.realm, openbao_path, rotation_due, user["user_id"]),
            )
            row = cur.fetchone()
        conn.commit()
        return format_secret(row)
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        raise HTTPException(status_code=409, detail="Secret with this name already exists")
    finally:
        conn.close()


@router.delete("/api/auth/secrets/{secret_id}", status_code=204)
def delete_secret(
    secret_id:    str = Path(...),
    x_tenant_id:  str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(get_current_user),
    _panic: None = Depends(enforce_vault_not_in_panic_mode),
) -> None:
    is_platform_admin = _is_platform_admin(user)
    if not is_platform_admin and not _is_tenant_admin_for(user, x_tenant_id):
        raise HTTPException(status_code=403, detail="Required role: platform-admin or tenant-admin")

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if not is_platform_admin:
                cur.execute(
                    "SELECT key_type FROM secrets_vault WHERE id = %s AND tenant_id = %s",
                    (secret_id, x_tenant_id),
                )
                existing = cur.fetchone()
                if not existing:
                    raise HTTPException(status_code=404, detail="Secret not found")
                if existing["key_type"] not in TENANT_MANAGEABLE_KEY_TYPES:
                    raise HTTPException(status_code=403, detail="tenant-admin cannot delete this key_type")

            cur.execute(
                "UPDATE secrets_vault SET is_active = false WHERE id = %s AND tenant_id = %s "
                "RETURNING key_name, key_type, version",
                (secret_id, x_tenant_id),
            )
            row = cur.fetchone()
        if not row:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Secret not found")
        conn.commit()
    finally:
        conn.close()

    vault = get_vault()
    if row["key_type"] in TRANSIT_KEY_TYPES:
        try:
            t_key = build_transit_key_name(x_tenant_id, row["key_name"])
            vault.secrets.transit.delete_key(name=t_key, mount_point=TRANSIT_MOUNT)
        except Exception as e:
            log.warning("transit_delete_failed", error=str(e))
    else:
        try:
            path = build_vault_path(x_tenant_id, row["key_name"])
            vault.secrets.kv.v2.destroy_secret_versions(
                mount_point=VAULT_MOUNT, path=path, versions=[row["version"]],
            )
        except Exception as e:
            log.warning("openbao_destroy_failed", error=str(e))


@router.post("/api/auth/secrets/{secret_id}/rotate", status_code=201)
def rotate_secret(
    secret_id:    str = Path(...),
    x_tenant_id:  str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
    body: dict[str, Any] | None = None,
    _panic: None = Depends(enforce_vault_not_in_panic_mode),
) -> dict[str, Any]:
    new_value = (body or {}).get("value", "")

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, key_name, key_type, algorithm, realm, version, rotation_due_at "
                "FROM secrets_vault WHERE id = %s AND tenant_id = %s AND is_active = true "
                "FOR UPDATE",
                (secret_id, x_tenant_id),
            )
            old = cur.fetchone()
            if not old:
                raise HTTPException(status_code=404, detail="Secret not found or already inactive")

            new_version = old["version"] + 1
            vault = get_vault()

            if old["key_type"] in TRANSIT_KEY_TYPES:
                t_key = build_transit_key_name(x_tenant_id, old["key_name"])
                try:
                    vault.secrets.transit.rotate_key(name=t_key, mount_point=TRANSIT_MOUNT)
                except Exception as e:
                    log.warning("transit_rotate_failed", error=str(e))
                    raise HTTPException(status_code=502, detail="Failed to rotate Transit key")
                openbao_path = f"transit/keys/{t_key}"
            else:
                if not new_value:
                    raise HTTPException(status_code=422, detail="New secret value required for rotation")
                path = build_vault_path(x_tenant_id, old["key_name"])
                try:
                    vault.secrets.kv.v2.create_or_update_secret(
                        mount_point=VAULT_MOUNT, path=path,
                        secret={"value": new_value, "key_type": old["key_type"], "algorithm": old["algorithm"] or ""},
                    )
                except Exception as e:
                    log.warning("openbao_rotate_failed", error=str(e))
                    raise HTTPException(status_code=502, detail="Failed to write rotated secret to vault")
                openbao_path = f"{VAULT_MOUNT}/data/{path}"

            rotation_due = datetime.now(timezone.utc) + timedelta(days=90)

            cur.execute("UPDATE secrets_vault SET is_active = false WHERE id = %s", (old["id"],))
            cur.execute(
                "INSERT INTO secrets_vault (tenant_id, key_name, key_type, algorithm, realm, "
                "openbao_path, version, rotation_due_at, last_rotated_at, created_by) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now(),%s) "
                "RETURNING id, key_name, key_type, algorithm, realm, version, is_active, "
                "rotation_due_at, last_rotated_at, created_by, created_at",
                (x_tenant_id, old["key_name"], old["key_type"], old["algorithm"],
                 old["realm"], openbao_path, new_version, rotation_due, user["user_id"]),
            )
            new_row = cur.fetchone()

            cur.execute(
                "INSERT INTO key_rotations (secret_id, triggered_by, actor_id, old_version, new_version, status) "
                "VALUES (%s,'MANUAL',%s,%s,%s,'SUCCESS')",
                (new_row["id"], user["user_id"], old["version"], new_version),
            )
        conn.commit()
        return format_secret(new_row)
    finally:
        conn.close()


@router.get("/api/auth/secrets/{secret_id}/reveal")
def reveal_secret(
    secret_id:     str = Path(...),
    access_reason: str | None = Query(None),
    x_tenant_id:   str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
    _panic: None = Depends(enforce_vault_not_in_panic_mode),
) -> dict[str, Any]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT key_name, key_type, version, is_active FROM secrets_vault "
                "WHERE id = %s AND tenant_id = %s",
                (secret_id, x_tenant_id),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Secret not found")

            pii_log = get_governance_setting(cur, "vault_pii_access_log")

        if pii_log:
            if not access_reason or not access_reason.strip():
                raise HTTPException(
                    status_code=422,
                    detail="access_reason is required when PII Access Log is enabled",
                )
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO key_rotations "
                    "(secret_id, triggered_by, actor_id, old_version, new_version, status, access_reason) "
                    "VALUES (%s,'REVEAL',%s,%s,%s,'SUCCESS',%s)",
                    (secret_id, user["user_id"], row["version"], row["version"], access_reason.strip()),
                )
            conn.commit()
    finally:
        conn.close()

    vault = get_vault()
    if row["key_type"] in TRANSIT_KEY_TYPES:
        t_key = build_transit_key_name(x_tenant_id, row["key_name"])
        try:
            key_info = vault.secrets.transit.read_key(name=t_key, mount_point=TRANSIT_MOUNT)
            keys_data = key_info["data"]["keys"]
            latest_v  = str(key_info["data"]["latest_version"])
            pub_key   = keys_data.get(latest_v, {}).get("public_key", "")
            value = pub_key if pub_key else "(symmetric key — private key never exported from vault)"
        except Exception as e:
            log.warning("transit_read_failed", error=str(e))
            if not row["is_active"]:
                raise HTTPException(status_code=410, detail="Transit key was deleted — not recoverable")
            raise HTTPException(status_code=502, detail="Failed to read Transit key info")
    else:
        try:
            path = build_vault_path(x_tenant_id, row["key_name"])
            resp = vault.secrets.kv.v2.read_secret_version(mount_point=VAULT_MOUNT, path=path, raise_on_deleted_version=True)
            value = resp["data"]["data"].get("value", "")
        except Exception as e:
            log.warning("openbao_read_failed", error=str(e))
            if not row["is_active"]:
                raise HTTPException(status_code=410, detail="Secret was permanently deleted — value is not recoverable")
            raise HTTPException(status_code=502, detail="Failed to read secret from vault")

    return {"id": secret_id, "key_name": row["key_name"], "value": value, "is_active": row["is_active"]}


# ── Governance ────────────────────────────────────────────────────────────────
