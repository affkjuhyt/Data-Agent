"""OpenBao reader for a source's API key (auth_mode = api_key); the key never enters a row or a log.

secret_ref is the KV v2 key tenants/<org id>/crawl-sources/… of the `secret` mount, as the api
accepts it (acquire rules); anything else, another organisation's key or another secret of the
same one, is refused before any call. The crawler's AppRole could not read it anyway.
"""

from __future__ import annotations

import asyncio
import functools
import re
import uuid

from libs.openbao import OpenBao, OpenBaoError, from_env


class SecretUnavailable(RuntimeError):
    pass


@functools.cache
def _vault() -> OpenBao | None:
    # One client per process: its AppRole login is reused across sources and runs. It never goes
    # through the egress proxy (libs/openbao ignores the environment's proxy; OpenBao is in-cluster).
    return from_env()


class Reader:
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
        vault = _vault()
        if vault is None:
            raise SecretUnavailable("OpenBao is not configured")
        try:
            values = await asyncio.to_thread(vault.read_kv, "/".join(parts))
        except OpenBaoError as exc:
            raise SecretUnavailable(f"source secret could not be read: {exc}") from exc
        key = values.get("api_key") or values.get("value")
        if not key:
            raise SecretUnavailable("source secret has no api_key")
        return str(key)
