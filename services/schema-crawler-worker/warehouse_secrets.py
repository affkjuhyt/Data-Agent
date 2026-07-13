"""Resolves a Snowflake warehouse password referenced by
KBWarehouseConfig.config.connection.secret_id against OpenBao — replaces the
shared SNOWFLAKE_CRAWLER_PASSWORD/.env credential (see
docs/Schema_Crawler_Risk_Remediation_Plan.md #1).

Sync counterpart of services/workflow-runtime/warehouse_secrets.py — this
worker is a synchronous psycopg2/loop-based service (see main.py's module
docstring), so it reads secrets_vault via the caller's already-open psycopg2
cursor and calls hvac directly (hvac itself is a sync client) instead of
routing through asyncio.to_thread like workflow-runtime does.

Reads secrets_vault directly — a second reader of services/auth-api's
existing metadata table, same pattern this worker already uses for
KBWarehouse (see snowflake_metadata.py / main.py's module docstring) instead
of inventing a parallel credential registry.

Uses its own AppRole (OPENBAO_ROLE_ID/OPENBAO_SECRET_ID), separate from
workflow-runtime's — a compromise of one process's credentials doesn't hand
over read access the other process holds, even though both AppRoles are
bound to the same read-only policy. See
infra/openbao/openbao-schema-crawler-approle-setup.sh.
"""
import logging
import os

import hvac

logger = logging.getLogger(__name__)

OPENBAO_ADDR = os.environ.get("OPENBAO_ADDR", "http://openbao:8200")
OPENBAO_ROLE_ID = os.environ.get("OPENBAO_ROLE_ID", "")
OPENBAO_SECRET_ID = os.environ.get("OPENBAO_SECRET_ID", "")
# Explicit rather than relying on hvac's own default (30s) — this call sits
# in the synchronous, sequential crawl_all() loop (main.py), so an
# unreachable/slow OpenBao would otherwise stall every warehouse queued
# behind the one that happened to trigger re-login (AppRole token TTL is 1h
# — see openbao-schema-crawler-approle-setup.sh — so this isn't a one-off:
# it recurs on whichever warehouse is first to resolve a secret after each
# token expiry). See docs/Schema_Crawler_Risk_Remediation_Results.md §3.3.
OPENBAO_TIMEOUT_SECONDS = int(os.environ.get("OPENBAO_TIMEOUT_SECONDS", "10"))

_VAULT_MOUNT = "secret"
_WAREHOUSE_KEY_TYPE = "WAREHOUSE_PASSWORD"

_vault_client: "hvac.Client | None" = None


def _vault_path(tenant_id: str, key_name: str) -> str:
    """Mirrors auth_core._vault_path() / workflow-runtime's warehouse_secrets.py — same KV v2 layout."""
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
    client = hvac.Client(url=OPENBAO_ADDR, timeout=OPENBAO_TIMEOUT_SECONDS)
    client.auth.approle.login(role_id=OPENBAO_ROLE_ID, secret_id=OPENBAO_SECRET_ID)
    _vault_client = client
    return _vault_client


def resolve_snowflake_password(cur, tenant_id: str, secret_id: str) -> "str | None":
    """Returns the plaintext password, or None if the secret doesn't exist,
    isn't active, doesn't match WAREHOUSE_PASSWORD, or OpenBao is
    unreachable — callers (main.py's connection_params()) treat None the
    same as "no password configured", an already-tested "skip this
    warehouse safely" path.

    `cur` is the caller's already-open psycopg2 cursor (same connection/
    transaction as the rest of the crawl) — this lookup doesn't maintain a
    separate Postgres connection pool of its own."""
    cur.execute(
        """SELECT key_name, key_type, is_active FROM secrets_vault WHERE id = %s AND tenant_id = %s""",
        (secret_id, tenant_id),
    )
    row = cur.fetchone()
    if not row or not row[2] or row[1] != _WAREHOUSE_KEY_TYPE:
        logger.warning(
            "resolve_snowflake_password | secret=%s not found/inactive/wrong-type for tenant=%s",
            secret_id, tenant_id,
        )
        return None
    key_name = row[0]

    vault = _get_vault()
    if vault is None:
        return None

    path = _vault_path(tenant_id, key_name)
    try:
        resp = vault.secrets.kv.v2.read_secret_version(
            mount_point=_VAULT_MOUNT, path=path, raise_on_deleted_version=True,
        )
        return resp["data"]["data"].get("value")
    except Exception as e:
        logger.warning("resolve_snowflake_password | failed to read secret=%s path=%s: %s", secret_id, path, e)
        return None
