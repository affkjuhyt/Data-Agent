"""OpenBao reader for a source's API key (auth_mode = api_key); the key never enters a row or a log.

secret_ref is the KV v2 key tenants/<org id>/crawl-sources/… of the `secret` mount, as the api
accepts it (acquire rules); anything else, another organisation's key or another secret of the
same one, is refused before any call. The crawler's AppRole could not read it anyway.
"""

from __future__ import annotations

import os
import re
import time
import uuid

import httpx


class SecretUnavailable(RuntimeError):
    pass


class Reader:
    def __init__(self) -> None:
        self.addr = os.environ.get("OPENBAO_ADDR", "").rstrip("/")
        self.role_id = os.environ.get("OPENBAO_ROLE_ID", "")
        self.secret_id = os.environ.get("OPENBAO_SECRET_ID", "")
        self.token: str | None = None
        self.expires = 0.0

    async def api_key(self, org_id: uuid.UUID, ref: str | None) -> str:
        parts = (ref or "").strip("/").split("/")
        if parts[:1] == ["secret"]:
            parts = parts[1:]
        if len(parts) < 4 or parts[:3] != ["tenants", str(org_id), "crawl-sources"]:
            raise SecretUnavailable(
                "source secret_ref is not a crawl-source key of the organisation"
            )
        if not all(
            re.fullmatch(r"[A-Za-z0-9_.-]+", part) and part not in (".", "..") for part in parts
        ):
            raise SecretUnavailable("source secret_ref is invalid")
        if not (self.addr and self.role_id and self.secret_id):
            raise SecretUnavailable("OpenBao is not configured")
        # OpenBao is in the cluster: never through the egress proxy.
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
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
                f"{self.addr}/v1/secret/data/{'/'.join(parts)}",
                headers={"X-Vault-Token": self.token},
            )
        if response.status_code != 200:
            raise SecretUnavailable("source secret could not be read")
        values = response.json()["data"]["data"]
        key = values.get("api_key") or values.get("value")
        if not key:
            raise SecretUnavailable("source secret has no api_key")
        return str(key)
