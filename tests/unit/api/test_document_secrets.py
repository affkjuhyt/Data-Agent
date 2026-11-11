"""A document source's account goes to OpenBao under the organisation's sources/ subtree, where the
api may write and worker-agent reads it for the sync (documents/sync.py)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from libs.openbao import OpenBaoError
from services.api.kernel.problems import Problem
from services.api.modules.document import ports

pytestmark = [pytest.mark.unit]


class _Vault:
    def __init__(self, fail: bool = False) -> None:
        self.written: list[tuple[str, dict[str, Any]]] = []
        self.fail = fail

    def write_kv(self, path: str, data: dict[str, Any], *, mount: str = "secret") -> None:
        if self.fail:
            raise OpenBaoError("write secret: OpenBao answered 403")
        self.written.append((f"{mount}/{path}", data))


async def test_the_account_is_written_under_the_organisations_sources() -> None:
    org, source, vault = uuid.uuid4(), uuid.uuid4(), _Vault()
    store = ports.OpenBaoSecrets(vault)  # type: ignore[arg-type]
    values = {"user": "AKIA-DOC", "password": "s3cret"}
    ref = await store.write(org, f"document-source/{source}", values)
    assert ref == f"secret/tenants/{org}/sources/document-source/{source}"
    assert vault.written == [(ref, values)]


async def test_a_refused_write_is_unavailable_and_says_no_secret() -> None:
    store = ports.OpenBaoSecrets(_Vault(fail=True))  # type: ignore[arg-type]
    with pytest.raises(Problem) as raised:
        await store.write(uuid.uuid4(), f"document-source/{uuid.uuid4()}", {"password": "s3cret"})
    assert raised.value.status == 503 and "s3cret" not in str(raised.value)


async def test_a_name_that_walks_out_of_the_subtree_is_refused() -> None:
    store = ports.OpenBaoSecrets(_Vault())  # type: ignore[arg-type]
    for name in ("../providers/x", "a//b", "a/./b", ""):
        with pytest.raises(ValueError):
            await store.write(uuid.uuid4(), name, {"password": "x"})


def test_the_store_is_openbao_when_it_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ports.openbao, "vault", lambda: None)
    assert isinstance(ports.secret_store(), ports.UnconfiguredSecrets)
    monkeypatch.setattr(ports.openbao, "vault", lambda: _Vault())
    assert isinstance(ports.secret_store(), ports.OpenBaoSecrets)
