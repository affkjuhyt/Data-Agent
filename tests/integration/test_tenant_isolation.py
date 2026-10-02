"""Multi-tenant isolation regression suite.

Promoted from deploy/docker/scripts/test_tenant_isolation.py. Each top-level group is
now an individual test so failures are visible per case in CI rather than
folded into one boolean.

Requires a live stack:
    kubectl apply -f deploy/k8s/production/database/ -f deploy/k8s/production/service/
    LIVE_STACK=1 pytest -m e2e tests/integration/test_tenant_isolation.py
"""
from __future__ import annotations

import pytest
import requests

from tests.conftest import live_token

pytestmark = pytest.mark.e2e


# ─── Helpers ───────────────────────────────────────────────────────────────
def _api(kong_url: str, token: str, method: str, path: str, **kw):
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    return getattr(requests, method)(f"{kong_url}/api/auth{path}", headers=h, **kw)


# ─── Fixtures (session-scoped — login once per run) ────────────────────────
@pytest.fixture(scope="module")
def t1_token(live_stack):
    return live_token(live_stack["keycloak"], "admin")


@pytest.fixture(scope="module")
def t2_token(live_stack):
    return live_token(live_stack["keycloak"], "tenant2_admin")


@pytest.fixture(scope="module")
def kong(live_stack):
    return live_stack["kong"]


@pytest.fixture(scope="module")
def t1_secrets(kong, t1_token):
    return _api(kong, t1_token, "get", "/secrets").json()


@pytest.fixture(scope="module")
def t2_secrets(kong, t2_token):
    return _api(kong, t2_token, "get", "/secrets").json()


@pytest.fixture(scope="module")
def t1_keys(kong, t1_token):
    return _api(kong, t1_token, "get", "/api-keys").json()


@pytest.fixture(scope="module")
def t2_keys(kong, t2_token):
    return _api(kong, t2_token, "get", "/api-keys").json()


@pytest.fixture(scope="module")
def t1_ips(kong, t1_token):
    return _api(kong, t1_token, "get", "/ip-allowlists").json()


@pytest.fixture(scope="module")
def t2_ips(kong, t2_token):
    return _api(kong, t2_token, "get", "/ip-allowlists").json()


# ─── 1. Secrets list isolation ─────────────────────────────────────────────
def test_secret_ids_do_not_overlap(t1_secrets, t2_secrets):
    overlap = {s["id"] for s in t1_secrets} & {s["id"] for s in t2_secrets}
    assert not overlap, f"Cross-tenant secret ID overlap: {overlap}"


def test_t1_list_contains_no_helios_keys(t1_secrets):
    helios = [s["key_name"] for s in t1_secrets if "HELIOS" in s["key_name"]]
    assert not helios, f"T1 sees Helios keys: {helios}"


def test_t2_list_contains_only_helios_keys(t2_secrets):
    non_helios = [s["key_name"] for s in t2_secrets if "HELIOS" not in s["key_name"]]
    assert not non_helios, f"T2 sees non-Helios keys: {non_helios}"


# ─── 2. Cross-tenant reveal by UUID ────────────────────────────────────────
def test_t1_cannot_reveal_t2_secret(kong, t1_token, t2_secrets):
    t2_kv = next(
        (s for s in t2_secrets if s["key_type"] == "BEARER_TOKEN" and s["is_active"]),
        None,
    )
    if not t2_kv:
        pytest.skip("No T2 BEARER_TOKEN seeded")
    r = _api(kong, t1_token, "get", f"/secrets/{t2_kv['id']}/reveal")
    assert r.status_code == 404, f"Expected 404, got {r.status_code}"


def test_t2_cannot_reveal_t1_secret(kong, t2_token, t1_secrets):
    t1_kv = next(
        (s for s in t1_secrets if s["key_type"] == "BEARER_TOKEN" and s["is_active"]),
        None,
    )
    if not t1_kv:
        pytest.skip("No T1 BEARER_TOKEN seeded")
    r = _api(kong, t2_token, "get", f"/secrets/{t1_kv['id']}/reveal")
    assert r.status_code == 404, f"Expected 404, got {r.status_code}"


# ─── 3. Cross-tenant Transit sign ──────────────────────────────────────────
def test_t1_cannot_sign_with_t2_key(kong, t1_token, t2_secrets):
    t2_sign = next(
        (s for s in t2_secrets if s["key_type"] == "SIGNING_KEY" and s["is_active"]),
        None,
    )
    if not t2_sign:
        pytest.skip("No T2 SIGNING_KEY seeded")
    r = _api(kong, t1_token, "post", f"/secrets/{t2_sign['id']}/sign", json={"data": "aGVsbG8="})
    assert r.status_code == 404


def test_t2_cannot_sign_with_t1_key(kong, t2_token, t1_secrets):
    t1_sign = next(
        (s for s in t1_secrets if s["key_type"] == "SIGNING_KEY" and s["is_active"]),
        None,
    )
    if not t1_sign:
        pytest.skip("No T1 SIGNING_KEY seeded")
    r = _api(kong, t2_token, "post", f"/secrets/{t1_sign['id']}/sign", json={"data": "aGVsbG8="})
    assert r.status_code == 404


# ─── 4. Cross-tenant delete ────────────────────────────────────────────────
def test_t1_cannot_delete_t2_secret(kong, t1_token, t2_secrets):
    t2_kv = next(
        (s for s in t2_secrets if s["key_type"] == "BEARER_TOKEN" and s["is_active"]),
        None,
    )
    if not t2_kv:
        pytest.skip("No T2 BEARER_TOKEN seeded")
    r = _api(kong, t1_token, "delete", f"/secrets/{t2_kv['id']}")
    assert r.status_code == 404


# ─── 5. API key isolation ──────────────────────────────────────────────────
def test_api_key_ids_do_not_overlap(t1_keys, t2_keys):
    overlap = {k["id"] for k in t1_keys} & {k["id"] for k in t2_keys}
    assert not overlap, f"Cross-tenant API key overlap: {overlap}"


def test_t1_cannot_revoke_t2_key(kong, t1_token, t2_keys):
    if not t2_keys:
        pytest.skip("No T2 API keys seeded")
    r = _api(kong, t1_token, "post", f"/api-keys/{t2_keys[0]['id']}/revoke")
    assert r.status_code == 404


def test_t2_cannot_revoke_t1_key(kong, t2_token, t1_keys):
    if not t1_keys:
        pytest.skip("No T1 API keys seeded")
    r = _api(kong, t2_token, "post", f"/api-keys/{t1_keys[0]['id']}/revoke")
    assert r.status_code == 404


def test_t2_cannot_rotate_t1_key(kong, t2_token, t1_keys):
    if not t1_keys:
        pytest.skip("No T1 API keys seeded")
    r = _api(kong, t2_token, "post", f"/api-keys/{t1_keys[0]['id']}/rotate")
    assert r.status_code == 404


# ─── 6. IP allowlist isolation ─────────────────────────────────────────────
def test_ip_rule_ids_do_not_overlap(t1_ips, t2_ips):
    overlap = {r["id"] for r in t1_ips} & {r["id"] for r in t2_ips}
    assert not overlap, f"Cross-tenant IP rule overlap: {overlap}"


def test_t1_cannot_toggle_t2_ip_rule(kong, t1_token, t2_ips):
    if not t2_ips:
        pytest.skip("No T2 IP rules seeded")
    r = _api(kong, t1_token, "patch", f"/ip-allowlists/{t2_ips[0]['id']}/toggle")
    assert r.status_code == 404


def test_t1_cannot_delete_t2_ip_rule(kong, t1_token, t2_ips):
    if not t2_ips:
        pytest.skip("No T2 IP rules seeded")
    r = _api(kong, t1_token, "delete", f"/ip-allowlists/{t2_ips[0]['id']}")
    assert r.status_code == 404


def test_t2_cannot_toggle_t1_ip_rule(kong, t2_token, t1_ips):
    if not t1_ips:
        pytest.skip("No T1 IP rules seeded")
    r = _api(kong, t2_token, "patch", f"/ip-allowlists/{t1_ips[0]['id']}/toggle")
    assert r.status_code == 404


# ─── 7. HSM / Transit key isolation ────────────────────────────────────────
def test_transit_keys_do_not_overlap(kong, t1_token, t2_token):
    t1 = {k["name"] for k in _api(kong, t1_token, "get", "/secrets/hsm/status").json().get("transit_keys", [])}
    t2 = {k["name"] for k in _api(kong, t2_token, "get", "/secrets/hsm/status").json().get("transit_keys", [])}
    overlap = t1 & t2
    assert not overlap, f"Transit key overlap: {overlap}"


def test_t1_hsm_status_excludes_helios_transit_keys(kong, t1_token):
    keys = _api(kong, t1_token, "get", "/secrets/hsm/status").json().get("transit_keys", [])
    helios = [k["name"] for k in keys if k["name"].startswith("b0000000")]
    assert not helios, f"T1 HSM exposes Helios keys: {helios}"


def test_t2_hsm_status_excludes_aeroflow_transit_keys(kong, t2_token):
    keys = _api(kong, t2_token, "get", "/secrets/hsm/status").json().get("transit_keys", [])
    aero = [k["name"] for k in keys if k["name"].startswith("a0000000")]
    assert not aero, f"T2 HSM exposes AeroFlow keys: {aero}"


# ─── 8. Audit log isolation ────────────────────────────────────────────────
def test_t1_audit_log_excludes_helios_entries(kong, t1_token):
    audit = _api(kong, t1_token, "get", "/secrets/audit-log").json()
    helios = [e["key_name"] for e in audit if "HELIOS" in e.get("key_name", "")]
    assert not helios, f"T1 audit log exposes Helios entries: {helios}"


def test_t2_audit_log_excludes_aeroflow_entries(kong, t2_token):
    audit = _api(kong, t2_token, "get", "/secrets/audit-log").json()
    aero = [
        e["key_name"]
        for e in audit
        if e.get("key_name") and "HELIOS" not in e["key_name"]
    ]
    assert not aero, f"T2 audit log exposes AeroFlow entries: {aero[:5]}"


# ─── 9. Positive: tenant CAN access own data ───────────────────────────────
def test_t2_can_reveal_own_secret(kong, t2_token, t2_secrets):
    t2_kv = next(
        (s for s in t2_secrets if s["key_type"] == "BEARER_TOKEN" and s["is_active"]),
        None,
    )
    if not t2_kv:
        pytest.skip("No T2 BEARER_TOKEN seeded")
    r = _api(kong, t2_token, "get", f"/secrets/{t2_kv['id']}/reveal")
    assert r.status_code == 200, f"T2 blocked from own data: {r.status_code} {r.text[:80]}"


def test_t2_can_sign_with_own_key(kong, t2_token, t2_secrets):
    t2_sign = next(
        (s for s in t2_secrets if s["key_type"] == "SIGNING_KEY" and s["is_active"]),
        None,
    )
    if not t2_sign:
        pytest.skip("No T2 SIGNING_KEY seeded")
    r = _api(kong, t2_token, "post", f"/secrets/{t2_sign['id']}/sign", json={"data": "aGVsbG8gd29ybGQ="})
    assert r.status_code == 200, f"T2 blocked from own signing key: {r.status_code} {r.text[:80]}"
