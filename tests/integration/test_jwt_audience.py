"""End-to-end JWT audience validation at the gateway.

  * Keycloak issues tokens with `dataagent-api` in the `aud` claim (the audience mappers
    installed by deploy/docker/keycloak/setup-audience-mappers.sh).
  * Kong's aeroflow-jwks plugin on the api service checks `aud` against `dataagent-api`
    (deploy/docker/kong/kong-setup.sh) and rejects anything it cannot verify.

The grace_period behaviour on JWKS rotation needs the Keycloak admin API to rotate keys and
timed assertions; it is not covered here.

Runs against the deployed cluster: `make e2e E2E_TESTS=tests/integration/test_jwt_audience.py`.
"""

from __future__ import annotations

import base64
import json
import os

import pytest
import requests

from tests.conftest import live_token

pytestmark = pytest.mark.e2e

DATA_SPACE = os.environ.get("E2E_DATA_SPACE", "DS-DEMO")
# Any /v1 route behind the api's plugin; the gate runs before the api sees the request.
GATED = "/v1/datasets"


def _decode_jwt_payload(token: str) -> dict:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def test_keycloak_issues_token_with_the_api_audience(live_stack):
    token = live_token(live_stack["keycloak"], "engineer")
    aud = _decode_jwt_payload(token).get("aud")
    # `aud` is a string when there's one value, a list when multiple.
    aud_list = [aud] if isinstance(aud, str) else (aud or [])
    assert "dataagent-api" in aud_list, (
        f"token aud {aud_list} lacks dataagent-api; run setup-audience-mappers.sh"
    )


def test_kong_accepts_a_token_for_the_api(live_stack):
    token = live_token(live_stack["keycloak"], "engineer")
    r = requests.get(
        f"{live_stack['kong']}{GATED}",
        headers={"Authorization": f"Bearer {token}", "X-Data-Space": DATA_SPACE},
        timeout=15,
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:200]}"


def test_kong_rejects_token_with_invalid_audience(live_stack):
    """A token whose payload has `aud = "other-service"` must be rejected.

    It cannot be signed with Keycloak's key, so it also fails signature verification. The
    property asserted is that a wrong-aud token does not pass the gate, whichever check
    stops it.
    """
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "RS256", "kid": "fake-kid"}).encode()
    ).rstrip(b"=")
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {
                "iss": f"{live_stack['keycloak']}/realms/aeroflow",
                "aud": "other-service",
                "exp": 9999999999,
                "sub": "spoofer",
            }
        ).encode()
    ).rstrip(b"=")
    fake_sig = base64.urlsafe_b64encode(b"\x00" * 256).rstrip(b"=")
    bad_token = f"{header.decode()}.{payload.decode()}.{fake_sig.decode()}"

    r = requests.get(
        f"{live_stack['kong']}{GATED}",
        headers={"Authorization": f"Bearer {bad_token}"},
        timeout=5,
    )
    assert r.status_code == 401, f"Expected 401, got {r.status_code}: {r.text[:200]}"


def test_kong_rejects_request_without_bearer_token(live_stack):
    """A regressed setup that forgot the plugin would answer here."""
    r = requests.get(f"{live_stack['kong']}{GATED}", timeout=5)
    assert r.status_code == 401, f"Expected 401, got {r.status_code}"


def test_kong_rejects_a_legacy_api_key(live_stack):
    """`sk-` keys were verified by the removed auth_api; V3 clients use client-credentials
    tokens (ADR-30), so a key is just a malformed bearer."""
    r = requests.get(
        f"{live_stack['kong']}{GATED}",
        headers={"Authorization": "Bearer sk-legacy"},
        timeout=5,
    )
    assert r.status_code == 401, f"Expected 401, got {r.status_code}"
