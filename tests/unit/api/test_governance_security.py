"""Provider secrets stay inside their organisation and a frontier probe reaches only the
public internet (security review of the model-provider routes)."""

from __future__ import annotations

import uuid

import pytest

from services.api.kernel.problems import Problem
from services.api.modules.governance.routes import _public_https, _tenant_secret_ref

pytestmark = pytest.mark.unit

ORG = uuid.UUID("01920000-0000-7000-8000-000000000001")
OTHER = uuid.UUID("01920000-0000-7000-8000-000000000002")


def test_a_secret_ref_names_a_key_of_the_callers_organisation() -> None:
    mine = f"secret/tenants/{ORG}/providers/frontier-a"
    assert _tenant_secret_ref(mine, ORG) == mine
    for ref in (
        f"secret/tenants/{OTHER}/providers/frontier-a",
        f"secret/tenants/{ORG}/../{OTHER}/providers/frontier-a",
        f"secret/tenants/{ORG}/",
        "kv/data-agent/providers/frontier-a",
        f"secret/tenants/{ORG}/a b",
    ):
        with pytest.raises(Problem):
            _tenant_secret_ref(ref, ORG)


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.com/v1",
        "https://127.0.0.1/v1",
        "https://10.0.0.5/v1",
        "https://169.254.169.254/latest",
        "https://[::1]/v1",
        "https://user:pw@example.com/v1",
        "https://localhost/v1",
    ],
)
def test_a_frontier_endpoint_is_a_public_https_host(url: str) -> None:
    assert _public_https(url) is False


def test_the_probe_connects_to_the_address_it_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    # DNS rebinding: the name answers a public address at the check, then a private one. The
    # connection goes to the checked address, never to a second answer.
    from services.api.modules.governance import routes

    answers = iter(["93.184.216.34", "10.0.0.5"])

    def resolve(host: str, port: int, **_: object) -> list[tuple[object, ...]]:
        return [(None, None, None, "", (next(answers), port))]

    reached: list[tuple[str, int]] = []

    def connect(address: tuple[str, int], timeout: float) -> None:
        reached.append(address)
        raise OSError("stop before TLS")

    monkeypatch.setattr(routes.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(routes.socket, "create_connection", connect)
    address = routes._public_address("https://api.example.com/v1")
    assert address == "93.184.216.34"
    with pytest.raises(OSError):
        routes._post_pinned("https://api.example.com/v1/messages", address, b"{}", {})
    assert reached == [("93.184.216.34", 443)]


def test_a_name_with_any_private_address_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.api.modules.governance import routes

    def resolve(host: str, port: int, **_: object) -> list[tuple[object, ...]]:
        return [
            (None, None, None, "", ("93.184.216.34", port)),
            (None, None, None, "", ("10.0.0.5", port)),
        ]

    monkeypatch.setattr(routes.socket, "getaddrinfo", resolve)
    assert routes._public_address("https://api.example.com/v1") is None
