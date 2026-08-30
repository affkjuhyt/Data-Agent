"""Smoke tests for the auth_api FastAPI service.

Scope: hit the routes through TestClient with a real Postgres container.
OpenBao is NOT spun up — tests deliberately stop at validation/auth before
any vault call (the validation paths are themselves a major source of bugs
and have zero coverage today).

These are the SHAPE of tests the team should write next. They are not
exhaustive — together with the e2e suite they form the floor, not the
ceiling. Each test should be readable in 30 seconds and fail with a
specific message.

Run:
    docker compose -f docker/docker-compose.test.yml up -d   # optional
    pytest -m integration tests/integration/test_auth_api_smoke.py -v
"""
from __future__ import annotations

import psycopg2
import psycopg2.extras
import pytest

pytestmark = pytest.mark.integration


# ─── 1. Health ─────────────────────────────────────────────────────────────
def test_health_returns_ok(client):
    """App boots and /health responds without auth headers."""
    r = client.get("/api/auth/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ─── 2. Kong-verified gate ─────────────────────────────────────────────────
@pytest.mark.parametrize(
    "kong_verified",
    ["", "false", "True", "1"],  # only literal "true" should pass
    ids=["missing", "explicit-false", "wrong-case", "numeric-truthy"],
)
def test_request_without_kong_verified_true_returns_401(client, headers_factory, kong_verified):
    """A protected route MUST refuse anything but `X-Kong-Verified: true`.

    Defends against the case where Kong is bypassed (e.g. service called
    directly via Docker network) — the backend itself must not trust headers
    until Kong has signed off. Wrong-case + truthy values matter because a
    naive `if header:` check would accept them; the code MUST do an exact
    string compare.
    """
    h = headers_factory(kong_verified=kong_verified)
    r = client.get("/api/auth/api-keys", headers=h)
    assert r.status_code == 401, r.text
    assert "Kong" in r.json()["detail"]


# ─── 3. Role gate ──────────────────────────────────────────────────────────
def test_insufficient_role_returns_403(client, headers_factory):
    """A user with no platform-admin role must be rejected.

    Today require_role() is the only authorization layer above Kong's JWT
    check. If this regresses, any authenticated user could enumerate keys.
    """
    h = headers_factory(roles="viewer,kb-editor")  # neither is platform-admin
    r = client.get("/api/auth/api-keys", headers=h)
    assert r.status_code == 403
    assert "platform-admin" in r.json()["detail"]


# ─── 4. Application-layer tenant scoping (the "poor man's RLS" check) ──────
# Until Postgres RLS lands (see staff review P0 #2), tenant isolation is
# entirely on the WHERE-clause in the route. This test pins that behavior so
# a refactor that drops the WHERE will fail loudly here, not silently in prod.
def test_list_api_keys_scopes_to_caller_tenant(client, headers_factory, auth_api_db):
    """Insert keys for two tenants; verify GET only returns the caller's."""
    # Match the UUIDs used by headers_factory() default + tenant= override
    TENANT_1 = "11111111-1111-1111-1111-111111111111"
    TENANT_2 = "22222222-2222-2222-2222-222222222222"

    conn = psycopg2.connect(auth_api_db["dsn"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO api_keys (tenant_id, created_by, name, key_hash, key_prefix, scope) "
                "VALUES (%s, 't1-admin', 'aero-prod', 'hash1', 'sk-prod-aero', 'full_access'), "
                "       (%s, 't2-admin', 'helios-prod', 'hash2', 'sk-prod-helio', 'full_access')",
                (TENANT_1, TENANT_2),
            )
    finally:
        conn.close()

    # Caller is T1 — must see exactly one key, the T1 one.
    r = client.get("/api/auth/api-keys", headers=headers_factory(tenant=TENANT_1))
    assert r.status_code == 200, r.text
    keys = r.json()
    assert len(keys) == 1, f"T1 sees {len(keys)} keys, expected 1"
    assert keys[0]["name"] == "aero-prod"
    assert keys[0]["key_prefix"] == "sk-prod-aero"

    # And T2 sees only T2's.
    r2 = client.get("/api/auth/api-keys", headers=headers_factory(tenant=TENANT_2))
    assert r2.status_code == 200
    keys2 = r2.json()
    assert len(keys2) == 1
    assert keys2[0]["name"] == "helios-prod"


# ─── 5. Create api-key happy path ──────────────────────────────────────────
def test_create_api_key_returns_raw_key_and_persists(client, headers_factory, auth_api_db):
    """POST returns the raw key once (only chance to see it) and writes a row."""
    payload = {"name": "ci-token", "scope": "read_only"}
    r = client.post("/api/auth/api-keys", headers=headers_factory(), json=payload)
    assert r.status_code == 201, r.text
    body = r.json()

    # Contract — these are what UI/CLI consumes; breaking these is breaking integrations.
    assert body["name"] == "ci-token"
    assert body["scope"] == "read_only"
    assert body["raw_key"].startswith("sk-ro-"), f"prefix mismatch: {body['raw_key']!r}"
    assert body["key_prefix"].startswith("sk-ro-")
    assert body["revoked_at"] is None
    assert body["id"]  # UUID set

    # Row in DB has hashed key, not raw.
    conn = psycopg2.connect(auth_api_db["dsn"])
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT key_hash, scope FROM api_keys WHERE id = %s", (body["id"],))
            row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None, "key was not persisted"
    assert row["scope"] == "read_only"
    assert row["key_hash"] != body["raw_key"], "raw key leaked into key_hash column"
    assert row["key_hash"].startswith("$2"), "expected bcrypt hash"


# ─── 6. Create api-key validation ──────────────────────────────────────────
def test_create_api_key_with_invalid_scope_returns_422(client, headers_factory):
    """Scope must be one of full_access / read_only / admin_platform.

    Free-form scope strings becoming valid would let a caller mint a key with
    an unknown scope that downstream services may misinterpret as elevated.
    """
    payload = {"name": "bad-scope", "scope": "banana"}
    r = client.post("/api/auth/api-keys", headers=headers_factory(), json=payload)
    assert r.status_code == 422
    assert "Invalid scope" in r.json()["detail"]


# ─── 7. Create secret validation (rejected before any vault call) ──────────
@pytest.mark.parametrize(
    "payload, expected_detail",
    [
        ({"key_name": "x", "key_type": "BANANA_KEY"}, "Invalid key_type"),
        ({"key_name": "x", "key_type": "BEARER_TOKEN"}, "value is required"),
    ],
    ids=["invalid-key-type", "kv-type-missing-value"],
)
def test_create_secret_validation_rejects_before_vault(client, headers_factory, payload, expected_detail):
    """Validation MUST fire before auth_core.get_vault() is called.

    If validation drifts below the vault call, every bad request hits OpenBao
    and a typo could create a malformed Transit key that's hard to clean up.
    OpenBao is NOT mocked in these tests — if the code reached vault, the
    test would error trying to connect, not assert 422 cleanly.
    """
    r = client.post("/api/auth/secrets", headers=headers_factory(), json=payload)
    assert r.status_code == 422
    assert expected_detail in r.json()["detail"]
