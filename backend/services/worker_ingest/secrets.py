"""OpenBao reader for source credentials; secret values never enter database rows or logs."""

from __future__ import annotations

import asyncio
import functools
import re
import uuid
from dataclasses import dataclass

from libs.openbao import OpenBao, OpenBaoError, from_env


class SecretUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Credentials:
    user: str
    password: str


@functools.cache
def _vault() -> OpenBao | None:
    # One client per process: its AppRole login is reused across sources and jobs.
    return from_env()


class Reader:
    async def read(self, org_id: uuid.UUID, ref: str) -> Credentials:
        parts = ref.strip("/").split("/")
        if len(parts) < 4 or parts[:2] != ["secret", "tenants"] or parts[2] != str(org_id):
            raise SecretUnavailable("source secret_ref is outside the organisation")
        if not all(re.fullmatch(r"[A-Za-z0-9_-]+", part) for part in parts):
            raise SecretUnavailable("source secret_ref is invalid")
        vault = _vault()
        if vault is None:
            raise SecretUnavailable("OpenBao is not configured")
        try:
            values = await asyncio.to_thread(vault.read_kv, "/".join(parts[1:]))
        except OpenBaoError as exc:
            raise SecretUnavailable(f"source secret could not be read: {exc}") from exc
        user = values.get("user") or values.get("username")
        if not user or values.get("password") is None:
            raise SecretUnavailable("source secret has no user and password")
        return Credentials(str(user), str(values["password"]))
