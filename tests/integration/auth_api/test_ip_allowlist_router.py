"""routers/ip_allowlist.py against real Postgres.

The rules feed Kong's ip-restriction plugin, so what matters is that the tenant
from the header scopes every read and write, that a failed write leaves the table
as it was, and that Kong is only re-synced after a change actually landed. Kong
is a recorder; the DB-free checks (auth guard, role gate, 422s) are in
tests/unit/auth_api/test_ip_allowlist_router.py.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from .conftest import TENANT_1, TENANT_2

pytestmark = pytest.mark.integration


@pytest.fixture
def synced(auth_api_app, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Kong re-syncs, recorded by tenant."""
    from services.auth_api.routers import ip_allowlist

    calls: list[str] = []
    monkeypatch.setattr(ip_allowlist, "sync_kong_ip_restrictions", calls.append)
    return calls


def _seed_rule(
    db_conn,
    *,
    tenant: str = TENANT_1,
    cidr: str = "10.0.0.0/8",
    label: str | None = "office",
    is_active: bool = True,
) -> str:
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ip_allowlists (tenant_id, cidr, label, is_active, created_by) "
            "VALUES (%s, %s, %s, %s, 'user-1') RETURNING id",
            (tenant, cidr, label, is_active),
        )
        return str(cur.fetchone()["id"])


def _rules(db_conn) -> list[dict[str, Any]]:
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT id::text, tenant_id::text, cidr, label, is_active, created_by FROM ip_allowlists"
        )
        return list(cur.fetchall())


def _setting(db_conn, key: str) -> str:
    with db_conn.cursor() as cur:
        cur.execute("SELECT value FROM platform_settings WHERE key = %s", (key,))
        return cur.fetchone()["value"]


class TestConfig:
    def test_put_persists_mode_then_resyncs_kong(
        self,
        migrated_client,
        headers_factory,
        db_conn,
        synced,
    ) -> None:
        resp = migrated_client.put(
            "/api/auth/ip-allowlist/config",
            json={"mode": "allowlist"},
            headers=headers_factory(),
        )

        assert resp.status_code == 200
        assert resp.json() == {"mode": "allowlist"}
        assert _setting(db_conn, "ip_allowlist_mode") == "allowlist"
        assert synced == [TENANT_1]
        # GET reads back what was stored.
        assert migrated_client.get(
            "/api/auth/ip-allowlist/config", headers=headers_factory()
        ).json() == {
            "mode": "allowlist",
        }


class TestList:
    def test_lists_only_the_header_tenant(
        self, migrated_client, headers_factory, db_conn, synced
    ) -> None:
        own = _seed_rule(db_conn, tenant=TENANT_1, label=None)
        _seed_rule(db_conn, tenant=TENANT_2, cidr="192.168.0.0/16")

        resp = migrated_client.get(
            "/api/auth/ip-allowlists", headers=headers_factory(tenant=TENANT_1)
        )

        assert resp.status_code == 200
        [rule] = resp.json()
        created_at = rule.pop("created_at")
        assert rule == {
            "id": own,
            "cidr": "10.0.0.0/8",
            "label": "",
            "is_active": True,
            "created_by": "user-1",
        }
        assert created_at


class TestCreate:
    def test_inserts_for_header_tenant_and_actor(
        self,
        migrated_client,
        headers_factory,
        db_conn,
        synced,
    ) -> None:
        resp = migrated_client.post(
            "/api/auth/ip-allowlists",
            json={"cidr": "192.168.1.0/24", "label": "vpn"},
            headers=headers_factory(user_id="user-1", tenant=TENANT_2),
        )

        assert resp.status_code == 201, resp.text
        assert resp.json()["cidr"] == "192.168.1.0/24"
        [stored] = _rules(db_conn)
        assert stored["id"] == resp.json()["id"]
        assert (
            stored["tenant_id"],
            stored["cidr"],
            stored["label"],
            stored["is_active"],
            stored["created_by"],
        ) == (
            TENANT_2,
            "192.168.1.0/24",
            "vpn",
            True,
            "user-1",
        )
        assert synced == [TENANT_2]

    def test_duplicate_cidr_is_409_rolled_back_and_not_synced(
        self,
        migrated_client,
        headers_factory,
        db_conn,
        synced,
    ) -> None:
        """The handler maps a constraint violation to 409. The migrated schema has
        no unique (tenant_id, cidr) index — a duplicate is simply stored — so the
        test adds one to drive that path, as the old psycopg2 test did with a
        scripted UniqueViolation."""
        _seed_rule(db_conn, cidr="10.0.0.0/8")
        with db_conn.cursor() as cur:
            cur.execute(
                "CREATE UNIQUE INDEX test_uq_ip_allowlists_tenant_cidr ON ip_allowlists (tenant_id, cidr)"
            )
        try:
            resp = migrated_client.post(
                "/api/auth/ip-allowlists",
                json={"cidr": "10.0.0.0/8"},
                headers=headers_factory(),
            )
        finally:
            with db_conn.cursor() as cur:
                cur.execute("DROP INDEX test_uq_ip_allowlists_tenant_cidr")

        assert resp.status_code == 409
        assert len(_rules(db_conn)) == 1
        assert synced == []

    def test_an_unknown_tenant_is_not_reported_as_a_duplicate(
        self,
        migrated_client,
        headers_factory,
        db_conn,
        synced,
    ) -> None:
        """Only a unique violation means "CIDR already exists". A tenant missing from
        "Tenants" fails the foreign key instead, and answering 409 for it would send
        an operator looking for a duplicate that does not exist."""
        resp = migrated_client.post(
            "/api/auth/ip-allowlists",
            json={"cidr": "10.0.0.0/8"},
            headers=headers_factory(tenant=str(uuid.uuid4())),
        )

        assert resp.status_code == 500
        assert _rules(db_conn) == []
        assert synced == []


class TestToggleAndDelete:
    def test_toggle_is_scoped_to_header_tenant(
        self,
        migrated_client,
        headers_factory,
        db_conn,
        synced,
    ) -> None:
        rule = _seed_rule(db_conn, tenant=TENANT_1, is_active=True)

        resp = migrated_client.patch(
            f"/api/auth/ip-allowlists/{rule}/toggle", headers=headers_factory()
        )

        assert resp.status_code == 200
        assert resp.json()["is_active"] is False
        assert [r["is_active"] for r in _rules(db_conn)] == [False]
        assert synced == [TENANT_1]

    def test_toggle_of_another_tenants_rule_is_404(
        self,
        migrated_client,
        headers_factory,
        db_conn,
        synced,
    ) -> None:
        # The tenant filter makes a foreign rule indistinguishable from a missing one.
        foreign = _seed_rule(db_conn, tenant=TENANT_2, is_active=True)

        resp = migrated_client.patch(
            f"/api/auth/ip-allowlists/{foreign}/toggle",
            headers=headers_factory(tenant=TENANT_1),
        )

        assert resp.status_code == 404
        assert [r["is_active"] for r in _rules(db_conn)] == [True]
        assert synced == []

    def test_delete_commits_and_resyncs(
        self, migrated_client, headers_factory, db_conn, synced
    ) -> None:
        rule = _seed_rule(db_conn, tenant=TENANT_1)
        _seed_rule(db_conn, tenant=TENANT_2)

        resp = migrated_client.delete(f"/api/auth/ip-allowlists/{rule}", headers=headers_factory())

        assert resp.status_code == 204
        assert [r["tenant_id"] for r in _rules(db_conn)] == [TENANT_2]
        assert synced == [TENANT_1]

    @pytest.mark.parametrize("whose", ["unknown", "other-tenant"])
    def test_delete_of_unknown_or_foreign_rule_is_404(
        self,
        migrated_client,
        headers_factory,
        db_conn,
        synced,
        whose: str,
    ) -> None:
        foreign = _seed_rule(db_conn, tenant=TENANT_2)
        rule = str(uuid.uuid4()) if whose == "unknown" else foreign

        resp = migrated_client.delete(
            f"/api/auth/ip-allowlists/{rule}", headers=headers_factory(tenant=TENANT_1)
        )

        assert resp.status_code == 404
        assert [r["id"] for r in _rules(db_conn)] == [foreign]
        assert synced == []
