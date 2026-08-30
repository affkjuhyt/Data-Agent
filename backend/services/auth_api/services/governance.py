from typing import Any

from fastapi import HTTPException
import psycopg2.extras

try:
    from services.auth_api.infrastructure.db import get_db
except ModuleNotFoundError:
    from auth_api.infrastructure.db import get_db

GOVERNANCE_KEYS = [
    "vault_auto_rotation",
    "vault_panic_mode",
    "vault_pii_access_log",
]


def get_governance_setting(cur: Any, key: str) -> bool:
    cur.execute("SELECT value FROM platform_settings WHERE key = %s", (key,))
    row = cur.fetchone()
    return row["value"].lower() == "true" if row else False


def enforce_vault_not_in_panic_mode() -> None:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if get_governance_setting(cur, "vault_panic_mode"):
                raise HTTPException(
                    status_code=503,
                    detail="Vault is in PANIC MODE — all secret access suspended. Disable via governance settings.",
                )
    finally:
        conn.close()
