"""Reading a source's credentials from OpenBao (BR-14: the database stores only the path).

catalog.source.secret_ref is `secret/tenants/{org_id}/<segments>` (the api checks the form and
that the org segment is the source's own). The reader logs in with an AppRole, reads the KV v2
entry and returns its fields (`user`/`username`, `password`, optionally `sslmode`). The org check
is repeated here: a ref of another organisation is refused before any call.

A port, so tests and a cluster without OpenBao use another implementation.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


class SecretUnavailable(RuntimeError):
    """The credentials could not be read; the message never contains a secret."""


@dataclass(frozen=True)
class Credentials:
    user: str
    password: str
    extra: dict[str, str]


class SecretReader(Protocol):
    async def read(self, org_id: uuid.UUID, secret_ref: str) -> Credentials: ...


_SEGMENT = re.compile(r"[A-Za-z0-9_-]+")


def _check_ref(org_id: uuid.UUID, secret_ref: str) -> str:
    parts = secret_ref.strip("/").split("/")
    # Every segment is a plain name: an empty, "." or ".." segment (or an encoded one) would let
    # the HTTP client's path normalisation walk out of this organisation's subtree.
    if (
        len(parts) < 4
        or parts[:2] != ["secret", "tenants"]
        or parts[2] != str(org_id)
        or not all(_SEGMENT.fullmatch(p) for p in parts)
    ):
        raise SecretUnavailable("the source's secret_ref is not one of its organisation's paths")
    # KV v2: secret/<path> is read at /v1/secret/data/<path>.
    return "/".join(parts[1:])


def _credentials(data: dict[str, Any]) -> Credentials:
    user = data.get("user") or data.get("username")
    password = data.get("password")
    if not user or password is None:
        raise SecretUnavailable("the secret has no user and password")
    extra = {k: str(v) for k, v in data.items() if k not in ("user", "username", "password")}
    return Credentials(str(user), str(password), extra)


class OpenBaoReader:
    """KV v2 reads with an AppRole login (OPENBAO_ADDR, OPENBAO_ROLE_ID, OPENBAO_SECRET_ID)."""

    def __init__(self, addr: str, role_id: str, secret_id: str, *, timeout: float = 5.0) -> None:
        self._addr = addr.rstrip("/")
        self._role_id, self._secret_id = role_id, secret_id
        self._timeout = timeout
        self._token: str | None = None
        self._token_expires = 0.0

    async def _login(self, client: httpx.AsyncClient) -> str:
        if self._token and time.monotonic() < self._token_expires:
            return self._token
        r = await client.post(
            f"{self._addr}/v1/auth/approle/login",
            json={"role_id": self._role_id, "secret_id": self._secret_id},
        )
        if r.status_code != 200:
            raise SecretUnavailable(f"OpenBao login failed ({r.status_code})")
        auth = r.json()["auth"]
        self._token = str(auth["client_token"])
        # Renew a little before the lease ends rather than on the first 403.
        self._token_expires = time.monotonic() + max(int(auth.get("lease_duration", 60)) - 30, 30)
        return self._token

    async def read(self, org_id: uuid.UUID, secret_ref: str) -> Credentials:
        path = _check_ref(org_id, secret_ref)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            token = await self._login(client)
            r = await client.get(
                f"{self._addr}/v1/secret/data/{path}", headers={"X-Vault-Token": token}
            )
        if r.status_code != 200:
            raise SecretUnavailable(f"the source's secret could not be read ({r.status_code})")
        return _credentials(r.json()["data"]["data"])


class StaticReader:
    """Credentials held in memory, keyed by secret_ref: tests, and a dev cluster without OpenBao."""

    def __init__(self, secrets: dict[str, dict[str, Any]]) -> None:
        self._secrets = secrets

    async def read(self, org_id: uuid.UUID, secret_ref: str) -> Credentials:
        _check_ref(org_id, secret_ref)
        if secret_ref not in self._secrets:
            raise SecretUnavailable("no credentials for this source")
        return _credentials(self._secrets[secret_ref])


def reader_from_env() -> SecretReader | None:
    addr = os.environ.get("OPENBAO_ADDR")
    role_id, secret_id = os.environ.get("OPENBAO_ROLE_ID"), os.environ.get("OPENBAO_SECRET_ID")
    if not (addr and role_id and secret_id):
        return None
    return OpenBaoReader(addr, role_id, secret_id)
