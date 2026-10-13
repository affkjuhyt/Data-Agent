"""The vault's Transit adapter (BR-189) against a stand-in for OpenBao's Transit API."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, ClassVar

import pytest

from services.api.kernel.problems import Problem
from services.api.modules.exec.transit import OpenBaoTransit, transit_from_env

pytestmark = pytest.mark.unit

PEM = "-----BEGIN PUBLIC KEY-----\nMIIC...\n-----END PUBLIC KEY-----\n"


class _Bao(BaseHTTPRequestHandler):
    keys: ClassVar[dict[str, dict[str, Any]]] = {}
    seen: ClassVar[list[tuple[str, str, str | None, dict[str, Any] | None]]] = []

    def _send(self, status: int, body: dict[str, Any] | None = None) -> None:
        raw = json.dumps(body).encode() if body is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        self.seen.append(("POST", self.path, self.headers.get("X-Vault-Token"), body))
        name = self.path.rsplit("/", 1)[-1]
        # Creating an existing key leaves it as it is, as OpenBao does.
        self.keys.setdefault(name, {"type": body["type"], "latest_version": 1})
        self._send(204)

    def do_GET(self) -> None:
        self.seen.append(("GET", self.path, self.headers.get("X-Vault-Token"), None))
        name = self.path.rsplit("/", 1)[-1]
        key = self.keys.get(name)
        if key is None:
            self._send(404, {"errors": []})
            return
        versions = {str(v): {"public_key": PEM} for v in range(1, key["latest_version"] + 1)}
        self._send(200, {"data": {**key, "keys": versions}})

    def log_message(self, *args: Any) -> None:
        return None


@pytest.fixture
def bao() -> Iterator[str]:
    _Bao.keys, _Bao.seen = {}, []
    server = HTTPServer(("127.0.0.1", 0), _Bao)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


async def test_a_vault_key_is_created_once_and_its_public_half_read(bao: str) -> None:
    transit = OpenBaoTransit(bao, "tok")
    name = "exec-cond-0198a1b2-0000-7000-8000-000000000001-0198a1b2-0000-7000-8000-000000000002"
    first = await transit.create(name)
    again = await transit.create(name)
    assert first.public_key_pem == PEM and first.version == 1 and again.version == 1
    assert len(_Bao.keys) == 1
    posted = [s for s in _Bao.seen if s[0] == "POST"]
    assert posted[0][1] == f"/v1/transit/keys/{name}" and posted[0][2] == "tok"
    assert posted[0][3] == {"type": "rsa-4096", "exportable": False}
    assert (await transit.public_key(name, 1)).public_key_pem == PEM


async def test_a_name_outside_the_routes_scheme_never_reaches_openbao(bao: str) -> None:
    transit = OpenBaoTransit(bao, "tok")
    with pytest.raises(Problem):
        await transit.create("../../sys/policy/root")
    assert _Bao.seen == []


async def test_an_unreachable_or_wrong_key_is_503(bao: str) -> None:
    down = OpenBaoTransit("http://127.0.0.1:9", "tok", timeout=1)
    with pytest.raises(Problem) as unreachable:
        await down.create("exec-cond-a")
    assert unreachable.value.status == 503
    _Bao.keys["exec-cond-b"] = {"type": "aes256-gcm96", "latest_version": 1}
    with pytest.raises(Problem) as wrong:
        await OpenBaoTransit(bao, "tok").public_key("exec-cond-b", 1)
    assert wrong.value.status == 503


def test_without_openbao_the_routes_keep_their_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENBAO_ADDR", raising=False)
    monkeypatch.delenv("OPENBAO_TOKEN", raising=False)
    assert transit_from_env() is None
    monkeypatch.setenv("OPENBAO_ADDR", "http://openbao:8200")
    monkeypatch.setenv("OPENBAO_TOKEN", "tok")
    assert isinstance(transit_from_env(), OpenBaoTransit)
