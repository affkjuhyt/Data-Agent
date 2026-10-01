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
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from libs.basemodel.services_databaseconnector.postgres_orm.auth_api_orm import MicrosoftSSOApprovalORM
from libs.basemodel.services_databaseconnector.postgres_orm.auth_release_orm import TenantsORM
from libs.shared.auth import get_current_user
from services.auth_api.infrastructure.db import get_session, orm_to_dict

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
    "data-analyst",
]

_kc_token_cache: dict[str, Any] = {}


def _kc_admin_token() -> str:
    import time
    cached = _kc_token_cache.get("token")
    exp    = _kc_token_cache.get("exp", 0)
    if cached and time.time() < exp - 30:
        return cast(str, cached)
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
    return cast(str, _kc_token_cache["token"])


def _kc_get(path: str) -> dict[str, Any] | list[Any]:
    token = _kc_admin_token()
    req   = urllib.request.Request(
        f"{KC_URL}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return cast("dict[str, Any] | list[Any]", json.loads(r.read()))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise


def _kc_put(path: str, body: dict[str, Any]) -> None:
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
            if not isinstance(ident, dict):
                continue
            if ident.get("identityProvider", "").lower() in ("microsoft", "azure-ad", "entra-id"):
                user_rep = _kc_get(f"/admin/realms/{KC_REALM}/users/{user_id}")
                if not isinstance(user_rep, dict):
                    continue
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
    """Raises when Keycloak cannot be updated: the account's enabled flag is the real
    gate, so a silent failure would leave a user "pending" in the DB but still able to
    log in."""
    user_rep = _kc_get(f"/admin/realms/{KC_REALM}/users/{user_id}")
    if isinstance(user_rep, dict):
        user_rep["enabled"] = enabled
        _kc_put(f"/admin/realms/{KC_REALM}/users/{user_id}", user_rep)


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


def _can_approve(actor: dict[str, Any], requested_tenant_id: str) -> bool:
    """platform-admin can approve all; tenant-admin only for their own tenant."""
    roles = actor.get("roles", [])
    if "platform-admin" in roles:
        return True
    if "tenant-admin" in roles and actor.get("tenant_id") == requested_tenant_id:
        return True
    return False


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/api/auth/me")
def get_me(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """
    Returns the calling user's approval gate status.
    - Regular Keycloak users → active
    - New MS SSO user (not in DB) → needs_profile (frontend shows role/tenant form)
    - MS SSO user with pending record → pending_approval
    - MS SSO user approved → active
    - MS SSO user rejected → rejected
    """
    user_id = user["user_id"]
    with get_session() as session:
        row = session.scalar(
            select(MicrosoftSSOApprovalORM).where(MicrosoftSSOApprovalORM.keycloak_user_id == user_id)
        )

    if row:
        if row.status == "approved":
            return {"status": "active"}
        return {
            "status": row.status if row.status != "pending" else "pending_approval",
            "since": row.created_at.isoformat(),
        }

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
def submit_profile(body: ProfileBody, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
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

    with get_session() as session:
        stmt = (
            pg_insert(MicrosoftSSOApprovalORM)
            .values(
                keycloak_user_id=user_id,
                email=email,
                display_name=name,
                requested_role=body.requested_role,
                requested_tenant_id=body.requested_tenant_id.strip(),
            )
            .on_conflict_do_update(
                index_elements=[MicrosoftSSOApprovalORM.keycloak_user_id],
                set_={
                    "requested_role": body.requested_role,
                    "requested_tenant_id": body.requested_tenant_id.strip(),
                    "status": "pending",
                },
                where=MicrosoftSSOApprovalORM.status != "approved",
            )
            .returning(MicrosoftSSOApprovalORM.status, MicrosoftSSOApprovalORM.created_at)
        )
        inserted = session.execute(stmt).mappings().one_or_none()

    # No row back: the conflict guard refused, because the user is already approved.
    # Re-submitting must not flip an approved account back to pending and disable it.
    if inserted is None:
        raise HTTPException(status_code=409, detail="Profile already approved")

    try:
        _set_kc_user_enabled(user_id, False)
    except Exception as exc:
        # The pending row is committed, so a retry of this call finishes the job.
        raise HTTPException(status_code=502, detail="Could not disable the account; retry") from exc

    since = inserted["created_at"].isoformat() if inserted else datetime.now(timezone.utc).isoformat()
    return {"status": "pending_approval", "since": since}


@router.get("/api/auth/me/roles")
def get_available_roles(_user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """Return the list of roles an SSO user can request."""
    return {"roles": AVAILABLE_ROLES}


@router.get("/api/auth/me/tenants")
def get_available_tenants(_user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    """Return the list of active tenants for SSO profile setup."""
    with get_session() as session:
        rows = session.scalars(
            select(TenantsORM)
            .where(TenantsORM.is_active.is_(True), TenantsORM.deleted_at.is_(None))
            .order_by(TenantsORM.name)
        ).all()
        return [{"id": str(row.id), "name": row.name, "slug": row.slug} for row in rows]


@router.get("/api/auth/approvals")
def list_approvals(
    status: str = "pending",
    user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
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

    with get_session() as session:
        stmt = select(MicrosoftSSOApprovalORM)
        if status != "all":
            stmt = stmt.where(MicrosoftSSOApprovalORM.status == status)
        if not is_platform_admin:
            stmt = stmt.where(MicrosoftSSOApprovalORM.requested_tenant_id == user.get("tenant_id", ""))
        rows = session.scalars(stmt.order_by(MicrosoftSSOApprovalORM.created_at.desc()).limit(500)).all()
        return [orm_to_dict(row) for row in rows]


@router.post("/api/auth/approvals/{user_id}/approve")
def approve_user(
    user_id: str = Path(...),
    actor:   dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Approve a pending MS SSO user.
    Assigns requested role + tenant to Keycloak user and enables their account.
    """
    with get_session() as session:
        row = session.scalar(
            select(MicrosoftSSOApprovalORM)
            .where(
                MicrosoftSSOApprovalORM.keycloak_user_id == user_id,
                MicrosoftSSOApprovalORM.status == "pending",
            )
            .with_for_update()
        )

        if not row:
            raise HTTPException(status_code=404, detail="Pending approval not found")

        if not _can_approve(actor, row.requested_tenant_id or ""):
            raise HTTPException(
                status_code=403,
                detail="Only platform-admin or tenant-admin of the matching tenant can approve",
            )

        # Update Keycloak first — if it fails the exception propagates and
        # we never commit, keeping DB and Keycloak in sync.
        _assign_kc_user_role_tenant(
            user_id,
            row.requested_role or "",
            row.requested_tenant_id or "",
        )

        row.status = "approved"
        row.reviewed_by = actor["user_id"]
        row.reviewed_at = datetime.now(timezone.utc)

    return {"ok": True, "user_id": user_id, "status": "approved"}


@router.post("/api/auth/approvals/{user_id}/reject")
def reject_user(
    user_id: str = Path(...),
    actor:   dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Reject a pending MS SSO user. Keeps their Keycloak account disabled."""
    with get_session() as session:
        row = session.scalar(
            select(MicrosoftSSOApprovalORM)
            .where(
                MicrosoftSSOApprovalORM.keycloak_user_id == user_id,
                MicrosoftSSOApprovalORM.status == "pending",
            )
            .with_for_update()
        )

        if not row:
            raise HTTPException(status_code=404, detail="Pending approval not found")

        if not _can_approve(actor, row.requested_tenant_id or ""):
            raise HTTPException(
                status_code=403,
                detail="Only platform-admin or tenant-admin of the matching tenant can reject",
            )

        row.status = "rejected"
        row.reviewed_by = actor["user_id"]
        row.reviewed_at = datetime.now(timezone.utc)

    return {"ok": True, "user_id": user_id, "status": "rejected"}
