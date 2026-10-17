"""Write a source account into OpenBao. The database keeps only the path (BR-14)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid

from libs.dataagent_db import uuid7
from services.api.kernel import openbao


class SecretStoreError(Exception):
    """The account could not be stored. The message never contains a secret."""


def account_ref(
    org_id: uuid.UUID, source_id: uuid.UUID, version_id: uuid.UUID, *, writeback: bool
) -> str:
    # A fresh version on every write. The source row adopts the path only after the caller
    # is allowed to change that source, so a refused request cannot overwrite the account
    # the source already points at.
    kind = "writeback" if writeback else "account"
    return f"secret/tenants/{org_id}/sources/{source_id}/{kind}-{version_id}"


def put_account(
    org_id: uuid.UUID,
    source_id: uuid.UUID,
    username: str,
    password: str,
    *,
    writeback: bool,
    addr: str | None = None,
    token: str | None = None,
    version_id: uuid.UUID | None = None,
) -> str:
    """Store ``user`` and ``password`` and return the path the source row keeps.

    ``password`` may be empty. The vault error body is discarded: a KV rejection can echo
    the payload.
    """
    addr = openbao.address() if addr is None else addr
    if token is None:
        # The api's own AppRole, write-only on tenant source paths (kernel/openbao.py).
        try:
            token = openbao.token() or ""
        except openbao.VaultLoginError as exc:
            raise SecretStoreError("the secret store is unavailable") from exc
    if not addr or not token:
        raise SecretStoreError("the secret store is not configured")
    ref = account_ref(org_id, source_id, version_id or uuid7(), writeback=writeback)
    path = ref.removeprefix("secret/")
    request = urllib.request.Request(
        f"{addr.rstrip('/')}/v1/secret/data/{path}",
        data=json.dumps({"data": {"user": username, "password": password}}).encode(),
        method="POST",
        headers={"X-Vault-Token": token, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            if response.status >= 300:
                raise SecretStoreError("the secret store rejected the account")
    except urllib.error.HTTPError as exc:
        raise SecretStoreError("the secret store rejected the account") from exc
    except (OSError, TimeoutError) as exc:
        raise SecretStoreError("the secret store is unavailable") from exc
    return ref
