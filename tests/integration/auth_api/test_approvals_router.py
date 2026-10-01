"""routers/approvals.py — the Microsoft SSO first-login gate — against real Postgres.

The router has no `require_role`: every endpoint takes `get_current_user` and
does its own role/tenant check. What depends on the database lives here: the
upsert that refuses to flip an approved user back to pending, a tenant-admin
seeing only their tenant's requests, and the FOR UPDATE row lock that keeps an
approve and a reject from both passing the "is it pending" read while Keycloak is
being updated. Keycloak is replaced by a recorder; the DB-free checks (auth
guard, 422s, role gates) are in tests/unit/auth_api/test_approvals_router.py.
"""

from __future__ import annotations

import threading
from typing import Any

import psycopg2
import psycopg2.errors
import pytest
from fastapi import HTTPException

from .conftest import TENANT_1, TENANT_2

pytestmark = pytest.mark.integration


class _Keycloak:
    """Records the Keycloak side effects the router triggers."""

    def __init__(self) -> None:
        self.is_ms_user = True
        self.assign_error: Exception | None = None
        self.disable_error: Exception | None = None
        self.while_assigning: Any = None
        self.enabled: list[tuple[str, bool]] = []
        self.assigned: list[tuple[str, str, str]] = []

    def is_microsoft_sso_user(self, user_id: str) -> tuple[bool, str, str]:
        if self.is_ms_user:
            return True, "jane@example.com", "Jane Doe"
        return False, "", ""

    def set_enabled(self, user_id: str, enabled: bool) -> None:
        if self.disable_error is not None:
            raise self.disable_error
        self.enabled.append((user_id, enabled))

    def assign(self, user_id: str, role: str, tenant_id: str) -> None:
        if self.while_assigning is not None:
            self.while_assigning()
        if self.assign_error is not None:
            raise self.assign_error
        self.assigned.append((user_id, role, tenant_id))


@pytest.fixture
def approvals(auth_api_app):
    from services.auth_api.routers import approvals

    return approvals


@pytest.fixture
def kc(approvals, monkeypatch: pytest.MonkeyPatch) -> _Keycloak:
    recorder = _Keycloak()
    monkeypatch.setattr(approvals, "_is_microsoft_sso_user", recorder.is_microsoft_sso_user)
    monkeypatch.setattr(approvals, "_set_kc_user_enabled", recorder.set_enabled)
    monkeypatch.setattr(approvals, "_assign_kc_user_role_tenant", recorder.assign)
    return recorder


def _seed(
    db_conn,
    user_id: str = "sso-user",
    *,
    status: str = "pending",
    tenant: str = TENANT_1,
    role: str = "data-analyst",
) -> None:
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO microsoft_sso_approvals "
            "(keycloak_user_id, email, display_name, status, requested_role, requested_tenant_id) "
            "VALUES (%s, %s, 'Jane Doe', %s, %s, %s)",
            (user_id, f"{user_id}@example.com", status, role, tenant),
        )


def _row(db_conn, user_id: str = "sso-user") -> dict[str, Any] | None:
    with db_conn.cursor() as cur:
        cur.execute("SELECT * FROM microsoft_sso_approvals WHERE keycloak_user_id = %s", (user_id,))
        return cur.fetchone()


def _sso_headers(headers_factory, user_id: str = "sso-user") -> dict[str, str]:
    return headers_factory(user_id=user_id, roles="")


class TestMe:
    def test_approved_user_is_active(self, migrated_client, headers_factory, db_conn, kc) -> None:
        _seed(db_conn, "sso-user", status="approved")
        _seed(db_conn, "other-user", status="pending")

        resp = migrated_client.get("/api/auth/me", headers=_sso_headers(headers_factory))

        # The caller's own row decides, not someone else's pending one.
        assert resp.json() == {"status": "active"}

    def test_pending_user_is_gated(self, migrated_client, headers_factory, db_conn, kc) -> None:
        _seed(db_conn)

        resp = migrated_client.get("/api/auth/me", headers=_sso_headers(headers_factory))

        body = resp.json()
        assert body["status"] == "pending_approval"
        assert body["since"] == _row(db_conn)["created_at"].isoformat()

    def test_unknown_sso_user_must_submit_profile(
        self, migrated_client, headers_factory, kc
    ) -> None:
        resp = migrated_client.get("/api/auth/me", headers=_sso_headers(headers_factory))

        assert resp.json() == {"status": "needs_profile"}


class TestSubmitProfile:
    def test_creates_pending_record_and_disables_account(
        self,
        migrated_client,
        headers_factory,
        db_conn,
        kc,
    ) -> None:
        resp = migrated_client.post(
            "/api/auth/me/profile",
            json={"requested_role": "data-analyst", "requested_tenant_id": f"  {TENANT_1} "},
            headers=_sso_headers(headers_factory),
        )

        assert resp.status_code == 200, resp.text
        row = _row(db_conn)
        assert row is not None, "the pending row was not committed"
        assert resp.json() == {"status": "pending_approval", "since": row["created_at"].isoformat()}
        assert (row["email"], row["display_name"]) == ("jane@example.com", "Jane Doe")
        assert (row["requested_role"], row["requested_tenant_id"], row["status"]) == (
            "data-analyst",
            TENANT_1,
            "pending",
        )
        assert kc.enabled == [("sso-user", False)]

    def test_resubmitting_an_approved_profile_is_refused_and_disables_nothing(
        self,
        migrated_client,
        headers_factory,
        db_conn,
        kc,
    ) -> None:
        """The upsert only touches a row that is not approved; no row back means the
        user is already approved, and flipping them to pending (and disabling the
        account) on a repeat call would lock a legitimate user out."""
        _seed(db_conn, status="approved")

        resp = migrated_client.post(
            "/api/auth/me/profile",
            json={"requested_role": "tenant-admin", "requested_tenant_id": TENANT_2},
            headers=_sso_headers(headers_factory),
        )

        assert resp.status_code == 409
        row = _row(db_conn)
        assert (row["status"], row["requested_role"], row["requested_tenant_id"]) == (
            "approved",
            "data-analyst",
            TENANT_1,
        )
        assert kc.enabled == []

    def test_resubmitting_a_rejected_profile_reopens_it(
        self,
        migrated_client,
        headers_factory,
        db_conn,
        kc,
    ) -> None:
        _seed(db_conn, status="rejected")

        resp = migrated_client.post(
            "/api/auth/me/profile",
            json={"requested_role": "ai-engineer", "requested_tenant_id": TENANT_2},
            headers=_sso_headers(headers_factory),
        )

        assert resp.status_code == 200, resp.text
        row = _row(db_conn)
        assert (row["status"], row["requested_role"], row["requested_tenant_id"]) == (
            "pending",
            "ai-engineer",
            TENANT_2,
        )
        assert kc.enabled == [("sso-user", False)]

    def test_a_failed_account_disable_is_reported_not_swallowed(
        self,
        migrated_client,
        headers_factory,
        db_conn,
        kc,
    ) -> None:
        kc.disable_error = RuntimeError("keycloak down")

        resp = migrated_client.post(
            "/api/auth/me/profile",
            json={"requested_role": "data-analyst", "requested_tenant_id": TENANT_1},
            headers=_sso_headers(headers_factory),
        )

        assert resp.status_code == 502
        # The pending row stays committed, so a retry finishes the job.
        assert _row(db_conn)["status"] == "pending"


class TestListApprovals:
    def test_tenant_admin_sees_only_own_tenant(
        self,
        migrated_client,
        headers_factory,
        db_conn,
        kc,
    ) -> None:
        _seed(db_conn, "own-user", tenant=TENANT_1)
        _seed(db_conn, "foreign-user", tenant=TENANT_2)
        _seed(db_conn, "own-approved", tenant=TENANT_1, status="approved")

        resp = migrated_client.get(
            "/api/auth/approvals",
            headers=headers_factory(roles="tenant-admin", tenant=TENANT_1),
        )

        assert resp.status_code == 200
        assert [r["keycloak_user_id"] for r in resp.json()] == ["own-user"]

    def test_platform_admin_all_is_unfiltered(
        self,
        migrated_client,
        headers_factory,
        db_conn,
        kc,
    ) -> None:
        _seed(db_conn, "own-user", tenant=TENANT_1)
        _seed(db_conn, "foreign-user", tenant=TENANT_2, status="rejected")
        _seed(db_conn, "approved-user", tenant=TENANT_2, status="approved")

        resp = migrated_client.get("/api/auth/approvals?status=all", headers=headers_factory())

        assert resp.status_code == 200
        assert {r["keycloak_user_id"] for r in resp.json()} == {
            "own-user",
            "foreign-user",
            "approved-user",
        }


class TestApproveReject:
    def test_platform_admin_approves_and_keycloak_gets_requested_role(
        self,
        migrated_client,
        headers_factory,
        db_conn,
        kc,
    ) -> None:
        _seed(db_conn)

        resp = migrated_client.post(
            "/api/auth/approvals/sso-user/approve",
            headers=headers_factory(user_id="admin-1", tenant=TENANT_2),
        )

        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "user_id": "sso-user", "status": "approved"}
        assert kc.assigned == [("sso-user", "data-analyst", TENANT_1)]
        row = _row(db_conn)
        assert (row["status"], row["reviewed_by"]) == ("approved", "admin-1")
        assert row["reviewed_at"] is not None

    def test_tenant_admin_of_other_tenant_cannot_approve(
        self,
        migrated_client,
        headers_factory,
        db_conn,
        kc,
    ) -> None:
        _seed(db_conn, tenant=TENANT_1)

        resp = migrated_client.post(
            "/api/auth/approvals/sso-user/approve",
            headers=headers_factory(roles="tenant-admin", tenant=TENANT_2),
        )

        assert resp.status_code == 403
        assert kc.assigned == []
        assert _row(db_conn)["status"] == "pending"

    def test_keycloak_failure_leaves_request_pending(
        self,
        migrated_client,
        headers_factory,
        db_conn,
        kc,
    ) -> None:
        # Keycloak is updated before the DB so a failure there must not leave
        # the row marked approved while the account stays disabled.
        _seed(db_conn)
        kc.assign_error = RuntimeError("keycloak down")

        resp = migrated_client.post(
            "/api/auth/approvals/sso-user/approve", headers=headers_factory()
        )

        assert resp.status_code == 500
        row = _row(db_conn)
        assert (row["status"], row["reviewed_by"], row["reviewed_at"]) == ("pending", None, None)

    def test_approve_without_pending_request_is_404(
        self,
        migrated_client,
        headers_factory,
        db_conn,
        kc,
    ) -> None:
        _seed(db_conn, "already-rejected", status="rejected")

        for user_id in ("ghost", "already-rejected"):
            resp = migrated_client.post(
                f"/api/auth/approvals/{user_id}/approve", headers=headers_factory()
            )
            assert resp.status_code == 404, user_id
        assert kc.assigned == []
        assert _row(db_conn, "already-rejected")["status"] == "rejected"

    def test_tenant_admin_rejects_own_tenant_request(
        self,
        migrated_client,
        headers_factory,
        db_conn,
        kc,
    ) -> None:
        _seed(db_conn, tenant=TENANT_1)

        resp = migrated_client.post(
            "/api/auth/approvals/sso-user/reject",
            headers=headers_factory(user_id="admin-1", roles="tenant-admin", tenant=TENANT_1),
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"
        row = _row(db_conn)
        assert (row["status"], row["reviewed_by"]) == ("rejected", "admin-1")
        assert kc.enabled == [] and kc.assigned == []


_ADMIN = {"user_id": "admin-1", "roles": ["platform-admin"], "tenant_id": TENANT_1}


class TestRowLock:
    """approve/reject read the pending row, (approve) call Keycloak, then update.
    Without FOR UPDATE a racing approve and reject both pass the read and Keycloak
    ends up enabled while the row says rejected."""

    def test_the_row_stays_locked_while_keycloak_is_updated(
        self,
        approvals,
        migrated_auth_db,
        db_conn,
        kc,
    ) -> None:
        _seed(db_conn)
        probed: list[str] = []

        def _try_to_lock() -> None:
            probe = psycopg2.connect(migrated_auth_db["dsn"])
            try:
                with probe.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM microsoft_sso_approvals WHERE keycloak_user_id = 'sso-user' "
                        "FOR UPDATE NOWAIT"
                    )
                probed.append("acquired")
            except psycopg2.errors.LockNotAvailable:
                probed.append("locked")
            finally:
                probe.rollback()
                probe.close()

        kc.while_assigning = _try_to_lock

        approvals.approve_user(user_id="sso-user", actor=_ADMIN)

        assert probed == ["locked"]

    @pytest.mark.parametrize(
        ("action", "winner_status"),
        [("approve", "rejected"), ("reject", "approved")],
    )
    def test_a_decision_made_while_waiting_for_the_lock_wins(
        self,
        approvals,
        migrated_auth_db,
        db_conn,
        kc,
        action: str,
        winner_status: str,
    ) -> None:
        """Another decision holds the row; once it commits, the waiting one re-reads
        the row, finds it no longer pending and backs off with 404 instead of
        overwriting it (and, for approve, without touching Keycloak)."""
        _seed(db_conn)
        handler = approvals.approve_user if action == "approve" else approvals.reject_user
        holder = psycopg2.connect(migrated_auth_db["dsn"])
        outcome: list[Any] = []

        def _decide() -> None:
            try:
                outcome.append(handler(user_id="sso-user", actor=_ADMIN))
            except HTTPException as exc:
                outcome.append(exc.status_code)

        try:
            with holder.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM microsoft_sso_approvals WHERE keycloak_user_id = 'sso-user' FOR UPDATE"
                )
                racer = threading.Thread(target=_decide)
                racer.start()
                racer.join(timeout=1.5)
                assert racer.is_alive(), f"{action} did not wait for the row lock: {outcome}"
                cur.execute(
                    "UPDATE microsoft_sso_approvals SET status = %s WHERE keycloak_user_id = 'sso-user'",
                    (winner_status,),
                )
            holder.commit()
            racer.join(timeout=10)
        finally:
            holder.close()

        assert outcome == [404]
        assert kc.assigned == []
        assert _row(db_conn)["status"] == winner_status
