"""The api's OpenBao token, from its own AppRole (OPENBAO_ROLE_ID, OPENBAO_SECRET_ID).

The api writes a source account typed in the UI, reads a model provider's key and keeps the exec
vault's Transit keys. Its AppRole policy (dataagent-api-policy, deploy/ansible/roles/k8s_openbao)
grants exactly that; the api used to hold the root token for it. The login is cached until a
little before its lease ends, and the calls stay synchronous like the callers (urllib in a
threadpool).

Without an AppRole the static OPENBAO_TOKEN is used, which is what a test or a local run sets; a
cluster never gives the api one.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request


class VaultLoginError(Exception):
    """No token could be had. The message never contains a credential."""


_lock = threading.Lock()
_cached: tuple[str, float] | None = None


def address() -> str:
    return os.environ.get("OPENBAO_ADDR", "").rstrip("/")


def token(*, timeout: float = 5.0) -> str | None:
    """A token for the api's own calls, or None when OpenBao is not configured at all."""
    global _cached
    role_id, secret_id = os.environ.get("OPENBAO_ROLE_ID"), os.environ.get("OPENBAO_SECRET_ID")
    if not (role_id and secret_id):
        return os.environ.get("OPENBAO_TOKEN") or None
    addr = address()
    if not addr:
        return None
    with _lock:
        if _cached and time.monotonic() < _cached[1]:
            return _cached[0]
        request = urllib.request.Request(
            f"{addr}/v1/auth/approle/login",
            data=json.dumps({"role_id": role_id, "secret_id": secret_id}).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                auth = json.loads(response.read())["auth"]
        except (OSError, ValueError, KeyError, urllib.error.URLError) as exc:
            raise VaultLoginError("the api could not log in to OpenBao") from exc
        value = str(auth["client_token"])
        # Renew a little before the lease ends rather than on the first 403.
        _cached = (value, time.monotonic() + max(int(auth.get("lease_duration", 60)) - 30, 30))
        return value


def forget() -> None:
    """Drop the cached login (tests, and a 403 that says the token was revoked)."""
    global _cached
    with _lock:
        _cached = None
