"""Signed service-to-service identity assertions (backend/libs/shared/service_identity.py).

These pin the primitive that replaced kb_search's forged admin headers. The
security properties worth regression-testing are the ones whose absence would
be invisible in normal use: a wrong signature must be rejected, an expired one
must be rejected, and the signature must be checked BEFORE any payload field is
believed — a token can otherwise smuggle roles simply by being well-formed.
"""
from __future__ import annotations

import base64
import json
import time

import pytest

from libs.shared.service_identity import (
    ServiceAssertionError,
    mint_service_assertion,
    verify_service_assertion,
)

pytestmark = pytest.mark.unit

SECRET = "s3cret-shared-between-workflow_runtime-and-kb-backend"
TENANT = "a0000000-0000-0000-0000-000000000001"
USER = "11111111-1111-1111-1111-111111111111"
ALLOWED = ("workflow_runtime",)


def _mint(**overrides):
    kwargs = {
        "service": "workflow_runtime",
        "tenant_id": TENANT,
        "user_id": USER,
        "roles": ["business-operator"],
        "secret": SECRET,
    }
    kwargs.update(overrides)
    return mint_service_assertion(**kwargs)


def _tamper_payload(token: str, mutate) -> str:
    """Rewrite the payload, leaving the original signature attached — what an
    attacker who captured a token would try."""
    prefix, payload_b64, signature_b64 = token.split(".")
    padding = "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
    mutate(payload)
    reencoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).rstrip(b"=").decode()
    return f"{prefix}.{reencoded}.{signature_b64}"


class TestRoundTrip:
    def test_valid_token_round_trips_the_identity(self):
        payload = verify_service_assertion(_mint(), secret=SECRET, allowed_services=ALLOWED)

        assert payload["svc"] == "workflow_runtime"
        assert payload["tid"] == TENANT
        assert payload["uid"] == USER
        assert payload["roles"] == ["business-operator"]

    def test_empty_roles_survive_as_empty_not_absent(self):
        """kb-backend turns an empty role list into no permissions. If this
        round-tripped as a missing key instead, that fail-closed behaviour would
        depend on a .get() default somewhere else."""
        payload = verify_service_assertion(_mint(roles=[]), secret=SECRET, allowed_services=ALLOWED)

        assert payload["roles"] == []

    def test_blank_roles_are_dropped(self):
        payload = verify_service_assertion(
            _mint(roles=["", "  viewer", ""]), secret=SECRET, allowed_services=ALLOWED,
        )

        assert payload["roles"] == ["  viewer"]


class TestSignature:
    def test_wrong_secret_is_rejected(self):
        with pytest.raises(ServiceAssertionError, match="signature mismatch"):
            verify_service_assertion(_mint(), secret="not-the-secret", allowed_services=ALLOWED)

    def test_escalating_roles_in_the_payload_is_rejected(self):
        """The whole point: a captured assertion for a low-privilege user must
        not be editable into an admin one."""
        token = _tamper_payload(_mint(), lambda p: p.update(roles=["platform-admin"]))

        with pytest.raises(ServiceAssertionError, match="signature mismatch"):
            verify_service_assertion(token, secret=SECRET, allowed_services=ALLOWED)

    def test_switching_tenant_in_the_payload_is_rejected(self):
        token = _tamper_payload(_mint(), lambda p: p.update(tid="b0000000-0000-0000-0000-000000000002"))

        with pytest.raises(ServiceAssertionError, match="signature mismatch"):
            verify_service_assertion(token, secret=SECRET, allowed_services=ALLOWED)

    def test_extending_expiry_in_the_payload_is_rejected(self):
        token = _tamper_payload(_mint(), lambda p: p.update(exp=int(time.time()) + 10_000))

        with pytest.raises(ServiceAssertionError, match="signature mismatch"):
            verify_service_assertion(token, secret=SECRET, allowed_services=ALLOWED)

    def test_signature_is_checked_before_the_payload_is_read(self):
        """A tampered payload that is ALSO unparseable must fail on the
        signature, proving nothing unauthenticated was decoded first."""
        prefix, _payload_b64, signature_b64 = _mint().split(".")
        token = f"{prefix}.{base64.urlsafe_b64encode(b'not json').rstrip(b'=').decode()}.{signature_b64}"

        with pytest.raises(ServiceAssertionError, match="signature mismatch"):
            verify_service_assertion(token, secret=SECRET, allowed_services=ALLOWED)


class TestExpiry:
    def test_expired_token_is_rejected(self):
        token = _mint(ttl_s=-3600)

        with pytest.raises(ServiceAssertionError, match="expired"):
            verify_service_assertion(token, secret=SECRET, allowed_services=ALLOWED)

    def test_token_just_inside_the_skew_window_still_verifies(self):
        """Container clocks drift; a few seconds of skew must not 401 an agent
        turn."""
        payload = verify_service_assertion(_mint(ttl_s=-5), secret=SECRET, allowed_services=ALLOWED)

        assert payload["svc"] == "workflow_runtime"


class TestServiceAllowlist:
    def test_service_outside_the_allowlist_is_rejected(self):
        """A correctly-signed token still doesn't let just any service act as a
        user — holding the secret and being authorised are separate."""
        token = _mint(service="some-other-service")

        with pytest.raises(ServiceAssertionError, match="not permitted"):
            verify_service_assertion(token, secret=SECRET, allowed_services=ALLOWED)

    def test_empty_allowlist_rejects_everything(self):
        with pytest.raises(ServiceAssertionError, match="not permitted"):
            verify_service_assertion(_mint(), secret=SECRET, allowed_services=())


class TestMisconfiguration:
    def test_verifying_without_a_secret_rejects_rather_than_accepts(self):
        """The dangerous default. An unconfigured verifier must refuse, never
        wave everything through."""
        with pytest.raises(ServiceAssertionError, match="not configured"):
            verify_service_assertion(_mint(), secret="", allowed_services=ALLOWED)

    def test_minting_without_a_secret_raises(self):
        with pytest.raises(ServiceAssertionError, match="without a secret"):
            _mint(secret="")

    @pytest.mark.parametrize("token", ["", "garbage", "v1.only-two-parts", "v2.a.b", "a.b.c.d"])
    def test_malformed_tokens_are_rejected(self, token):
        with pytest.raises(ServiceAssertionError):
            verify_service_assertion(token, secret=SECRET, allowed_services=ALLOWED)
