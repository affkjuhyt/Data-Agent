"""Write a source account into OpenBao. The database keeps only the path (BR-14)."""

from __future__ import annotations

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
    vault: openbao.OpenBao | None = None,
    version_id: uuid.UUID | None = None,
) -> str:
    """Store ``user`` and ``password`` and return the path the source row keeps.

    ``password`` may be empty. The store is the api's own AppRole, write-only on tenant source
    paths (kernel/openbao.py); its errors never carry the payload (libs/openbao).
    """
    store = vault if vault is not None else openbao.vault()
    if store is None:
        raise SecretStoreError("the secret store is not configured")
    ref = account_ref(org_id, source_id, version_id or uuid7(), writeback=writeback)
    try:
        store.write_kv(ref.removeprefix("secret/"), {"user": username, "password": password})
    except openbao.OpenBaoError as exc:
        raise SecretStoreError("the secret store rejected the account or is unavailable") from exc
    return ref
