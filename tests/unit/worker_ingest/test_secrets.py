"""A source's credentials, read from its organisation's subtree of OpenBao only (libs/openbao)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from libs.openbao import OpenBaoError
from services.worker_ingest import secrets

pytestmark = pytest.mark.unit

ORG = uuid.uuid4()


class _Vault:
    def __init__(self, data: dict[str, dict[str, Any]], fail: bool = False) -> None:
        self.data, self.fail = data, fail
        self.read: list[str] = []

    def read_kv(self, path: str, *, mount: str = "secret") -> dict[str, Any]:
        self.read.append(path)
        if self.fail:
            raise OpenBaoError("read secret: OpenBao answered 403")
        return self.data[path]


async def test_its_own_ref_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _Vault({f"tenants/{ORG}/sources/dw": {"username": "u", "password": "p"}})
    monkeypatch.setattr(secrets, "_vault", lambda: vault)
    creds = await secrets.Reader().read(ORG, f"secret/tenants/{ORG}/sources/dw")
    assert (creds.user, creds.password) == ("u", "p")


@pytest.mark.parametrize(
    "ref", [f"secret/tenants/{uuid.uuid4()}/sources/dw", f"secret/tenants/{ORG}/../x/dw"]
)
async def test_another_subtree_is_refused_before_any_call(
    monkeypatch: pytest.MonkeyPatch, ref: str
) -> None:
    vault = _Vault({})
    monkeypatch.setattr(secrets, "_vault", lambda: vault)
    with pytest.raises(secrets.SecretUnavailable):
        await secrets.Reader().read(ORG, ref)
    assert vault.read == []


async def test_a_refusal_is_said_as_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(secrets, "_vault", lambda: _Vault({}, fail=True))
    with pytest.raises(secrets.SecretUnavailable, match="403"):
        await secrets.Reader().read(ORG, f"secret/tenants/{ORG}/sources/dw")
