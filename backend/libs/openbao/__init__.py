"""OpenBao for every service: one AppRole (or static token) login, KV v2 and Transit, via hvac.

The api, worker-agent, worker-ingest and the crawler each carried their own client (urllib or
httpx), each with its own login cache and its own idea of what may go wrong. This is the one they
share, built on hvac (Apache-2.0), with what they all had to get right held here:

- a token is never sent anywhere but OpenBao: redirects are not followed (one would replay the
  token, or a provider key read with it, to wherever it points), and no proxy from the environment
  is used (the crawler's pod routes the internet through an egress proxy; OpenBao is in-cluster);
- an error says what failed and its status, never OpenBao's body: a KV rejection can echo the
  payload, a password included. It is raised without its cause for the same reason;
- an AppRole login is reused until a little before its lease ends, and replaced once when OpenBao
  says the token is no longer good (revoked, or the lease was shortened).

Calls are synchronous, as hvac is: async callers run them with asyncio.to_thread.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from typing import Any, TypeVar

import hvac
import requests
from hvac.exceptions import Forbidden, VaultError

T = TypeVar("T")

# Renew this long before the lease ends rather than on the first refusal.
RENEW_BEFORE_S = 30


class OpenBaoError(Exception):
    """OpenBao could not be used. The message never contains a credential or OpenBao's body."""


class OpenBao:
    def __init__(
        self,
        addr: str,
        *,
        role_id: str | None = None,
        secret_id: str | None = None,
        token: str | None = None,
        timeout: float = 5.0,
    ) -> None:
        session = requests.Session()
        session.trust_env = False
        session.max_redirects = 0
        self._client = hvac.Client(
            url=addr.rstrip("/"),
            token=token,
            timeout=timeout,
            session=session,
            allow_redirects=False,
        )
        self._approle = (role_id, secret_id) if role_id and secret_id else None
        self._expires = 0.0
        self._lock = threading.Lock()

    def _login(self) -> None:
        if self._approle is None:
            if not self._client.token:
                raise OpenBaoError("OpenBao has no token to use")
            return
        with self._lock:
            if self._client.token and time.monotonic() < self._expires:
                return
            role_id, secret_id = self._approle
            try:
                answer = self._client.auth.approle.login(
                    role_id=role_id, secret_id=secret_id, use_token=True
                )
            except (VaultError, requests.RequestException, ValueError, KeyError):
                raise OpenBaoError("OpenBao login failed") from None
            lease = int(answer.get("auth", {}).get("lease_duration") or 60)
            self._expires = time.monotonic() + max(lease - RENEW_BEFORE_S, RENEW_BEFORE_S)

    def forget(self) -> None:
        """Drop the login, so the next call logs in again (tests; a token known to be revoked)."""
        with self._lock:
            self._expires = 0.0

    def _call(self, what: str, call: Callable[[hvac.Client], T]) -> T:
        self._login()
        try:
            return call(self._client)
        except Forbidden:
            if self._approle is None:
                raise OpenBaoError(f"{what}: OpenBao refused the token (403)") from None
        except VaultError as exc:
            raise OpenBaoError(f"{what}: OpenBao answered {_status(exc)}") from None
        except (requests.RequestException, ValueError, KeyError):
            raise OpenBaoError(f"{what}: OpenBao is unreachable or answered badly") from None
        # A 403 on a login thought good: the token was revoked. One new login, one more try.
        self.forget()
        self._login()
        try:
            return call(self._client)
        except VaultError as exc:
            raise OpenBaoError(f"{what}: OpenBao answered {_status(exc)}") from None
        except (requests.RequestException, ValueError, KeyError):
            raise OpenBaoError(f"{what}: OpenBao is unreachable or answered badly") from None

    def read_kv(self, path: str, *, mount: str = "secret") -> dict[str, Any]:
        """The fields of a KV v2 secret at `mount`/`path`."""

        def read(client: hvac.Client) -> dict[str, Any]:
            answer = client.secrets.kv.v2.read_secret_version(
                path=path, mount_point=mount, raise_on_deleted_version=True
            )
            data = answer["data"]["data"]
            if not isinstance(data, dict):
                raise ValueError("not a KV v2 secret")
            return dict(data)

        return self._call("read secret", read)

    def write_kv(self, path: str, data: dict[str, Any], *, mount: str = "secret") -> None:
        """Write a new version of the KV v2 secret at `mount`/`path`."""
        self._call(
            "write secret",
            lambda client: client.secrets.kv.v2.create_or_update_secret(
                path=path, secret=data, mount_point=mount
            ),
        )

    def transit_key(self, name: str, *, mount: str = "transit") -> dict[str, Any]:
        """A Transit key's description (type, versions and their public halves)."""

        def read(client: hvac.Client) -> dict[str, Any]:
            data = client.secrets.transit.read_key(name=name, mount_point=mount)["data"]
            if not isinstance(data, dict):
                raise ValueError("not a Transit key")
            return dict(data)

        return self._call("read key", read)

    def create_transit_key(
        self, name: str, *, key_type: str, exportable: bool = False, mount: str = "transit"
    ) -> None:
        """Create a Transit key; for an existing one OpenBao changes nothing, so a retry is safe."""
        self._call(
            "create key",
            lambda client: client.secrets.transit.create_key(
                name=name, key_type=key_type, exportable=exportable, mount_point=mount
            ),
        )


def _status(exc: VaultError) -> str:
    # hvac's message is OpenBao's errors list: the status only, the body may echo a payload.
    return {
        "InvalidRequest": "400",
        "Unauthorized": "401",
        "Forbidden": "403",
        "InvalidPath": "404",
        "RateLimitExceeded": "429",
        "InternalServerError": "500",
        "VaultNotInitialized": "501",
        "BadGateway": "502",
        "VaultDown": "503",
    }.get(type(exc).__name__, "an error")


def from_env() -> OpenBao | None:
    """The service's own OpenBao (OPENBAO_ADDR with OPENBAO_ROLE_ID and OPENBAO_SECRET_ID, or the
    static OPENBAO_TOKEN a test or a local run sets); None when it has neither."""
    addr = os.environ.get("OPENBAO_ADDR", "").strip()
    role_id, secret_id = os.environ.get("OPENBAO_ROLE_ID"), os.environ.get("OPENBAO_SECRET_ID")
    token = os.environ.get("OPENBAO_TOKEN")
    if not addr or not ((role_id and secret_id) or token):
        return None
    return OpenBao(addr, role_id=role_id, secret_id=secret_id, token=token)
