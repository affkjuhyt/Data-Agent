"""The api reaches OpenBao with its own AppRole, never the root token (kernel/openbao.py).

The login itself is libs/openbao's (tests/unit/api/test_openbao_client.py); here, that the api
keeps one client for the process and builds its adapters on it."""

from __future__ import annotations

import pytest

from services.api.kernel import openbao
from services.api.modules.exec import transit

pytestmark = [pytest.mark.unit]


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    openbao.forget()
    for name in ("OPENBAO_ADDR", "OPENBAO_TOKEN", "OPENBAO_ROLE_ID", "OPENBAO_SECRET_ID"):
        monkeypatch.delenv(name, raising=False)


def test_without_openbao_there_is_no_client() -> None:
    assert openbao.vault() is None
    assert transit.transit_from_env() is None


def test_an_approle_gives_one_client_for_the_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENBAO_ADDR", "http://openbao:8200")
    monkeypatch.setenv("OPENBAO_ROLE_ID", "role")
    monkeypatch.setenv("OPENBAO_SECRET_ID", "s3cr3t")
    first = openbao.vault()
    assert first is not None and openbao.vault() is first
    keys = transit.transit_from_env()
    assert keys is not None and keys._vault is first
    openbao.forget()
    assert openbao.vault() is not first


def test_an_address_alone_is_not_a_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENBAO_ADDR", "http://openbao:8200")
    assert openbao.vault() is None
