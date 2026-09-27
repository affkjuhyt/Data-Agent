from typing import Any
from fastapi import APIRouter, Depends, Header, HTTPException, Path

import psycopg2.extras

from libs.shared.auth import require_role
try:
    from services.auth_api.domain.secrets import TRANSIT_KEY_TYPES, TRANSIT_MOUNT, build_transit_key_name
    from services.auth_api.infrastructure.config import log
    from services.auth_api.infrastructure.db import get_db
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
    from auth_api.infrastructure.db import get_db
    from auth_api.infrastructure.vault import get_vault
    from auth_api.schemas import SignPayload, VerifyPayload
    from auth_api.services.governance import (
        GOVERNANCE_KEYS,
        enforce_vault_not_in_panic_mode,
        get_governance_setting,
    )

router = APIRouter()


@router.get("/api/auth/secrets/governance")
def get_governance(
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> dict[str, Any]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            result = {}
            for key in GOVERNANCE_KEYS:
                result[key] = get_governance_setting(cur, key)
        return result
    finally:
        conn.close()


@router.put("/api/auth/secrets/governance")
def set_governance(
    body: dict[str, Any],
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> dict[str, Any]:
    conn = get_db()
    try:
        with conn.cursor() as cur:
            for key in GOVERNANCE_KEYS:
                if key in body:
                    val = "true" if body[key] else "false"
                    cur.execute(
                        "INSERT INTO platform_settings (key, value, updated_at) VALUES (%s,%s,now()) "
                        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()",
                        (key, val),
                    )
        conn.commit()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            result = {key: get_governance_setting(cur, key) for key in GOVERNANCE_KEYS}
        return result
    finally:
        conn.close()


@router.post("/api/auth/secrets/panic", status_code=200)
def trigger_panic(
    x_tenant_id:  str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> dict[str, Any]:
    conn = get_db()
    revoked = []
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "UPDATE secrets_vault SET is_active = false "
                "WHERE tenant_id = %s AND is_active = true "
                "RETURNING id, key_name, version",
                (x_tenant_id,),
            )
            rows = cur.fetchall()
            for r in rows:
                cur.execute(
                    "INSERT INTO key_rotations (secret_id, triggered_by, actor_id, old_version, new_version, status) "
                    "VALUES (%s,'PANIC',%s,%s,%s,'SUCCESS')",
                    (r["id"], user["user_id"], r["version"], r["version"]),
                )
                revoked.append(r["key_name"])
        conn.commit()
    finally:
        conn.close()

    # Enable panic mode in governance settings
    conn2 = get_db()
    try:
        with conn2.cursor() as cur:
            cur.execute(
                "INSERT INTO platform_settings (key, value, updated_at) VALUES ('vault_panic_mode','true',now()) "
                "ON CONFLICT (key) DO UPDATE SET value='true', updated_at=now()",
            )
        conn2.commit()
    finally:
        conn2.close()

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

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT key_name, key_type FROM secrets_vault WHERE id = %s AND tenant_id = %s AND is_active = true",
                (secret_id, x_tenant_id),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Secret not found or inactive")
    if row["key_type"] not in TRANSIT_KEY_TYPES:
        raise HTTPException(status_code=422, detail="sign is only available for Transit-managed key types")

    t_key = build_transit_key_name(x_tenant_id, row["key_name"])
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

    return {"signature": sig, "key_version": ver, "key_name": row["key_name"]}


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

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT key_name, key_type FROM secrets_vault WHERE id = %s AND tenant_id = %s",
                (secret_id, x_tenant_id),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Secret not found")
    if row["key_type"] not in TRANSIT_KEY_TYPES:
        raise HTTPException(status_code=422, detail="verify is only available for Transit-managed key types")

    t_key = build_transit_key_name(x_tenant_id, row["key_name"])
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

    return {"valid": valid, "key_name": row["key_name"]}


@router.get("/api/auth/secrets/pii-log")
def secrets_pii_log(
    x_tenant_id:  str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> list[dict[str, Any]]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT kr.id, kr.actor_id, kr.old_version, kr.created_at, "
                "       kr.access_reason, sv.key_name, sv.key_type "
                "FROM key_rotations kr "
                "JOIN secrets_vault sv ON sv.id = kr.secret_id "
                "WHERE sv.tenant_id = %s AND kr.triggered_by = 'REVEAL' "
                "ORDER BY kr.created_at DESC LIMIT 50",
                (x_tenant_id,),
            )
            rows = cur.fetchall()
        return [
            {
                "id":            str(r["id"]),
                "key_name":      r["key_name"],
                "key_type":      r["key_type"],
                "actor_id":      r["actor_id"],
                "version":       r["old_version"],
                "access_reason": r["access_reason"],
                "time":          r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]
    finally:
        conn.close()


@router.get("/api/auth/secrets/audit-log")
def secrets_audit_log(
    x_tenant_id:  str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> list[dict[str, Any]]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT kr.id, kr.triggered_by, kr.actor_id, kr.old_version, kr.new_version, "
                "kr.status, kr.error, kr.created_at, sv.key_name "
                "FROM key_rotations kr "
                "JOIN secrets_vault sv ON sv.id = kr.secret_id "
                "WHERE sv.tenant_id = %s "
                "ORDER BY kr.created_at DESC LIMIT 20",
                (x_tenant_id,),
            )
            rows = cur.fetchall()
        return [
            {
                "id":           str(r["id"]),
                "event":        r["triggered_by"],
                "key_name":     r["key_name"],
                "actor_id":     r["actor_id"],
                "old_version":  r["old_version"],
                "new_version":  r["new_version"],
                "status":       r["status"],
                "error":        r["error"],
                "time":         r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]
    finally:
        conn.close()
