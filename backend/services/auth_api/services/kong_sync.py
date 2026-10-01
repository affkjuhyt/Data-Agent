from typing import Any

import httpx
from sqlalchemy import select

from libs.basemodel.services_databaseconnector.postgres_orm.auth_api_orm import (
    AuthPlatformSettingORM,
    IPAllowlistORM,
)
try:
    from services.auth_api.infrastructure.config import KONG_ADMIN, PRIVATE_FALLBACK_CIDRS, log
    from services.auth_api.infrastructure.db import get_session
except ModuleNotFoundError:
    from auth_api.infrastructure.config import KONG_ADMIN, PRIVATE_FALLBACK_CIDRS, log
    from auth_api.infrastructure.db import get_session

IP_PROTECTED_SERVICES = ["aeroflow-backend"]
IP_PROTECTED_ROUTES = ["auth_api-keys-route", "auth_api-secrets-route"]


def get_ip_allowlist_mode() -> str:
    with get_session() as session:
        return (
            session.scalar(
                select(AuthPlatformSettingORM.value).where(
                    AuthPlatformSettingORM.key == "ip_allowlist_mode"
                )
            )
            or "allow_all"
        )


def get_active_cidrs(tenant_id: str) -> list[str]:
    with get_session() as session:
        return list(
            session.scalars(
                select(IPAllowlistORM.cidr).where(
                    IPAllowlistORM.tenant_id == tenant_id,
                    IPAllowlistORM.is_active.is_(True),
                )
            )
        )


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
