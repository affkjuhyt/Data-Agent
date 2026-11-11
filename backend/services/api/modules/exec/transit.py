"""The vault's Transit key in OpenBao (BR-189, tab 5 G10.1): the TransitKeys port of ports.py.

The api creates one RSA-4096 key per vault and reads its public half, which the device encrypts
the conditions under. It never asks Transit to decrypt: only svc_exec_worker's OpenBao policy may.
Same connection as the governance module's KV reads: the api's AppRole (kernel/openbao.py), whose
policy covers transit/keys/exec-cond-*; the mount is EXEC_TRANSIT_MOUNT (default "transit").
"""

from __future__ import annotations

import os
import re

from starlette.concurrency import run_in_threadpool

from services.api.kernel import openbao
from services.api.kernel.problems import Problem
from services.api.modules.exec.ports import TransitKey

# exec-cond-<org uuid>-<user uuid>: the route's names; anything else never reaches the URL path.
_NAME = re.compile(r"[a-z0-9-]{1,128}")


class OpenBaoTransit:
    def __init__(self, vault: openbao.OpenBao, mount: str = "transit"):
        self._vault = vault
        self._mount = mount.strip("/")

    def _read(self, name: str, version: int | None) -> TransitKey:
        try:
            data = self._vault.transit_key(name, mount=self._mount)
        except openbao.OpenBaoError as exc:
            # The message never carries the token or OpenBao's body (libs/openbao).
            raise Problem(
                503, "source_unavailable", "The vault key service is unavailable."
            ) from exc
        chosen = version or int(data.get("latest_version") or 0)
        entry = (data.get("keys") or {}).get(str(chosen)) or {}
        pem = entry.get("public_key") if isinstance(entry, dict) else None
        if data.get("type") != "rsa-4096" or not pem:
            raise Problem(
                503, "source_unavailable", "The vault key is not an RSA-4096 Transit key."
            )
        return TransitKey(public_key_pem=pem, version=chosen)

    def _create(self, name: str) -> TransitKey:
        try:
            self._vault.create_transit_key(
                name, key_type="rsa-4096", exportable=False, mount=self._mount
            )
        except openbao.OpenBaoError as exc:
            raise Problem(
                503, "source_unavailable", "The vault key service is unavailable."
            ) from exc
        return self._read(name, None)

    async def create(self, name: str) -> TransitKey:
        if not _NAME.fullmatch(name):
            raise Problem(500, "internal_error", "The vault key name is invalid.")
        return await run_in_threadpool(self._create, name)

    async def public_key(self, name: str, version: int) -> TransitKey:
        if not _NAME.fullmatch(name):
            raise Problem(500, "internal_error", "The vault key name is invalid.")
        return await run_in_threadpool(self._read, name, version)


def transit_from_env() -> OpenBaoTransit | None:
    """The adapter when OpenBao is configured; None keeps the routes' 503 (ports.transit)."""
    vault = openbao.vault()
    if vault is None:
        return None
    return OpenBaoTransit(vault, os.environ.get("EXEC_TRANSIT_MOUNT", "transit"))
