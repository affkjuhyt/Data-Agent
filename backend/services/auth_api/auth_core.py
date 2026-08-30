"""Backward-compatible re-exports for auth_api internals.

New code should import from role-specific modules under:
- services.auth_api.infrastructure
- services.auth_api.domain
- services.auth_api.services
- services.auth_api.lifecycle
"""

import asyncio
import ipaddress
import os
import secrets
import string
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import bcrypt
import httpx
import hvac
import psycopg2
import psycopg2.extras
from fastapi import Depends, Header, HTTPException, Path, Query
from psycopg2 import pool as pg_pool

from libs.shared.auth import get_current_user, require_role
try:
    from services.auth_api.domain.api_keys import (
        SCOPE_PREFIX,
        VALID_SCOPES,
        format_api_key as _fmt_key,
        generate_api_key as _gen_key,
        hash_api_key as _hash_key,
    )
    from services.auth_api.domain.ip_allowlists import format_ip_allowlist as _fmt_ip
    from services.auth_api.domain.secrets import (
        TENANT_MANAGEABLE_KEY_TYPES,
        TRANSIT_ALGORITHM_MAP as _ALGO_TO_TRANSIT,
        TRANSIT_KEY_TYPES as _TRANSIT_TYPES,
        TRANSIT_MOUNT as _TRANSIT_MOUNT,
        VALID_KEY_TYPES,
        VAULT_MOUNT as _VAULT_MOUNT,
        build_transit_key_name as _transit_key_name,
        build_vault_path as _vault_path,
        format_secret as _fmt_secret,
    )
    from services.auth_api.infrastructure.config import (
        KONG_ADMIN,
        OPENBAO_ADDR,
        OWNER_POOL_MAX as _OWNER_POOL_MAX,
        PG_DSN,
        POOL_MAX,
        POOL_MIN,
        PRIVATE_FALLBACK_CIDRS as _PRIVATE_FALLBACK,
        log,
    )
    from services.auth_api.infrastructure.db import (
        PooledConnection as _PooledConn,
        ensure_settings_table as _ensure_settings_table,
        get_db,
        get_owner_pool as _get_owner_pool,
        owner_pool as _owner_pool,
        owner_pool_lock as _owner_pool_lock,
    )
    from services.auth_api.infrastructure.vault import (
        OPENBAO_TOKEN,
        TOKEN_FILE as _TOKEN_FILE,
        get_vault,
        load_openbao_token as _load_openbao_token,
    )
    from services.auth_api.lifecycle import startup
    from services.auth_api.services.governance import (
        GOVERNANCE_KEYS as _GOV_KEYS,
        enforce_vault_not_in_panic_mode as _vault_panic_check,
        get_governance_setting as _get_gov_setting,
    )
    from services.auth_api.services.kong_sync import (
        IP_PROTECTED_ROUTES as _IP_PROTECTED_ROUTES,
        IP_PROTECTED_SERVICES as _IP_PROTECTED_SERVICES,
        get_active_cidrs as _get_active_cidrs,
        get_ip_allowlist_mode as _get_ip_mode,
        sync_kong_ip_restrictions as _sync_kong,
        upsert_ip_restriction_plugin as _upsert_ip_plugin,
    )
    from services.auth_api.services.rotation import (
        auto_rotation_loop as _auto_rotation_loop,
        run_auto_rotation as _run_auto_rotation,
    )
except ModuleNotFoundError:
    from auth_api.domain.api_keys import (
        SCOPE_PREFIX,
        VALID_SCOPES,
        format_api_key as _fmt_key,
        generate_api_key as _gen_key,
        hash_api_key as _hash_key,
    )
    from auth_api.domain.ip_allowlists import format_ip_allowlist as _fmt_ip
    from auth_api.domain.secrets import (
        TENANT_MANAGEABLE_KEY_TYPES,
        TRANSIT_ALGORITHM_MAP as _ALGO_TO_TRANSIT,
        TRANSIT_KEY_TYPES as _TRANSIT_TYPES,
        TRANSIT_MOUNT as _TRANSIT_MOUNT,
        VALID_KEY_TYPES,
        VAULT_MOUNT as _VAULT_MOUNT,
        build_transit_key_name as _transit_key_name,
        build_vault_path as _vault_path,
        format_secret as _fmt_secret,
    )
    from auth_api.infrastructure.config import (
        KONG_ADMIN,
        OPENBAO_ADDR,
        OWNER_POOL_MAX as _OWNER_POOL_MAX,
        PG_DSN,
        POOL_MAX,
        POOL_MIN,
        PRIVATE_FALLBACK_CIDRS as _PRIVATE_FALLBACK,
        log,
    )
    from auth_api.infrastructure.db import (
        PooledConnection as _PooledConn,
        ensure_settings_table as _ensure_settings_table,
        get_db,
        get_owner_pool as _get_owner_pool,
        owner_pool as _owner_pool,
        owner_pool_lock as _owner_pool_lock,
    )
    from auth_api.infrastructure.vault import (
        OPENBAO_TOKEN,
        TOKEN_FILE as _TOKEN_FILE,
        get_vault,
        load_openbao_token as _load_openbao_token,
    )
    from auth_api.lifecycle import startup
    from auth_api.services.governance import (
        GOVERNANCE_KEYS as _GOV_KEYS,
        enforce_vault_not_in_panic_mode as _vault_panic_check,
        get_governance_setting as _get_gov_setting,
    )
    from auth_api.services.kong_sync import (
        IP_PROTECTED_ROUTES as _IP_PROTECTED_ROUTES,
        IP_PROTECTED_SERVICES as _IP_PROTECTED_SERVICES,
        get_active_cidrs as _get_active_cidrs,
        get_ip_allowlist_mode as _get_ip_mode,
        sync_kong_ip_restrictions as _sync_kong,
        upsert_ip_restriction_plugin as _upsert_ip_plugin,
    )
    from auth_api.services.rotation import (
        auto_rotation_loop as _auto_rotation_loop,
        run_auto_rotation as _run_auto_rotation,
    )


async def _sync_kong_on_startup() -> None:
    await asyncio.sleep(15)
    try:
        await asyncio.to_thread(_sync_kong, "")
        log.info("startup_kong_sync_done")
    except Exception as exc:
        log.warning("startup_kong_sync_failed", error=str(exc))


__all__ = [
    "Any",
    "Optional",
    "Depends",
    "Header",
    "HTTPException",
    "Path",
    "Query",
    "bcrypt",
    "datetime",
    "get_current_user",
    "get_db",
    "get_vault",
    "httpx",
    "hvac",
    "ipaddress",
    "KONG_ADMIN",
    "log",
    "OPENBAO_ADDR",
    "OPENBAO_TOKEN",
    "os",
    "PG_DSN",
    "POOL_MAX",
    "POOL_MIN",
    "psycopg2",
    "pg_pool",
    "require_role",
    "secrets",
    "string",
    "threading",
    "timedelta",
    "timezone",
    "VALID_KEY_TYPES",
    "VALID_SCOPES",
    "TENANT_MANAGEABLE_KEY_TYPES",
    "SCOPE_PREFIX",
    "_ALGO_TO_TRANSIT",
    "_auto_rotation_loop",
    "_ensure_settings_table",
    "_fmt_ip",
    "_fmt_key",
    "_fmt_secret",
    "_gen_key",
    "_get_active_cidrs",
    "_get_gov_setting",
    "_get_ip_mode",
    "_get_owner_pool",
    "_GOV_KEYS",
    "_hash_key",
    "_IP_PROTECTED_ROUTES",
    "_IP_PROTECTED_SERVICES",
    "_load_openbao_token",
    "_OWNER_POOL_MAX",
    "_owner_pool",
    "_owner_pool_lock",
    "_PooledConn",
    "_PRIVATE_FALLBACK",
    "_run_auto_rotation",
    "_sync_kong",
    "_sync_kong_on_startup",
    "_TOKEN_FILE",
    "_TRANSIT_MOUNT",
    "_TRANSIT_TYPES",
    "_transit_key_name",
    "_upsert_ip_plugin",
    "_VAULT_MOUNT",
    "_vault_panic_check",
    "_vault_path",
    "startup",
]
