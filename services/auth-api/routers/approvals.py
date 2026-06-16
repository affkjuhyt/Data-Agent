"""
Microsoft SSO first-login approval flow.

Flow:
  1. User authenticates via Microsoft SSO → valid JWT.
  2. Frontend calls GET /api/auth/me:
       - Not in DB + is MS SSO user  → status=needs_profile
       - In DB, status=pending        → status=pending_approval
       - In DB, status=approved       → status=active
       - In DB, status=rejected       → status=rejected
       - Not MS SSO user              → status=active
  3. Frontend shows ProfileSetup form (role + tenant selection).
  4. POST /api/auth/me/profile → creates pending record, disables KC account.
  5. Platform-admin OR tenant-admin of the matching tenant approves via POST endpoints.
  6. On approval: role_id + tenant_id user attributes set in Keycloak, account enabled.
"""
import os
import urllib.request
import urllib.parse
import json
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, Depends, Header, HTTPException, Path
from pydantic import BaseModel

from auth_core import get_db
from shared.auth import get_current_user, require_role

router = APIRouter()

KC_URL      = os.environ.get("KC_URL",            "http://keycloak-lb:8080")
KC_REALM    = os.environ.get("KC_REALM",          "aeroflow")
KC_ADMIN    = os.environ.get("KC_ADMIN_USER",     "admin")
KC_PASSWORD = os.environ.get("KC_ADMIN_PASSWORD", "admin")

# Roles available for SSO users to request.
AVAILABLE_ROLES = [
    "ai-engineer",
    "executive-viewer",
    "tenant-admin",
    "release-manager",
    "data-analyst",
]

_kc_token_cache: dict = {}


def _kc_admin_token() -> str:
    import time
    cached = _kc_token_cache.get("token")
    exp    = _kc_token_cache.get("exp", 0)
    if cached and time.time() < exp - 30:
        return cached
    data = urllib.parse.urlencode({
        "client_id":  "admin-cli",
        "username":   KC_ADMIN,
        "password":   KC_PASSWORD,
        "grant_type": "password",
    }).encode()
    req = urllib.request.Request(
        f"{KC_URL}/realms/master/protocol/openid-connect/token", data=data
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        payload = json.loads(r.read())
    import time
    _kc_token_cache["token"] = payload["access_token"]
    _kc_token_cache["exp"]   = time.time() + payload.get("expires_in", 60)
    return _kc_token_cache["token"]


def _kc_get(path: str) -> dict | list:
    token = _kc_admin_token()
    req   = urllib.request.Request(
        f"{KC_URL}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise


def _kc_put(path: str, body: dict) -> None:
    token = _kc_admin_token()
    data  = json.dumps(body).encode()
    req   = urllib.request.Request(
        f"{KC_URL}{path}",
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="PUT",
    )
    urllib.request.urlopen(req, timeout=10).close()


def _is_microsoft_sso_user(user_id: str) -> tuple[bool, str, str]:
    try:
        identities = _kc_get(f"/admin/realms/{KC_REALM}/users/{user_id}/federated-identity")
        for ident in identities:
            if ident.get("identityProvider", "").lower() in ("microsoft", "azure-ad", "entra-id"):
                user_rep = _kc_get(f"/admin/realms/{KC_REALM}/users/{user_id}")
                email    = user_rep.get("email", "")
                name     = (
                    f"{user_rep.get('firstName', '')} {user_rep.get('lastName', '')}".strip()
                    or user_rep.get("username", "")
                )
                return True, email, name
    except Exception:
        pass
    return False, "", ""


def _set_kc_user_enabled(user_id: str, enabled: bool) -> None:
    try:
        user_rep = _kc_get(f"/admin/realms/{KC_REALM}/users/{user_id}")
        if isinstance(user_rep, dict):
            user_rep["enabled"] = enabled
            _kc_put(f"/admin/realms/{KC_REALM}/users/{user_id}", user_rep)
    except Exception:
        pass


def _assign_kc_user_role_tenant(user_id: str, role: str, tenant_id: str) -> None:
    """Set role_id and tenant_id user attributes and enable the Keycloak account.

    Raises on any failure so callers can roll back the DB transaction.
    """
    import logging
    user_rep = _kc_get(f"/admin/realms/{KC_REALM}/users/{user_id}")
    if not isinstance(user_rep, dict):
        raise ValueError(f"User {user_id} not found in Keycloak")
    # Build a clean representation with only safe scalar fields to avoid
    # Keycloak rejecting the PUT due to special characters in name fields.
    clean = {
        "id":            user_rep.get("id"),
        "username":      user_rep.get("username"),
        "email":         user_rep.get("email", ""),
        "emailVerified": True,
        "enabled":       True,
        "attributes":    {
            **(user_rep.get("attributes") or {}),
            "role_id":   [role],
            "tenant_id": [tenant_id],
        },
    }
    _kc_put(f"/admin/realms/{KC_REALM}/users/{user_id}", clean)
    logging.info("assign_kc: set role=%s tenant=%s for user %s", role, tenant_id, user_id)


def _can_approve(actor: dict, requested_tenant_id: str) -> bool:
    """platform-admin can approve all; tenant-admin only for their own tenant."""
    roles = actor.get("roles", [])
    if "platform-admin" in roles:
        return True
    if "tenant-admin" in roles and actor.get("tenant_id") == requested_tenant_id:
        return True
    return False


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/api/auth/me")
def get_me(user: dict = Depends(get_current_user)):
    """
    Returns the calling user's approval gate status.
    - Regular Keycloak users → active
    - New MS SSO user (not in DB) → needs_profile (frontend shows role/tenant form)
    - MS SSO user with pending record → pending_approval
    - MS SSO user approved → active
    - MS SSO user rejected → rejected
    """
    user_id = user["user_id"]
    conn    = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT status, created_at FROM microsoft_sso_approvals WHERE keycloak_user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if row:
        if row["status"] == "approved":
            return {"status": "active"}
        return {"status": row["status"] if row["status"] != "pending" else "pending_approval",
                "since": row["created_at"].isoformat()}

    # Not in DB — first time. Check if MS SSO user.
    is_ms, _email, _name = _is_microsoft_sso_user(user_id)
    if not is_ms:
        return {"status": "active"}

    # New MS SSO user: ask them to choose role + tenant before creating the record.
    return {"status": "needs_profile"}


class ProfileBody(BaseModel):
    requested_role:      str
    requested_tenant_id: str


@router.post("/api/auth/me/profile")
def submit_profile(body: ProfileBody, user: dict = Depends(get_current_user)):
    """
    Called once by a new MS SSO user after they select their role and tenant.
    Creates the pending approval record and disables the Keycloak account.
    """
    user_id = user["user_id"]

    if body.requested_role not in AVAILABLE_ROLES:
        raise HTTPException(status_code=422, detail=f"Invalid role. Choose from: {AVAILABLE_ROLES}")
    if not body.requested_tenant_id.strip():
        raise HTTPException(status_code=422, detail="tenant_id is required")

    # Re-verify it's an MS SSO user (prevent regular users from hitting this endpoint).
    is_ms, email, name = _is_microsoft_sso_user(user_id)
    if not is_ms:
        raise HTTPException(status_code=403, detail="Not a Microsoft SSO user")

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO microsoft_sso_approvals
                  (keycloak_user_id, email, display_name, requested_role, requested_tenant_id)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (keycloak_user_id) DO UPDATE
                  SET requested_role      = EXCLUDED.requested_role,
                      requested_tenant_id = EXCLUDED.requested_tenant_id,
                      status              = 'pending'
                RETURNING status, created_at
                """,
                (user_id, email, name,
                 body.requested_role, body.requested_tenant_id.strip()),
            )
            inserted = cur.fetchone()
        conn.commit()
    finally:
        conn.close()

    _set_kc_user_enabled(user_id, False)

    since = inserted["created_at"].isoformat() if inserted else datetime.now(timezone.utc).isoformat()
    return {"status": "pending_approval", "since": since}


@router.get("/api/auth/me/roles")
def get_available_roles(_user: dict = Depends(get_current_user)):
    """Return the list of roles an SSO user can request."""
    return {"roles": AVAILABLE_ROLES}


@router.get("/api/auth/me/tenants")
def get_available_tenants(_user: dict = Depends(get_current_user)):
    """Return the list of active tenants for SSO profile setup."""
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, name, slug FROM tenants WHERE is_active = true AND deleted_at IS NULL ORDER BY name"
            )
            return [{"id": str(r["id"]), "name": r["name"], "slug": r["slug"]} for r in cur.fetchall()]
    finally:
        conn.close()


@router.get("/api/auth/approvals")
def list_approvals(
    status: str = "pending",
    user: dict = Depends(get_current_user),
):
    """
    List Microsoft SSO approval requests.
    - platform-admin: sees all requests.
    - tenant-admin: sees only requests where requested_tenant_id matches their tenant.
    - Others: 403.
    """
    roles = user.get("roles", [])
    is_platform_admin = "platform-admin" in roles
    is_tenant_admin   = "tenant-admin" in roles

    if not is_platform_admin and not is_tenant_admin:
        raise HTTPException(status_code=403, detail="Admin role required")

    valid = ("pending", "approved", "rejected", "all")
    if status not in valid:
        raise HTTPException(status_code=422, detail=f"status must be one of {valid}")

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if is_platform_admin:
                # See everything
                if status == "all":
                    cur.execute(
                        "SELECT * FROM microsoft_sso_approvals ORDER BY created_at DESC LIMIT 500"
                    )
                else:
                    cur.execute(
                        "SELECT * FROM microsoft_sso_approvals WHERE status = %s ORDER BY created_at DESC LIMIT 500",
                        (status,),
                    )
            else:
                # tenant-admin: restrict to their tenant
                tenant_id = user.get("tenant_id", "")
                if status == "all":
                    cur.execute(
                        "SELECT * FROM microsoft_sso_approvals WHERE requested_tenant_id = %s ORDER BY created_at DESC LIMIT 500",
                        (tenant_id,),
                    )
                else:
                    cur.execute(
                        "SELECT * FROM microsoft_sso_approvals WHERE status = %s AND requested_tenant_id = %s ORDER BY created_at DESC LIMIT 500",
                        (status, tenant_id),
                    )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


@router.post("/api/auth/approvals/{user_id}/approve")
def approve_user(
    user_id: str = Path(...),
    actor:   dict = Depends(get_current_user),
):
    """
    Approve a pending MS SSO user.
    Assigns requested role + tenant to Keycloak user and enables their account.
    """
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM microsoft_sso_approvals WHERE keycloak_user_id = %s AND status = 'pending'",
                (user_id,),
            )
            row = cur.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Pending approval not found")

        if not _can_approve(actor, row["requested_tenant_id"] or ""):
            raise HTTPException(
                status_code=403,
                detail="Only platform-admin or tenant-admin of the matching tenant can approve",
            )

        # Update Keycloak first — if it fails the exception propagates and
        # we never commit, keeping DB and Keycloak in sync.
        _assign_kc_user_role_tenant(
            user_id,
            row["requested_role"]      or "",
            row["requested_tenant_id"] or "",
        )

        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE microsoft_sso_approvals
                SET status = 'approved', reviewed_by = %s, reviewed_at = now()
                WHERE keycloak_user_id = %s AND status = 'pending'
                """,
                (actor["user_id"], user_id),
            )
        conn.commit()
    finally:
        conn.close()

    return {"ok": True, "user_id": user_id, "status": "approved"}


@router.post("/api/auth/approvals/{user_id}/reject")
def reject_user(
    user_id: str = Path(...),
    actor:   dict = Depends(get_current_user),
):
    """Reject a pending MS SSO user. Keeps their Keycloak account disabled."""
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT requested_tenant_id FROM microsoft_sso_approvals WHERE keycloak_user_id = %s AND status = 'pending'",
                (user_id,),
            )
            row = cur.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Pending approval not found")

        if not _can_approve(actor, row["requested_tenant_id"] or ""):
            raise HTTPException(
                status_code=403,
                detail="Only platform-admin or tenant-admin of the matching tenant can reject",
            )

        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE microsoft_sso_approvals
                SET status = 'rejected', reviewed_by = %s, reviewed_at = now()
                WHERE keycloak_user_id = %s AND status = 'pending'
                """,
                (actor["user_id"], user_id),
            )
        conn.commit()
    finally:
        conn.close()

    return {"ok": True, "user_id": user_id, "status": "rejected"}
