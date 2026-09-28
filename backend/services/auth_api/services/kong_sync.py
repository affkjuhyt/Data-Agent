from typing import Any

import httpx

try:
    from services.auth_api.infrastructure.config import KONG_ADMIN, PRIVATE_FALLBACK_CIDRS, log
    from services.auth_api.infrastructure.db import get_db
except ModuleNotFoundError:
    from auth_api.infrastructure.config import KONG_ADMIN, PRIVATE_FALLBACK_CIDRS, log
    from auth_api.infrastructure.db import get_db

IP_PROTECTED_SERVICES = ["aeroflow-backend"]
IP_PROTECTED_ROUTES = ["auth_api-keys-route", "auth_api-secrets-route"]


def get_ip_allowlist_mode() -> str:
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM platform_settings WHERE key = 'ip_allowlist_mode'")
            row = cur.fetchone()
            return row[0] if row else "allow_all"
    finally:
        conn.close()


def get_active_cidrs(tenant_id: str) -> list[str]:
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT cidr FROM ip_allowlists WHERE tenant_id = %s AND is_active = true",
                (tenant_id,),
            )
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def upsert_ip_restriction_plugin(scope_url: str, payload: dict[str, Any]) -> None:
    response = httpx.get(scope_url, timeout=5)
    existing = [plugin for plugin in response.json().get("data", []) if plugin.get("name") == "ip-restriction"]
    if existing:
        httpx.patch(f"{KONG_ADMIN}/plugins/{existing[0]['id']}", json=payload, timeout=5)
    else:
        httpx.post(scope_url, json=payload, timeout=5)


def sync_kong_ip_restrictions(tenant_id: str) -> None:
    mode = get_ip_allowlist_mode()
    if mode == "allow_all":
        allow = ["0.0.0.0/0", "::/0"]
    else:
        user_cidrs = get_active_cidrs(tenant_id)
        allow = user_cidrs if user_cidrs else PRIVATE_FALLBACK_CIDRS

    payload = {"name": "ip-restriction", "config": {"allow": allow}}
    try:
        for service_name in IP_PROTECTED_SERVICES:
            upsert_ip_restriction_plugin(f"{KONG_ADMIN}/services/{service_name}/plugins", payload)
        for route_name in IP_PROTECTED_ROUTES:
            upsert_ip_restriction_plugin(f"{KONG_ADMIN}/routes/{route_name}/plugins", payload)
    except Exception as exc:
        log.warning("kong_sync_failed", error=str(exc))
        return

    log.info("kong_sync", mode=mode, cidr_count=len(allow))
