"""auth_api /internal/api-keys/verify against real rows.

A key issued through the API must verify as its tenant and scope role; a
revoked, rotated-away, expired or wrong key must not. The DB-free refusals
(gateway key, malformed key) are in tests/unit/auth_api/test_api_key_verify.py.
"""

from __future__ import annotations

import pytest

from .conftest import TENANT_1

pytestmark = pytest.mark.integration

_GATEWAY_KEY = "gw-test-key"


@pytest.fixture(autouse=True)
def _gateway_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATEWAY_KEY", _GATEWAY_KEY)
    monkeypatch.delenv("KB_GATEWAY_KEY", raising=False)


def _admin_headers() -> dict[str, str]:
    return {
        "X-Kong-Verified": "true",
        "X-Gateway-Key": _GATEWAY_KEY,
        "X-User-Id": "admin-1",
        "X-User-Roles": "platform-admin",
        "X-Tenant-Id": TENANT_1,
    }


def _issue(client, scope: str = "read_only", **extra: str) -> dict:
    resp = client.post(
        "/api/auth/api-keys", json={"name": "ci", "scope": scope, **extra}, headers=_admin_headers()
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _verify(client, key: str):
    return client.post(
        "/internal/api-keys/verify", json={"key": key}, headers={"X-Gateway-Key": _GATEWAY_KEY}
    )


def test_an_issued_key_verifies_as_its_tenant_and_scope_role(migrated_client) -> None:
    issued = _issue(migrated_client, scope="full_access")

    resp = _verify(migrated_client, issued["raw_key"])

    assert resp.status_code == 200
    assert resp.json() == {
        "key_id": issued["id"],
        "user_id": f"api-key:{issued['id']}",
        "user_name": "ci",
        "tenant_id": TENANT_1,
        "scope": "full_access",
        "roles": ["ai-engineer"],
    }


def test_verifying_records_last_used_at(migrated_client, db_conn) -> None:
    issued = _issue(migrated_client)

    assert _verify(migrated_client, issued["raw_key"]).status_code == 200

    with db_conn.cursor() as cur:
        cur.execute("SELECT last_used_at FROM api_keys WHERE id = %s", (issued["id"],))
        assert cur.fetchone()["last_used_at"] is not None


def test_a_key_differing_only_in_its_random_part_is_refused(migrated_client) -> None:
    raw = _issue(migrated_client)["raw_key"]
    tampered = raw[:-1] + ("A" if raw[-1] != "A" else "B")

    assert _verify(migrated_client, tampered).status_code == 401


def test_a_revoked_key_is_refused(migrated_client) -> None:
    issued = _issue(migrated_client)
    revoke = migrated_client.post(
        f"/api/auth/api-keys/{issued['id']}/revoke", headers=_admin_headers()
    )
    assert revoke.status_code == 200

    assert _verify(migrated_client, issued["raw_key"]).status_code == 401


def test_rotation_retires_the_old_key_and_the_new_one_verifies(migrated_client) -> None:
    issued = _issue(migrated_client)
    rotated = migrated_client.post(
        f"/api/auth/api-keys/{issued['id']}/rotate", headers=_admin_headers()
    ).json()

    assert _verify(migrated_client, issued["raw_key"]).status_code == 401
    assert _verify(migrated_client, rotated["raw_key"]).status_code == 200


def test_an_expired_key_is_refused(migrated_client) -> None:
    issued = _issue(migrated_client, expires_at="2020-01-01T00:00:00")

    assert _verify(migrated_client, issued["raw_key"]).status_code == 401
