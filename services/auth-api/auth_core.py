"""
Auth API — AeroFlow Platform
=============================
Manages IP allowlists, API keys, and Secrets Vault (backed by OpenBao KV v2 + Transit).
Kong injects X-User-Id, X-User-Roles, X-Tenant-Id from validated JWT.

Secret storage model:
  SIGNING_KEY, ENCRYPTION_KEY, HMAC_KEY  → OpenBao Transit engine (key never leaves vault)
  BEARER_TOKEN, MCP_TOKEN, KB_API_KEY    → OpenBao KV v2 (raw value stored)
"""
import asyncio
import ipaddress
import os
import secrets
import string
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import httpx
import hvac
import psycopg2
import psycopg2.extras
from psycopg2 import pool as pg_pool
import structlog
from fastapi import Depends, Header, HTTPException, Path, Query
from shared.auth import get_current_user, require_role

structlog.configure(processors=[structlog.processors.JSONRenderer()])
log = structlog.get_logger()

PG_DSN       = os.environ.get("DATABASE_URL",   "postgresql://aeroflow:aeroflow_secret@postgres:5432/aeroflow")
KONG_ADMIN   = os.environ.get("KONG_ADMIN_URL", "http://kong:8001")
OPENBAO_ADDR = os.environ.get("OPENBAO_ADDR",  "http://openbao:8200")

_TOKEN_FILE = "/openbao/data/.root-token"

def _load_openbao_token() -> str:
    if os.path.exists(_TOKEN_FILE):
        try:
            token = open(_TOKEN_FILE).read().strip()
            if token:
                return token
        except OSError:
            pass
    return os.environ.get("OPENBAO_TOKEN", "aeroflow-dev-token")

OPENBAO_TOKEN = _load_openbao_token()


def get_vault() -> hvac.Client:
    # Re-read token file each call so rotated tokens are picked up without restart
    token = _load_openbao_token()
    return hvac.Client(url=OPENBAO_ADDR, token=token)

# Private-network fallback when allowlist is empty — prevents lockout
_PRIVATE_FALLBACK = ["127.0.0.1/32", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]

# ── Connection pool ─────────────────────────────────────────────────────────────
# Pool the owner-role connections instead of opening a fresh TCP+auth handshake
# on every request (the old per-request psycopg2.connect() both added latency and
# risked exhausting Postgres max_connections under load).
#
# POOL_MAX bounds in-flight owner connections; the route threadpool is capped to
# the same number in startup() so a `def` route thread always finds a free
# connection — psycopg2's pool RAISES "exhausted" rather than waiting, so we must
# never let more threads run than the pool can serve. OWNER_POOL_MAX adds a small
# margin so the background rotation task can still get a connection when routes
# are saturated.
POOL_MIN        = int(os.environ.get("DB_POOL_MIN", "2"))
POOL_MAX        = int(os.environ.get("DB_POOL_MAX", "20"))
_OWNER_POOL_MAX = POOL_MAX + 2

_owner_pool: Optional[pg_pool.ThreadedConnectionPool] = None
_owner_pool_lock = threading.Lock()


def _get_owner_pool() -> pg_pool.ThreadedConnectionPool:
    # Lazy + double-checked: Postgres may not be up at import time.
    global _owner_pool
    if _owner_pool is None:
        with _owner_pool_lock:
            if _owner_pool is None:
                _owner_pool = pg_pool.ThreadedConnectionPool(POOL_MIN, _OWNER_POOL_MAX, PG_DSN)
    return _owner_pool


class _PooledConn:
    """Proxy so existing `conn = get_db(); ...; conn.close()` call sites return the
    connection to the pool instead of really closing it — no call site changes.

    A rollback on release clears any open/aborted transaction so the next borrower
    starts clean: psycopg2's putconn() does NOT reset connection state.
    """

    def __init__(self, pool: pg_pool.ThreadedConnectionPool, conn) -> None:
        object.__setattr__(self, "_pool", pool)
        object.__setattr__(self, "_conn", conn)

    def close(self) -> None:
        conn = object.__getattribute__(self, "_conn")
        pool = object.__getattribute__(self, "_pool")
        try:
            conn.rollback()
        except Exception:
            pass
        pool.putconn(conn)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_conn"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_conn"), name, value)


def get_db():
    raw = _get_owner_pool().getconn()
    raw.autocommit = False
    return _PooledConn(_get_owner_pool(), raw)


def _ensure_settings_table():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS platform_settings (
                    key        varchar(100) PRIMARY KEY,
                    value      text NOT NULL,
                    updated_at timestamptz DEFAULT now()
                )
            """)
            # Add REVEAL to triggered_by constraint (idempotent)
            cur.execute("ALTER TABLE key_rotations DROP CONSTRAINT IF EXISTS key_rotations_triggered_by_check")
            cur.execute("""
                ALTER TABLE key_rotations ADD CONSTRAINT key_rotations_triggered_by_check
                  CHECK (triggered_by IN ('SCHEDULED','MANUAL','PANIC','REVEAL'))
            """)
            # Seed default: allow_all so fresh deployments are not locked out
            cur.execute("""
                INSERT INTO platform_settings (key, value, updated_at)
                VALUES ('ip_allowlist_mode', 'allow_all', now())
                ON CONFLICT (key) DO NOTHING
            """)
            # Add access_reason column to key_rotations (idempotent)
            cur.execute("ALTER TABLE key_rotations ADD COLUMN IF NOT EXISTS access_reason text")
        conn.commit()
    except Exception as e:
        log.warning("schema_migration_failed", error=str(e))
    finally:
        conn.close()


# ── Panic mode guard ──────────────────────────────────────────────────────────

def _vault_panic_check():
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if _get_gov_setting(cur, "vault_panic_mode"):
                raise HTTPException(
                    status_code=503,
                    detail="Vault is in PANIC MODE — all secret access suspended. Disable via governance settings.",
                )
    finally:
        conn.close()


# ── Auto-rotation background task ─────────────────────────────────────────────

def _run_auto_rotation():
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if not _get_gov_setting(cur, "vault_auto_rotation"):
                return
            cur.execute(
                "SELECT id, tenant_id, key_name, key_type, version "
                "FROM secrets_vault WHERE is_active = true AND rotation_due_at < now()"
            )
            overdue = cur.fetchall()
    finally:
        conn.close()

    if not overdue:
        return

    vault = get_vault()
    for row in overdue:
        conn2 = get_db()
        try:
            with conn2.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if row["key_type"] in _TRANSIT_TYPES:
                    t_key = _transit_key_name(row["tenant_id"], row["key_name"])
                    try:
                        vault.secrets.transit.rotate_key(name=t_key, mount_point=_TRANSIT_MOUNT)
                        status, error = "SUCCESS", None
                    except Exception as e:
                        status, error = "FAILED", str(e)
                    new_version = row["version"] + 1
                    rotation_due = datetime.now(timezone.utc) + timedelta(days=90)
                    cur.execute(
                        "UPDATE secrets_vault SET version=%s, rotation_due_at=%s, last_rotated_at=now() WHERE id=%s",
                        (new_version, rotation_due, row["id"]),
                    )
                    cur.execute(
                        "INSERT INTO key_rotations (secret_id, triggered_by, actor_id, old_version, new_version, status, error) "
                        "VALUES (%s,'SCHEDULED','system',%s,%s,%s,%s)",
                        (row["id"], row["version"], new_version, status, error),
                    )
                    log.info("auto_rotation", key=row["key_name"], status=status)
                else:
                    # KV secrets cannot be auto-rotated without a new value — log a warning entry
                    cur.execute(
                        "INSERT INTO key_rotations (secret_id, triggered_by, actor_id, old_version, new_version, status, error) "
                        "VALUES (%s,'SCHEDULED','system',%s,%s,'FAILED',%s)",
                        (row["id"], row["version"], row["version"],
                         "Manual rotation required — KV secrets need a new value"),
                    )
                    log.warning("auto_rotation_skipped_kv", key=row["key_name"])
            conn2.commit()
        except Exception as e:
            conn2.rollback()
            log.error("auto_rotation_error", key=row["key_name"], error=str(e))
        finally:
            conn2.close()


async def _auto_rotation_loop():
    await asyncio.sleep(60)  # wait for startup to settle
    while True:
        try:
            # Offload the blocking psycopg2 + vault work to a thread so it never
            # runs on (and stalls) the event loop.
            await asyncio.to_thread(_run_auto_rotation)
        except Exception as e:
            log.error("auto_rotation_loop_error", error=str(e))
        await asyncio.sleep(86400)  # run every 24 hours


async def _sync_kong_on_startup():
    # Wait for Kong to finish setup before syncing
    await asyncio.sleep(15)
    try:
        # _sync_kong uses blocking httpx + psycopg2 — keep it off the event loop.
        await asyncio.to_thread(_sync_kong, "")
        log.info("startup_kong_sync_done")
    except Exception as e:
        log.warning("startup_kong_sync_failed", error=str(e))

async def startup():
    # Cap the route threadpool to the DB pool size. Sync (`def`) routes run in
    # this threadpool and each borrows one pooled connection; psycopg2's pool
    # RAISES when exhausted, so we must not let more route threads run than the
    # pool can serve. Excess requests then queue at this limiter (which waits)
    # instead of erroring at the pool. Raise DB_POOL_MAX (and Postgres
    # max_connections) together to lift concurrency.
    try:
        import anyio
        anyio.to_thread.current_default_thread_limiter().total_tokens = POOL_MAX
    except Exception as e:
        log.warning("threadpool_limiter_set_failed", error=str(e))
    _ensure_settings_table()
    asyncio.create_task(_auto_rotation_loop())
    asyncio.create_task(_sync_kong_on_startup())



# ── Kong sync ─────────────────────────────────────────────────────────────────

def _get_ip_mode() -> str:
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM platform_settings WHERE key = 'ip_allowlist_mode'")
            row = cur.fetchone()
            return row[0] if row else "allow_all"
    finally:
        conn.close()


def _get_active_cidrs(tenant_id: str) -> list[str]:
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT cidr FROM ip_allowlists WHERE tenant_id = %s AND is_active = true",
                (tenant_id,),
            )
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


# Services and routes that enforce IP restriction.
# auth-api service itself is NOT listed — only the api-keys route is, so
# ip-allowlist management routes remain accessible from any IP.
_IP_PROTECTED_SERVICES = ["aeroflow-backend", "release-worker"]
_IP_PROTECTED_ROUTES   = ["auth-api-keys-route", "auth-api-secrets-route"]


def _upsert_ip_plugin(scope_url: str, payload: dict):
    res = httpx.get(scope_url, timeout=5)
    existing = [p for p in res.json().get("data", []) if p.get("name") == "ip-restriction"]
    if existing:
        httpx.patch(f"{KONG_ADMIN}/plugins/{existing[0]['id']}", json=payload, timeout=5)
    else:
        httpx.post(scope_url, json=payload, timeout=5)


def _sync_kong(tenant_id: str):
    mode = _get_ip_mode()
    if mode == "allow_all":
        allow = ["0.0.0.0/0", "::/0"]
    else:
        user_cidrs = _get_active_cidrs(tenant_id)
        allow = user_cidrs if user_cidrs else _PRIVATE_FALLBACK

    payload = {"name": "ip-restriction", "config": {"allow": allow}}
    try:
        for svc in _IP_PROTECTED_SERVICES:
            _upsert_ip_plugin(f"{KONG_ADMIN}/services/{svc}/plugins", payload)
        for route in _IP_PROTECTED_ROUTES:
            _upsert_ip_plugin(f"{KONG_ADMIN}/routes/{route}/plugins", payload)
        log.info("kong_sync", mode=mode, cidr_count=len(allow))
    except Exception as e:
        log.warning("kong_sync_failed", error=str(e))


# ── Health ────────────────────────────────────────────────────────────────────

def _fmt_ip(r: dict) -> dict:
    return {
        "id":         str(r["id"]),
        "cidr":       r["cidr"],
        "label":      r["label"] or "",
        "is_active":  r["is_active"],
        "created_by": r["created_by"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# API KEYS
# ═══════════════════════════════════════════════════════════════════════════════

SCOPE_PREFIX = {"full_access": "prod", "read_only": "ro", "admin_platform": "adm"}
VALID_SCOPES = set(SCOPE_PREFIX.keys())


def _gen_key(scope: str) -> tuple[str, str]:
    suffix = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
    prefix = f"sk-{SCOPE_PREFIX.get(scope, 'key')}-{''.join(secrets.choice(string.ascii_letters) for _ in range(4))}"
    return f"{prefix}{suffix}", prefix


def _hash_key(raw: str) -> str:
    return bcrypt.hashpw(raw.encode(), bcrypt.gensalt(rounds=12)).decode()

def _fmt_key(r: dict) -> dict:
    def _iso(v): return v.isoformat() if v else None
    return {
        "id":           str(r["id"]),
        "name":         r["name"],
        "key_prefix":   r["key_prefix"],
        "scope":        r["scope"],
        "created_by":   r["created_by"],
        "last_used_at": _iso(r["last_used_at"]),
        "expires_at":   _iso(r["expires_at"]),
        "revoked_at":   _iso(r["revoked_at"]),
        "rotated_from": str(r["rotated_from"]) if r["rotated_from"] else None,
        "created_at":   _iso(r["created_at"]),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SECRETS VAULT (backed by OpenBao KV v2)
# ═══════════════════════════════════════════════════════════════════════════════

VALID_KEY_TYPES = {
    "ENCRYPTION_KEY", "SIGNING_KEY", "HMAC_KEY", "BEARER_TOKEN", "MCP_TOKEN", "KB_API_KEY",
    # Warehouse credentials (e.g. Snowflake) referenced by a canvas node's
    # snowflakeSecretId — see services/workflow-runtime/warehouse_secrets.py.
    # KV v2 raw-value storage like BEARER_TOKEN/MCP_TOKEN/KB_API_KEY (not Transit).
    "WAREHOUSE_PASSWORD",
}
_VAULT_MOUNT   = "secret"
_TRANSIT_MOUNT = "transit"
_GOV_KEYS = ["vault_auto_rotation", "vault_panic_mode", "vault_pii_access_log"]

# Key types managed by Transit engine — private key never leaves OpenBao
_TRANSIT_TYPES = {"SIGNING_KEY", "ENCRYPTION_KEY", "HMAC_KEY"}

# Map algorithm name (UI/DB) → OpenBao Transit key type
_ALGO_TO_TRANSIT: dict[str, str] = {
    "RSA-4096":    "rsa-4096",
    "RSA-2048":    "rsa-2048",
    "EC-P256":     "ecdsa-p256",
    "HMAC-SHA256": "aes256-gcm96",  # OpenBao Transit: hmac ops use aes256-gcm96 key type
    "AES-256":     "aes256-gcm96",
    "ChaCha20":    "chacha20-poly1305",
}


def _vault_path(tenant_id: str, key_name: str) -> str:
    return f"tenants/{tenant_id}/{key_name}"


def _transit_key_name(tenant_id: str, key_name: str) -> str:
    # Transit key names: alphanumeric + dash only, max 63 chars
    safe = key_name.lower().replace("_", "-").replace(" ", "-")
    return f"{tenant_id[:8]}-{safe}"[:63]


def _get_gov_setting(cur, key: str) -> bool:
    cur.execute("SELECT value FROM platform_settings WHERE key = %s", (key,))
    row = cur.fetchone()
    return row["value"].lower() == "true" if row else False


def _fmt_secret(r: dict) -> dict:
    def _iso(v): return v.isoformat() if v else None
    return {
        "id":              str(r["id"]),
        "key_name":        r["key_name"],
        "key_type":        r["key_type"],
        "algorithm":       r["algorithm"] or "",
        "realm":           r["realm"] or "",
        "version":         r["version"],
        "is_active":       r["is_active"],
        "rotation_due_at": _iso(r["rotation_due_at"]),
        "last_rotated_at": _iso(r["last_rotated_at"]),
        "created_by":      r["created_by"] or "",
        "created_at":      _iso(r["created_at"]),
    }

__all__ = [name for name in globals() if not name.startswith("__")]

