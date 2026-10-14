"""OpenBao reader for source credentials; secret values never enter database rows or logs."""

from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import dataclass

import httpx


class SecretUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Credentials:
    user: str
    password: str


class Reader:
    def __init__(self) -> None:
        self.addr = os.environ.get("OPENBAO_ADDR", "").rstrip("/")
        self.role_id = os.environ.get("OPENBAO_ROLE_ID", "")
        self.secret_id = os.environ.get("OPENBAO_SECRET_ID", "")
        self.token: str | None = None
        self.expires = 0.0

    async def read(self, org_id: uuid.UUID, ref: str) -> Credentials:
        parts = ref.strip("/").split("/")
        if len(parts) < 4 or parts[:2] != ["secret", "tenants"] or parts[2] != str(org_id):
            raise SecretUnavailable("source secret_ref is outside the organisation")
        if not all(re.fullmatch(r"[A-Za-z0-9_-]+", part) for part in parts):
            raise SecretUnavailable("source secret_ref is invalid")
        if not (self.addr and self.role_id and self.secret_id):
            raise SecretUnavailable("OpenBao is not configured")
        async with httpx.AsyncClient(timeout=5) as client:
            if not self.token or time.monotonic() >= self.expires:
                login = await client.post(
                    f"{self.addr}/v1/auth/approle/login",
                    json={"role_id": self.role_id, "secret_id": self.secret_id},
                )
                if login.status_code != 200:
                    raise SecretUnavailable("OpenBao login failed")
                auth = login.json()["auth"]
                self.token = str(auth["client_token"])
                self.expires = time.monotonic() + max(int(auth.get("lease_duration", 60)) - 30, 30)
            response = await client.get(
                f"{self.addr}/v1/secret/data/{'/'.join(parts[1:])}",
                headers={"X-Vault-Token": self.token},
            )
        if response.status_code != 200:
            raise SecretUnavailable("source secret could not be read")
        values = response.json()["data"]["data"]
        user = values.get("user") or values.get("username")
        if not user or values.get("password") is None:
            raise SecretUnavailable("source secret has no user and password")
        return Credentials(str(user), str(values["password"]))
