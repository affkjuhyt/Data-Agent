"""The api reaches OpenBao with its own AppRole, never the root token (kernel/openbao.py)."""

from __future__ import annotations

import io
import json
from typing import Any

import pytest

from services.api.kernel import openbao
from services.api.modules.exec import transit

pytestmark = [pytest.mark.unit]


class _Response(io.BytesIO):
    status = 200

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    openbao.forget()
    for name in ("OPENBAO_ADDR", "OPENBAO_TOKEN", "OPENBAO_ROLE_ID", "OPENBAO_SECRET_ID"):
        monkeypatch.delenv(name, raising=False)


def _approle(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    monkeypatch.setenv("OPENBAO_ADDR", "http://openbao:8200")
    monkeypatch.setenv("OPENBAO_ROLE_ID", "role")
    monkeypatch.setenv("OPENBAO_SECRET_ID", "secret")
    logins: list[dict[str, Any]] = []

    def urlopen(request: Any, timeout: float = 0) -> _Response:
        assert request.full_url == "http://openbao:8200/v1/auth/approle/login"
        logins.append(json.loads(request.data))
        body = {"auth": {"client_token": f"t{len(logins)}", "lease_duration": 3600}}
        return _Response(json.dumps(body).encode())

    monkeypatch.setattr(openbao.urllib.request, "urlopen", urlopen)
    return logins


def test_logs_in_with_its_approle_once_and_reuses_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logins = _approle(monkeypatch)
    # The static token is never used once an AppRole is configured.
    monkeypatch.setenv("OPENBAO_TOKEN", "root")
    assert openbao.token() == "t1"
    assert openbao.token() == "t1"
    assert logins == [{"role_id": "role", "secret_id": "secret"}]


def test_a_lapsed_login_logs_in_again(monkeypatch: pytest.MonkeyPatch) -> None:
    logins = _approle(monkeypatch)
    openbao.token()
    monkeypatch.setattr(openbao.time, "monotonic", lambda: 10**9)
    assert openbao.token() == "t2" and len(logins) == 2


def test_without_an_approle_the_static_token_or_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    assert openbao.token() is None
    monkeypatch.setenv("OPENBAO_TOKEN", "tok")
    assert openbao.token() == "tok"


def test_a_failed_login_says_so_without_the_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENBAO_ADDR", "http://127.0.0.1:9")
    monkeypatch.setenv("OPENBAO_ROLE_ID", "role")
    monkeypatch.setenv("OPENBAO_SECRET_ID", "s3cr3t")
    with pytest.raises(openbao.VaultLoginError) as caught:
        openbao.token(timeout=1)
    assert "s3cr3t" not in str(caught.value) and "role" not in str(caught.value)


def test_transit_from_env_takes_the_approle_and_asks_for_a_token_per_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approle(monkeypatch)
    keys = transit.transit_from_env()
    assert keys is not None
    asked: list[str] = []
    monkeypatch.setattr(transit.openbao, "token", lambda: asked.append("x") or "t")
    rebuilt = transit.OpenBaoTransit("http://openbao:8200", transit.openbao.token)
    assert rebuilt._token_now() == "t" and rebuilt._token_now() == "t"
    assert asked == ["x", "x"]
