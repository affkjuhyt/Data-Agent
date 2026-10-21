"""Synchronous OpenBao reader used by the schema crawler (a warehouse password by its vault path).

Read with the platform-workers AppRole; the worker is a plain sync loop, so hvac is called directly.
None means no credential: the caller skips the warehouse rather than crash the crawl.
"""
from __future__ import annotations

import logging
import os
from typing import cast

import hvac

log = logging.getLogger("platform-workers.schema-crawler.secrets")

OPENBAO_ADDR = os.environ.get("OPENBAO_ADDR", "http://openbao:8200")
OPENBAO_ROLE_ID = os.environ.get("OPENBAO_ROLE_ID", "")
OPENBAO_SECRET_ID = os.environ.get("OPENBAO_SECRET_ID", "")

_VAULT_MOUNT = "secret"

_vault_client: hvac.Client | None = None


def _get_vault() -> hvac.Client | None:
    global _vault_client
    if not OPENBAO_ROLE_ID or not OPENBAO_SECRET_ID:
        log.warning("OPENBAO_ROLE_ID/OPENBAO_SECRET_ID not configured — cannot resolve openbao_path")
        return None
    if _vault_client is not None:
        try:
            if _vault_client.is_authenticated():
                return _vault_client
        except Exception:  # noqa: BLE001 — a dead session just logs in again below
            pass
    client = hvac.Client(url=OPENBAO_ADDR)
    client.auth.approle.login(role_id=OPENBAO_ROLE_ID, secret_id=OPENBAO_SECRET_ID)
    _vault_client = client
    return _vault_client


def resolve_openbao_secret(path: str) -> str | None:
    """Returns the plaintext value at `path`, or None if OpenBao is
    unreachable/misconfigured or the path doesn't exist — callers treat None
    as "no credential available" rather than crash the crawl."""
    vault = _get_vault()
    if vault is None:
        return None
    try:
        resp = vault.secrets.kv.v2.read_secret_version(
            mount_point=_VAULT_MOUNT, path=path, raise_on_deleted_version=True,
        )
        return cast("str | None", resp["data"]["data"].get("value"))
    except Exception as e:  # noqa: BLE001 — None is the documented "no credential" answer
        log.warning("resolve_openbao_secret | failed to read vault path=%s: %s", path, e)
        return None


__all__ = ["resolve_openbao_secret"]
