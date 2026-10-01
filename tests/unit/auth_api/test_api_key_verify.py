"""The refusals of auth_api's /internal/api-keys/verify that happen before the
database, and the key-format helpers it relies on.

Kong's aeroflow-jwks plugin calls this endpoint for every request carrying an
`sk-` key. Issued keys were never checked anywhere before, so a key looked like
a credential while authenticating nothing. The router is mounted alone and
`get_session` is a tripwire; the lookups against real rows (revoked, expired,
last_used_at) are in tests/integration/auth_api/test_api_key_verify.py.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.auth_api.domain.api_keys import (
    KEY_SUFFIX_LENGTH,
    SCOPE_ROLES,
    VALID_SCOPES,
    api_key_matches,
    generate_api_key,
    hash_api_key,
    key_prefix_of,
)
from services.auth_api.routers import api_keys

pytestmark = pytest.mark.unit

_GATEWAY_KEY = "gw-test-key"


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def _tripwire() -> Any:
        calls.append("get_session")
        raise AssertionError("the request reached the database")

    monkeypatch.setattr(api_keys, "get_session", _tripwire)
    return calls


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(api_keys.router)
    return TestClient(app)


def _verify(client: TestClient, key: str, gateway_key: str | None = _GATEWAY_KEY) -> Any:
    headers = {"X-Gateway-Key": gateway_key} if gateway_key is not None else {}
    return client.post("/internal/api-keys/verify", json={"key": key}, headers=headers)


class TestKeyFormat:
    def test_the_prefix_of_a_generated_key_is_the_stored_prefix(self) -> None:
        for scope in VALID_SCOPES:
            raw, prefix = generate_api_key(scope)
            assert len(raw) == len(prefix) + KEY_SUFFIX_LENGTH
            assert key_prefix_of(raw) == prefix

    @pytest.mark.parametrize(
        "raw", ["", "sk-short", "pk-ro-abcd" + "x" * KEY_SUFFIX_LENGTH, "eyJhbGciOi.x.y"]
    )
    def test_a_value_that_cannot_be_a_key_has_no_prefix(self, raw: str) -> None:
        assert key_prefix_of(raw) is None

    def test_matches_only_the_key_that_was_hashed(self) -> None:
        raw, _ = generate_api_key("read_only")
        other, _ = generate_api_key("read_only")
        key_hash = hash_api_key(raw)
        assert api_key_matches(raw, key_hash)
        assert not api_key_matches(other, key_hash)

    def test_every_scope_acts_as_a_role(self) -> None:
        assert set(SCOPE_ROLES) == VALID_SCOPES


class TestGatewayKey:
    def test_without_a_configured_gateway_key_api_keys_are_off(
        self, client: TestClient, opened: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GATEWAY_KEY", raising=False)
        monkeypatch.delenv("KB_GATEWAY_KEY", raising=False)
        raw, _ = generate_api_key("full_access")

        resp = _verify(client, raw, gateway_key="")

        assert resp.status_code == 401
        assert opened == []

    @pytest.mark.parametrize("presented", [None, "", "wrong-key"])
    def test_a_caller_without_the_gateway_key_is_refused(
        self,
        client: TestClient,
        opened: list[str],
        monkeypatch: pytest.MonkeyPatch,
        presented: str | None,
    ) -> None:
        monkeypatch.setenv("GATEWAY_KEY", _GATEWAY_KEY)
        raw, _ = generate_api_key("full_access")

        resp = _verify(client, raw, gateway_key=presented)

        assert resp.status_code == 401
        assert opened == []


def test_a_malformed_key_is_refused_without_a_lookup(
    client: TestClient, opened: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GATEWAY_KEY", _GATEWAY_KEY)

    resp = _verify(client, "not-a-key")

    assert resp.status_code == 401
    assert opened == []
