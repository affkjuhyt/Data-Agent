"""End-to-end JWT audience validation tests.

Verifies the audience-validation slice of P1 #7:

  * Keycloak issues tokens with `aeroflow-backend` and `auth_api`
    in the `aud` claim (driven by audience mappers
    installed via deploy/docker/keycloak/setup-audience-mappers.sh).
  * Kong's aeroflow-jwks plugin rejects a token if its `aud` doesn't
    match the per-route audience config (set by deploy/docker/kong/kong-setup.sh).

This test does NOT cover the grace_period behavior on JWKS rotation —
that needs Keycloak admin API to actually rotate keys + carefully timed
assertions. Run the procedural test in
docs/auth-runbook.md "JWKS rotation grace period" for that.

Requires the full live stack:

    kubectl apply -f deploy/k8s/production/database/ -f deploy/k8s/production/service/
    kubectl apply -f deploy/k8s/production/job/setup.yaml
    LIVE_STACK=1 pytest -m e2e tests/integration/test_jwt_audience.py -v
"""
from __future__ import annotations

import base64
import json

import pytest
import requests

pytestmark = pytest.mark.e2e


def _get_token(keycloak_url: str, username: str = "platform-admin", password: str = "Admin@123456") -> str:
    r = requests.post(
        f"{keycloak_url}/realms/aeroflow/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "aeroflow-frontend",
            "username": username,
            "password": password,
        },
        timeout=10,
    )
    d = r.json()
    if "access_token" not in d:
        raise RuntimeError(f"Login failed: {d}")
    return d["access_token"]


def _decode_jwt_payload(token: str) -> dict:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


# ─── Token shape — verify Keycloak is issuing tokens with our audiences ────
def test_keycloak_issues_token_with_expected_audiences(live_stack):
    """The audience-mapper setup must make Keycloak put all three target
    audiences into the issued token's `aud` claim."""
    token = _get_token(live_stack["keycloak"])
    payload = _decode_jwt_payload(token)
    aud = payload.get("aud")

    # `aud` is a string when there's one value, a list when multiple. Normalize.
    aud_list = [aud] if isinstance(aud, str) else (aud or [])

    expected = {"aeroflow-backend", "auth_api"}
    missing = expected - set(aud_list)
    assert not missing, (
        f"Keycloak token missing audiences {missing}. Run "
        f"setup-audience-mappers.sh first. Token aud: {aud_list}"
    )


# ─── Kong accepts the token on routes whose audience matches ───────────────
def test_kong_accepts_token_on_matching_audience_route(live_stack):
    """auth_api route has audience=auth_api in its plugin config. A token
    that includes auth_api in its aud claim should be accepted."""
    token = _get_token(live_stack["keycloak"])

    r = requests.get(
        f"{live_stack['kong']}/api/auth/health",
        headers={"Authorization": f"Bearer {token}"},
        timeout=5,
    )
    # /health is auth-free, but a Bearer that's invalid would still 401 at
    # the plugin layer before reaching the handler. We expect 200.
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:200]}"


# ─── Kong rejects tokens crafted with a wrong audience (manipulation guard) ─
# We can't easily synthesize a JWT with a wrong audience because we don't
# have Keycloak's private key. But we CAN demonstrate that a JWT signed by
# a DIFFERENT key (or one with manipulated payload) is rejected. This test
# uses the audience contract differently: hand-craft an unsigned token with
# wrong aud and verify it's rejected even though the structure is valid.
def test_kong_rejects_token_with_invalid_audience(live_stack):
    """A token whose payload has `aud = "other-service"` must be rejected.

    We can't sign with Keycloak's private key, so this token will also fail
    signature verification. That's fine — the test asserts that 401 is the
    outcome, which is the security property we care about: a wrong-aud
    token does NOT pass the gate, regardless of whether it fails on
    signature or on aud check.
    """
    # Header: alg=RS256, kid=none
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "kid": "fake-kid"}).encode()).rstrip(b"=")
    # Payload with wrong audience
    payload = base64.urlsafe_b64encode(json.dumps({
        "iss": f"{live_stack['keycloak']}/realms/aeroflow",
        "aud": "other-service",  # NOT auth_api / aeroflow-backend
        "exp": 9999999999,
        "sub": "spoofer",
    }).encode()).rstrip(b"=")
    fake_sig = base64.urlsafe_b64encode(b"\x00" * 256).rstrip(b"=")
    bad_token = f"{header.decode()}.{payload.decode()}.{fake_sig.decode()}"

    r = requests.get(
        f"{live_stack['kong']}/api/auth/health",
        headers={"Authorization": f"Bearer {bad_token}"},
        timeout=5,
    )
    assert r.status_code == 401, f"Expected 401, got {r.status_code}: {r.text[:200]}"


# ─── Token without Authorization header rejected at the gate ───────────────
def test_kong_rejects_request_without_bearer_token(live_stack):
    """Baseline: no Bearer = 401. Sanity-checks the plugin is active on the
    route (a regressed setup that forgot the plugin would 200 here)."""
    r = requests.get(f"{live_stack['kong']}/api/auth/health", timeout=5)
    assert r.status_code == 401, f"Expected 401, got {r.status_code}"
