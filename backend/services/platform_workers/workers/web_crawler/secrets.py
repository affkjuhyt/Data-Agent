"""Resolves a `KBSourceConfig.config.openbao_path` reference to a plaintext
credential for the `rest_api`/`sdmx` connector — modeled on
`workflow_runtime/warehouse_secrets.py`'s read-only AppRole pattern (NOT
`schema_crawler/metadata.py`'s env-var `_password`, which predates the
OpenBao migration). Unlike `warehouse_secrets.py`, there's no `secrets_vault`
row indirection here: `openbao_path` already holds the vault path directly, a
steward pastes it in after a platform admin creates/rotates the secret in
OpenBao out-of-band. This worker is a plain sync loop (no asyncio), so hvac
is called directly instead of via `asyncio.to_thread`.
"""
from __future__ import annotations

import logging
import os
from typing import cast

import hvac

log = logging.getLogger("platform-workers.web-crawler.secrets")

OPENBAO_ADDR = os.environ.get("OPENBAO_ADDR", "http://openbao:8200")
OPENBAO_ROLE_ID = os.environ.get("OPENBAO_ROLE_ID", "")
OPENBAO_SECRET_ID = os.environ.get("OPENBAO_SECRET_ID", "")

_VAULT_MOUNT = "secret"

_vault_client: "hvac.Client | None" = None


def _get_vault() -> "hvac.Client | None":
    global _vault_client
    if not OPENBAO_ROLE_ID or not OPENBAO_SECRET_ID:
        log.warning("OPENBAO_ROLE_ID/OPENBAO_SECRET_ID not configured — cannot resolve openbao_path")
        return None
    if _vault_client is not None:
        try:
            if _vault_client.is_authenticated():
                return _vault_client
        except Exception:
            pass
    client = hvac.Client(url=OPENBAO_ADDR)
    client.auth.approle.login(role_id=OPENBAO_ROLE_ID, secret_id=OPENBAO_SECRET_ID)
    _vault_client = client
    return _vault_client


def resolve_openbao_secret(path: str) -> str | None:
    """Returns the plaintext value at `path`, or None if OpenBao is
    unreachable/misconfigured or the path doesn't exist — callers treat None
    as "no credential available" and fetch unauthenticated rather than crash
    a run outright."""
    vault = _get_vault()
    if vault is None:
        return None
    try:
        resp = vault.secrets.kv.v2.read_secret_version(
            mount_point=_VAULT_MOUNT, path=path, raise_on_deleted_version=True,
        )
        return cast("str | None", resp["data"]["data"].get("value"))
    except Exception as e:
        log.warning("resolve_openbao_secret | failed to read vault path=%s: %s", path, e)
        return None
