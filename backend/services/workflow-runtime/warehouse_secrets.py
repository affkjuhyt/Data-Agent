"""Resolves a warehouse/DB password referenced by a canvas node's secret_id
against OpenBao — instead of trusting a raw plaintext password value out of
the Mongo flow-node document (PR review gap, see
docs/Sandbox_SchemaCrawler_Implementation.md §9). `resolve_snowflake_password`
(WAREHOUSE_PASSWORD, Snowflake) and `resolve_db_connector_password`
(DB_CONNECTOR_PASSWORD, DB Connector node's Postgres connections) share the
same `_resolve_vault_password` lookup, differing only by expected `key_type` —
each key_type is only ever readable as itself, so a DB Connector secret_id can
never resolve as a Snowflake password or vice versa.

Reads `secrets_vault` directly (a second reader of backend/services/auth-api's
existing metadata table, same pattern as schema-crawler-worker reading
KBWarehouse instead of inventing a parallel registry) to find which vault
path a given secret_id maps to, then reads the value from OpenBao via a
read-only AppRole — deliberately NOT auth-api's root token, so a
compromised workflow-runtime can only read secrets, never create/rotate/
delete them. See deploy/docker/openbao/openbao-approle-setup.sh.
"""
import asyncio
import logging
import os
from typing import cast

import hvac
from sqlalchemy import text

logger = logging.getLogger(__name__)

OPENBAO_ADDR = os.environ.get("OPENBAO_ADDR", "http://openbao:8200")
OPENBAO_ROLE_ID = os.environ.get("OPENBAO_ROLE_ID", "")
OPENBAO_SECRET_ID = os.environ.get("OPENBAO_SECRET_ID", "")

_VAULT_MOUNT = "secret"
_WAREHOUSE_KEY_TYPE = "WAREHOUSE_PASSWORD"
_DB_CONNECTOR_KEY_TYPE = "DB_CONNECTOR_PASSWORD"

_vault_client: "hvac.Client | None" = None


def _vault_path(tenant_id: str, key_name: str) -> str:
    """Mirrors auth_core._vault_path() exactly — same KV v2 layout."""
    return f"tenants/{tenant_id}/{key_name}"


def _get_vault() -> "hvac.Client | None":
    global _vault_client
    if not OPENBAO_ROLE_ID or not OPENBAO_SECRET_ID:
        logger.warning("OPENBAO_ROLE_ID/OPENBAO_SECRET_ID not configured — cannot resolve warehouse secrets")
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


async def _resolve_vault_password(tenant_id: str, secret_id: str, key_type: str) -> "str | None":
    """Returns the plaintext password, or None if the secret doesn't exist,
    isn't active, doesn't match `key_type`, or OpenBao is unreachable —
    callers treat None the same as "no password configured" (fails the
    eventual DB auth cleanly downstream, doesn't crash config load)."""
    from libs.database_connector.postgres_connector import client as pg_client

    if not pg_client.is_connected():
        logger.warning("postgres not connected — cannot resolve secret %s", secret_id)
        return None

    try:
        async with pg_client.get_client(tenant_id=tenant_id) as session:
            row = (await session.execute(
                text(
                    """SELECT key_name, key_type, is_active FROM secrets_vault
                       WHERE id = :id AND tenant_id = :tenant_id"""
                ),
                {"id": secret_id, "tenant_id": tenant_id},
            )).fetchone()
    except Exception as e:
        logger.warning("secrets_vault lookup failed for %s: %s", secret_id, e)
        return None

    if not row or not row.is_active or row.key_type != key_type:
        logger.warning(
            "secret %s not found/inactive/wrong-type (expected=%s) for tenant=%s", secret_id, key_type, tenant_id
        )
        return None

    vault = _get_vault()
    if vault is None:
        return None

    path = _vault_path(tenant_id, row.key_name)
    try:
        resp = await asyncio.to_thread(
            vault.secrets.kv.v2.read_secret_version,
            mount_point=_VAULT_MOUNT, path=path, raise_on_deleted_version=True,
        )
        return cast("str | None", resp["data"]["data"].get("value"))
    except Exception as e:
        logger.warning("failed to read secret %s from vault path=%s: %s", secret_id, path, e)
        return None


async def resolve_snowflake_password(tenant_id: str, secret_id: str) -> "str | None":
    return await _resolve_vault_password(tenant_id, secret_id, _WAREHOUSE_KEY_TYPE)


async def resolve_db_connector_password(tenant_id: str, secret_id: str) -> "str | None":
    return await _resolve_vault_password(tenant_id, secret_id, _DB_CONNECTOR_KEY_TYPE)
