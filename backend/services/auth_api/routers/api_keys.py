from typing import Any
from fastapi import APIRouter

from auth_core import *
from schemas import *
from shared.db_context import get_tenant_db

router = APIRouter()


@router.get("/api/auth/api-keys")
def list_api_keys(
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> list[dict[str, Any]]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, name, key_prefix, scope, created_by, last_used_at, expires_at, revoked_at, rotated_from, created_at FROM api_keys WHERE tenant_id = %s ORDER BY created_at DESC",
                (x_tenant_id,),
            )
            return [_fmt_key(r) for r in cur.fetchall()]
    finally:
        conn.close()


@router.get("/api/auth/api-keys/list-via-rls")
def list_api_keys_via_rls(
    db: Any = Depends(get_tenant_db),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> list[dict[str, Any]]:
    """RLS-only variant — same data, no WHERE clause in SQL.

    PoC for the rollout in [P0 #2]: trust Postgres RLS to scope rows by
    tenant rather than relying on every developer to remember `WHERE
    tenant_id = ...`. The dep `get_tenant_db` opens a connection as
    `app_user` and SETs `app.tenant_id` from the X-Tenant-Id header. The
    SELECT below would return ALL tenants' rows on a superuser connection;
    on app_user it returns only the caller's.

    When all routes have migrated to this pattern, the legacy WHERE-based
    sibling above can be retired.
    """
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT id, name, key_prefix, scope, created_by, last_used_at, "
            "expires_at, revoked_at, rotated_from, created_at "
            "FROM api_keys ORDER BY created_at DESC"
        )
        return [_fmt_key(r) for r in cur.fetchall()]


@router.post("/api/auth/api-keys", status_code=201)
def create_api_key(
    body: APIKeyIn,
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> dict[str, Any]:
    if body.scope not in VALID_SCOPES:
        raise HTTPException(status_code=422, detail=f"Invalid scope: {body.scope}")

    raw_key, prefix = _gen_key(body.scope)
    key_hash = _hash_key(raw_key)
    expires_at = None
    if body.expires_at:
        try:
            expires_at = datetime.fromisoformat(body.expires_at).replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid expires_at format")

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO api_keys (tenant_id, created_by, name, key_hash, key_prefix, scope, expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id, name, key_prefix, scope, created_by, last_used_at, expires_at, revoked_at, rotated_from, created_at",
                (x_tenant_id, user["user_id"], body.name, key_hash, prefix, body.scope, expires_at),
            )
            row = cur.fetchone()
        conn.commit()
        return {**_fmt_key(row), "raw_key": raw_key}
    finally:
        conn.close()


@router.post("/api/auth/api-keys/{key_id}/revoke")
def revoke_api_key(
    key_id: str = Path(...),
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> dict[str, Any]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "UPDATE api_keys SET revoked_at = now() WHERE id = %s AND tenant_id = %s AND revoked_at IS NULL RETURNING id, name, key_prefix, scope, created_by, last_used_at, expires_at, revoked_at, rotated_from, created_at",
                (key_id, x_tenant_id),
            )
            row = cur.fetchone()
        if not row:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Key not found or already revoked")
        conn.commit()
        return _fmt_key(row)
    finally:
        conn.close()


@router.post("/api/auth/api-keys/{key_id}/rotate", status_code=201)
def rotate_api_key(
    key_id: str = Path(...),
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    user: dict[str, Any] = Depends(require_role("platform-admin")),
) -> dict[str, Any]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, scope, name, expires_at FROM api_keys WHERE id = %s AND tenant_id = %s AND revoked_at IS NULL",
                (key_id, x_tenant_id),
            )
            old = cur.fetchone()
            if not old:
                raise HTTPException(status_code=404, detail="Key not found or already revoked")
            cur.execute("UPDATE api_keys SET revoked_at = now() WHERE id = %s", (old["id"],))
            raw_key, prefix = _gen_key(old["scope"])
            cur.execute(
                "INSERT INTO api_keys (tenant_id, created_by, name, key_hash, key_prefix, scope, expires_at, rotated_from) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id, name, key_prefix, scope, created_by, last_used_at, expires_at, revoked_at, rotated_from, created_at",
                (x_tenant_id, user["user_id"], old["name"], _hash_key(raw_key), prefix, old["scope"], old["expires_at"], old["id"]),
            )
            new_row = cur.fetchone()
        conn.commit()
        return {**_fmt_key(new_row), "raw_key": raw_key}
    finally:
        conn.close()
