"""libs/openbao against a stand-in for OpenBao: AppRole login, KV v2, Transit keys, and what it
must never do with a token (follow a redirect, go through a proxy, echo a rejected payload)."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, ClassVar

import pytest

from libs import openbao
from libs.openbao import OpenBao, OpenBaoError

pytestmark = pytest.mark.unit

ROLE, SECRET = "role-1", "secret-1"


class _Bao(BaseHTTPRequestHandler):
    kv: ClassVar[dict[str, dict[str, Any]]] = {}
    keys: ClassVar[dict[str, dict[str, Any]]] = {}
    logins: ClassVar[list[dict[str, Any]]] = []
    seen: ClassVar[list[tuple[str, str, str | None]]] = []
    lease: ClassVar[int] = 3600
    revoked: ClassVar[set[str]] = set()

    def _send(self, status: int, body: Any = None, headers: dict[str, str] | None = None) -> None:
        raw = json.dumps(body).encode() if body is not None else b""
        self.send_response(status)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def _token_ok(self) -> bool:
        token = self.headers.get("X-Vault-Token")
        self.seen.append((self.command, self.path, token))
        if not token or token in self.revoked:
            self._send(403, {"errors": ["permission denied"]})
            return False
        return True

    def do_POST(self) -> None:
        if self.path == "/v1/auth/approle/login":
            body = self._body()
            self.logins.append(body)
            if body != {"role_id": ROLE, "secret_id": SECRET}:
                self._send(400, {"errors": ["invalid role or secret ID"]})
                return
            token = f"t{len(self.logins)}"
            self._send(200, {"auth": {"client_token": token, "lease_duration": self.lease}})
            return
        if not self._token_ok():
            return
        body = self._body()
        if self.path.startswith("/v1/secret/data/"):
            path = self.path.removeprefix("/v1/secret/data/")
            if path.startswith("echo/"):
                # A rejection that repeats what was sent, as a KV policy error may.
                self._send(400, {"errors": [f"rejected {json.dumps(body)}"]})
                return
            self.kv[path] = body["data"]
            self._send(200, {"data": {"version": 1}})
        elif self.path.startswith("/v1/transit/keys/"):
            name = self.path.rsplit("/", 1)[-1]
            self.keys.setdefault(name, {"type": body["type"], "latest_version": 1})
            self._send(204)
        else:
            self._send(404, {"errors": []})

    def do_GET(self) -> None:
        if self.path.startswith("/v1/moved/"):
            # Whoever follows this redirect replays the token to the target.
            self._send(307, headers={"Location": "/v1/stolen"})
            return
        if not self._token_ok():
            return
        if self.path.startswith("/v1/secret/data/"):
            data = self.kv.get(self.path.removeprefix("/v1/secret/data/"))
            if data is None:
                self._send(404, {"errors": []})
                return
            self._send(200, {"data": {"data": data, "metadata": {"version": 1}}})
        elif self.path.startswith("/v1/transit/keys/"):
            key = self.keys.get(self.path.rsplit("/", 1)[-1])
            if key is None:
                self._send(404, {"errors": []})
                return
            self._send(200, {"data": {**key, "keys": {"1": {"public_key": "PEM"}}}})
        else:
            self._send(404, {"errors": []})

    def log_message(self, *args: Any) -> None:
        return None


@pytest.fixture
def bao() -> Iterator[str]:
    _Bao.kv, _Bao.keys, _Bao.logins, _Bao.seen = {}, {}, [], []
    _Bao.lease, _Bao.revoked = 3600, set()
    server = HTTPServer(("127.0.0.1", 0), _Bao)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_an_approle_logs_in_once_and_its_token_reads_and_writes(bao: str) -> None:
    vault = OpenBao(bao, role_id=ROLE, secret_id=SECRET)
    vault.write_kv("tenants/o1/sources/s1/account-1", {"user": "u", "password": "p"})
    assert vault.read_kv("tenants/o1/sources/s1/account-1") == {"user": "u", "password": "p"}
    assert len(_Bao.logins) == 1
    assert {token for _, path, token in _Bao.seen} == {"t1"}


def test_a_lease_about_to_end_logs_in_again(bao: str, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = OpenBao(bao, role_id=ROLE, secret_id=SECRET)
    _Bao.kv["a"] = {"v": "1"}
    vault.read_kv("a")
    clock = openbao.time.monotonic() + 3600
    monkeypatch.setattr(openbao.time, "monotonic", lambda: clock)
    vault.read_kv("a")
    assert len(_Bao.logins) == 2


def test_a_revoked_token_is_replaced_once(bao: str) -> None:
    vault = OpenBao(bao, role_id=ROLE, secret_id=SECRET)
    _Bao.kv["a"] = {"v": "1"}
    vault.read_kv("a")
    _Bao.revoked.add("t1")
    assert vault.read_kv("a") == {"v": "1"}
    assert len(_Bao.logins) == 2


def test_a_static_token_is_used_as_is(bao: str) -> None:
    _Bao.kv["a"] = {"v": "1"}
    assert OpenBao(bao, token="static").read_kv("a") == {"v": "1"}
    assert _Bao.logins == []


def test_a_failed_login_says_so_without_the_credentials(bao: str) -> None:
    with pytest.raises(OpenBaoError) as caught:
        OpenBao(bao, role_id=ROLE, secret_id="wrong-secret").read_kv("a")
    assert "wrong-secret" not in str(caught.value) and caught.value.__cause__ is None


def test_a_missing_secret_is_an_error_with_its_status(bao: str) -> None:
    with pytest.raises(OpenBaoError, match="404"):
        OpenBao(bao, token="static").read_kv("nothing/here")


def test_a_rejected_write_never_carries_the_payload(bao: str) -> None:
    with pytest.raises(OpenBaoError) as caught:
        OpenBao(bao, token="static").write_kv("echo/x", {"password": "hunter2"})
    assert "hunter2" not in str(caught.value) and caught.value.__cause__ is None


def test_a_redirect_is_not_followed(bao: str) -> None:
    with pytest.raises(OpenBaoError):
        OpenBao(bao, token="static").read_kv("x", mount="moved")
    assert not any(path == "/v1/stolen" for _, path, _ in _Bao.seen)


def test_no_proxy_from_the_environment_is_used(bao: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # The crawler's pod has an egress proxy in its environment; OpenBao is in the cluster.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "")
    _Bao.kv["a"] = {"v": "1"}
    assert OpenBao(bao, token="static").read_kv("a") == {"v": "1"}


def test_a_transit_key_is_created_and_read(bao: str) -> None:
    vault = OpenBao(bao, token="static")
    vault.create_transit_key("exec-u1", key_type="rsa-4096")
    vault.create_transit_key("exec-u1", key_type="rsa-4096")
    assert vault.transit_key("exec-u1")["keys"]["1"]["public_key"] == "PEM"
    assert _Bao.keys == {"exec-u1": {"type": "rsa-4096", "latest_version": 1}}


def test_from_env_needs_an_address_and_an_approle_or_a_token(
    bao: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("OPENBAO_ADDR", "OPENBAO_ROLE_ID", "OPENBAO_SECRET_ID", "OPENBAO_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    assert openbao.from_env() is None
    monkeypatch.setenv("OPENBAO_ADDR", bao)
    assert openbao.from_env() is None
    monkeypatch.setenv("OPENBAO_TOKEN", "static")
    assert openbao.from_env() is not None
