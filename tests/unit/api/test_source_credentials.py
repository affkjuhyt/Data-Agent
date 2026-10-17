"""A source account is a path, or a username and password the API stores in OpenBao."""

from __future__ import annotations

import json
import uuid

import pytest
from pydantic import ValidationError

from services.api.modules.catalog.schemas import SecretReference
from services.api.modules.catalog.vault import SecretStoreError, put_account

pytestmark = [pytest.mark.unit]


def test_an_empty_password_is_an_account() -> None:
    cred = SecretReference(username="explorer", password="")
    assert cred.secret_ref is None
    assert (cred.username, cred.password) == ("explorer", "")


def test_omitted_password_is_empty() -> None:
    cred = SecretReference(username="explorer")
    assert cred.password == ""


def test_a_path_and_a_password_together_are_refused() -> None:
    with pytest.raises(ValidationError):
        SecretReference(secret_ref="secret/tenants/org/core", password="s3cret")


def test_a_path_alone_is_unchanged() -> None:
    cred = SecretReference(secret_ref="secret/tenants/org/core")
    assert cred.username is None and cred.password is None


def test_put_account_sends_the_password_and_returns_only_the_path() -> None:
    org, source = uuid.uuid4(), uuid.uuid4()
    seen: dict[str, object] = {}

    class _Response:
        status = 200

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    def opener(request: object, timeout: float) -> _Response:
        seen["url"] = request.full_url  # type: ignore[attr-defined]
        seen["body"] = json.loads(request.data.decode())  # type: ignore[attr-defined]
        seen["timeout"] = timeout
        header = request.get_header("X-vault-token")  # type: ignore[attr-defined]
        seen["token"] = header
        return _Response()

    import services.api.modules.catalog.vault as vault

    original = vault.urllib.request.urlopen
    vault.urllib.request.urlopen = opener  # type: ignore[assignment]
    try:
        ref = put_account(
            org, source, "explorer", "", writeback=False, addr="http://openbao:8120", token="tok"
        )
    finally:
        vault.urllib.request.urlopen = original  # type: ignore[assignment]

    prefix = f"secret/tenants/{org}/sources/{source}/account-"
    assert ref.startswith(prefix) and ref != prefix
    assert "explorer" not in ref and seen["token"] == "tok"
    assert seen["body"] == {"data": {"user": "explorer", "password": ""}}
    assert seen["url"] == f"http://openbao:8120/v1/secret/data/{ref.removeprefix('secret/')}"


def test_each_write_uses_a_new_path() -> None:
    org, source = uuid.uuid4(), uuid.uuid4()

    class _Response:
        status = 200

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    import services.api.modules.catalog.vault as vault

    original = vault.urllib.request.urlopen
    vault.urllib.request.urlopen = lambda *_a, **_k: _Response()  # type: ignore[assignment]
    try:
        first = put_account(
            org, source, "explorer", "", writeback=False, addr="http://v", token="t"
        )
        second = put_account(
            org, source, "explorer", "", writeback=False, addr="http://v", token="t"
        )
    finally:
        vault.urllib.request.urlopen = original  # type: ignore[assignment]
    assert first != second


def test_put_account_without_a_token_does_not_call_the_store() -> None:
    with pytest.raises(SecretStoreError):
        put_account(uuid.uuid4(), uuid.uuid4(), "explorer", "", writeback=False, addr="", token="")
