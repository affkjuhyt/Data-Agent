"""A source account is a path, or a username and password the API stores in OpenBao."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from libs.openbao import OpenBaoError
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


class _Store:
    """Stands in for the api's OpenBao (libs/openbao.OpenBao.write_kv)."""

    def __init__(self, fail: bool = False) -> None:
        self.written: list[tuple[str, dict[str, Any]]] = []
        self.fail = fail

    def write_kv(self, path: str, data: dict[str, Any], *, mount: str = "secret") -> None:
        if self.fail:
            raise OpenBaoError("write secret: OpenBao answered 403")
        self.written.append((f"{mount}/{path}", data))


def test_put_account_sends_the_password_and_returns_only_the_path() -> None:
    org, source = uuid.uuid4(), uuid.uuid4()
    store = _Store()
    ref = put_account(org, source, "explorer", "", writeback=False, vault=store)  # type: ignore[arg-type]
    prefix = f"secret/tenants/{org}/sources/{source}/account-"
    assert ref.startswith(prefix) and ref != prefix and "explorer" not in ref
    assert store.written == [(ref, {"user": "explorer", "password": ""})]


def test_each_write_uses_a_new_path() -> None:
    org, source, store = uuid.uuid4(), uuid.uuid4(), _Store()
    first = put_account(org, source, "explorer", "", writeback=False, vault=store)  # type: ignore[arg-type]
    second = put_account(org, source, "explorer", "", writeback=False, vault=store)  # type: ignore[arg-type]
    assert first != second


def test_a_refused_write_is_said_without_the_password() -> None:
    with pytest.raises(SecretStoreError) as caught:
        put_account(
            uuid.uuid4(),
            uuid.uuid4(),
            "explorer",
            "hunter2",
            writeback=False,
            vault=_Store(fail=True),  # type: ignore[arg-type]
        )
    assert "hunter2" not in str(caught.value)


def test_put_account_without_openbao_does_not_call_the_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.api.kernel import openbao

    monkeypatch.setattr(openbao, "vault", lambda: None)
    with pytest.raises(SecretStoreError):
        put_account(uuid.uuid4(), uuid.uuid4(), "explorer", "", writeback=False)
