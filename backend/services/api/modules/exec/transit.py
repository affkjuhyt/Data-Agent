"""The vault's Transit key in OpenBao (BR-189, tab 5 G10.1): the TransitKeys port of ports.py.

The api creates one RSA-4096 key per vault and reads its public half, which the device encrypts
the conditions under. It never asks Transit to decrypt: only svc_exec_worker's OpenBao policy may.
Same connection as the governance module's KV reads: the api's AppRole (kernel/openbao.py), whose
policy covers transit/keys/exec-cond-*; the mount is EXEC_TRANSIT_MOUNT (default "transit").
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from starlette.concurrency import run_in_threadpool

from services.api.kernel import openbao
from services.api.kernel.problems import Problem
from services.api.modules.exec.ports import TransitKey

# exec-cond-<org uuid>-<user uuid>: the route's names; anything else never reaches the URL path.
_NAME = re.compile(r"[a-z0-9-]{1,128}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # A redirect would replay the token to wherever it points.
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


class OpenBaoTransit:
    def __init__(
        self,
        addr: str,
        token: str | Callable[[], str | None],
        mount: str = "transit",
        timeout: float = 5.0,
    ):
        self._base = f"{addr.rstrip('/')}/v1/{mount.strip('/')}"
        # A callable is asked on every call: an AppRole token expires, a static one does not.
        self._token = token
        self._timeout = timeout

    def _token_now(self) -> str:
        if isinstance(self._token, str):
            return self._token
        try:
            value = self._token()
        except openbao.VaultLoginError as exc:
            raise Problem(
                503, "source_unavailable", "The vault key service is unreachable."
            ) from exc
        if not value:
            raise Problem(503, "source_unavailable", "The vault key service is not configured.")
        return value

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self._base}/{path}",
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "X-Vault-Token": self._token_now(),
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with _OPENER.open(request, timeout=self._timeout) as response:
                raw = response.read()
        except (OSError, urllib.error.URLError) as exc:
            # The message never carries the token or the response body.
            raise Problem(
                503, "source_unavailable", "The vault key service is unavailable."
            ) from exc
        try:
            parsed: dict[str, Any] = json.loads(raw) if raw else {}
        except ValueError as exc:
            raise Problem(
                503, "source_unavailable", "The vault key service answered badly."
            ) from exc
        return parsed

    def _read(self, name: str, version: int | None) -> TransitKey:
        data = self._call("GET", f"keys/{name}").get("data") or {}
        chosen = version or int(data.get("latest_version") or 0)
        entry = (data.get("keys") or {}).get(str(chosen)) or {}
        pem = entry.get("public_key") if isinstance(entry, dict) else None
        if data.get("type") != "rsa-4096" or not pem:
            raise Problem(
                503, "source_unavailable", "The vault key is not an RSA-4096 Transit key."
            )
        return TransitKey(public_key_pem=pem, version=chosen)

    def _create(self, name: str) -> TransitKey:
        # Writing an existing key's config is accepted and changes nothing, so a retry is safe.
        self._call("POST", f"keys/{name}", {"type": "rsa-4096", "exportable": False})
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
    addr = openbao.address()
    has_role = os.environ.get("OPENBAO_ROLE_ID") and os.environ.get("OPENBAO_SECRET_ID")
    if not addr or not (has_role or os.environ.get("OPENBAO_TOKEN")):
        return None
    return OpenBaoTransit(addr, openbao.token, os.environ.get("EXEC_TRANSIT_MOUNT", "transit"))
