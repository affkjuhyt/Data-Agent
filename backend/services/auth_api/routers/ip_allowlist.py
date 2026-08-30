from typing import Any
from fastapi import APIRouter

from services.auth_api.auth_core import *
from services.auth_api.schemas import *

router = APIRouter()


@router.get("/api/auth/ip-allowlist/config")
def get_ip_config(
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> dict[str, Any]:
    return {"mode": _get_ip_mode()}


@router.put("/api/auth/ip-allowlist/config")
def set_ip_config(
    body: dict[str, Any],
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> dict[str, Any]:
    mode = body.get("mode", "allowlist")
    if mode not in ("allowlist", "allow_all"):
        raise HTTPException(status_code=422, detail="mode must be 'allowlist' or 'allow_all'")

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO platform_settings (key, value, updated_at)
                VALUES ('ip_allowlist_mode', %s, now())
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
            """, (mode,))
        conn.commit()
    finally:
        conn.close()

    _sync_kong(x_tenant_id)
    return {"mode": mode}


# ═══════════════════════════════════════════════════════════════════════════════
# IP ALLOWLIST CRUD
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/api/auth/ip-allowlists")
def list_ip_allowlists(
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> list[dict[str, Any]]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, cidr, label, is_active, created_by, created_at FROM ip_allowlists WHERE tenant_id = %s ORDER BY created_at DESC",
                (x_tenant_id,),
            )
            rows = cur.fetchall()
        return [_fmt_ip(r) for r in rows]
    finally:
        conn.close()


@router.post("/api/auth/ip-allowlists", status_code=201)
def create_ip_rule(
    body: IPRuleIn,
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> dict[str, Any]:
    try:
        ipaddress.ip_network(body.cidr, strict=False)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid CIDR: {body.cidr}")

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO ip_allowlists (tenant_id, cidr, label, is_active, created_by) VALUES (%s,%s,%s,%s,%s) RETURNING id, cidr, label, is_active, created_by, created_at",
                (x_tenant_id, body.cidr, body.label, body.is_active, user["user_id"]),
            )
            row = cur.fetchone()
        conn.commit()
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        raise HTTPException(status_code=409, detail="CIDR already exists for this tenant")
    finally:
        conn.close()

    _sync_kong(x_tenant_id)
    return _fmt_ip(row)


@router.patch("/api/auth/ip-allowlists/{rule_id}/toggle")
def toggle_ip_rule(
    rule_id: str = Path(...),
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> dict[str, Any]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "UPDATE ip_allowlists SET is_active = NOT is_active WHERE id = %s AND tenant_id = %s RETURNING id, cidr, label, is_active, created_by, created_at",
                (rule_id, x_tenant_id),
            )
            row = cur.fetchone()
        if not row:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Rule not found")
        conn.commit()
    finally:
        conn.close()

    _sync_kong(x_tenant_id)
    return _fmt_ip(row)


@router.delete("/api/auth/ip-allowlists/{rule_id}", status_code=204)
def delete_ip_rule(
    rule_id: str = Path(...),
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> None:
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ip_allowlists WHERE id = %s AND tenant_id = %s", (rule_id, x_tenant_id))
            if cur.rowcount == 0:
                conn.rollback()
                raise HTTPException(status_code=404, detail="Rule not found")
        conn.commit()
    finally:
        conn.close()

    _sync_kong(x_tenant_id)
