"""The auth_api read endpoints that returned 500 on the dev cluster (27/09/2026):
`/api/auth/secrets/pii-log` and `/audit-log` selected `key_rotations.rotated_at`,
a column the table never had (b8c9d0e1f2a3 creates `created_at`), and
`/api/auth/api-keys` used `psycopg2.extras` without importing it. Each handler
runs here against the schema the alembic chain builds, so a mapped column the
migrations never create fails the request instead of passing a mock.
"""

from __future__ import annotations

from typing import Any

import pytest

from .conftest import TENANT_1, TENANT_2

pytestmark = pytest.mark.integration


def _seed_rotation(db_conn, tenant: str, triggered_by: str, *, key_name: str) -> dict[str, Any]:
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO secrets_vault (tenant_id, key_name, key_type) VALUES (%s, %s, 'HMAC_KEY') RETURNING id",
            (tenant, key_name),
        )
        secret_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO key_rotations "
            "(secret_id, triggered_by, actor_id, old_version, new_version, status, access_reason) "
            "VALUES (%s, %s, 'u1', 1, 2, 'SUCCESS', 'audit') RETURNING id, created_at",
            (secret_id, triggered_by),
        )
        return cur.fetchone()


@pytest.mark.parametrize("path", ["/api/auth/secrets/pii-log", "/api/auth/secrets/audit-log"])
def test_rotation_logs_select_only_real_columns(
    migrated_client, headers_factory, db_conn, path: str
) -> None:
    own = _seed_rotation(db_conn, TENANT_1, "REVEAL", key_name="own-key")
    _seed_rotation(db_conn, TENANT_2, "REVEAL", key_name="foreign-key")

    resp = migrated_client.get(path, headers=headers_factory(tenant=TENANT_1))

    assert resp.status_code == 200, resp.text
    [entry] = resp.json()
    assert entry["id"] == str(own["id"])
    assert entry["key_name"] == "own-key"
    assert entry["time"] == own["created_at"].isoformat()


def test_pii_log_lists_only_reveals(migrated_client, headers_factory, db_conn) -> None:
    _seed_rotation(db_conn, TENANT_1, "MANUAL", key_name="rotated")
    reveal = _seed_rotation(db_conn, TENANT_1, "REVEAL", key_name="revealed")

    resp = migrated_client.get("/api/auth/secrets/pii-log", headers=headers_factory())

    assert [e["id"] for e in resp.json()] == [str(reveal["id"])]


def test_list_api_keys_runs(migrated_client, headers_factory) -> None:
    resp = migrated_client.get("/api/auth/api-keys", headers=headers_factory())

    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_panic_mode_locks_only_the_tenant_it_was_triggered_for(
    migrated_client, headers_factory
) -> None:
    """Panic used to set one platform-wide flag, so a panic for one tenant
    answered 503 on every tenant's secret routes."""
    tenant_1, tenant_2 = headers_factory(tenant=TENANT_1), headers_factory(tenant=TENANT_2)

    assert migrated_client.post("/api/auth/secrets/panic", headers=tenant_1).status_code == 200

    assert migrated_client.get("/api/auth/secrets", headers=tenant_1).status_code == 503
    assert migrated_client.get("/api/auth/secrets", headers=tenant_2).status_code == 200
    assert migrated_client.get("/api/auth/secrets/governance", headers=tenant_2).json()[
        "vault_panic_mode"
    ] is False

    off = migrated_client.put(
        "/api/auth/secrets/governance", json={"vault_panic_mode": False}, headers=tenant_1
    )
    assert off.json()["vault_panic_mode"] is False
    assert migrated_client.get("/api/auth/secrets", headers=tenant_1).status_code == 200
