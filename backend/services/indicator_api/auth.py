"""Caller identity for indicator-api: the platform's gateway / service-assertion check."""

from __future__ import annotations

from typing import Any

from fastapi import Depends

from libs.shared.auth import make_get_current_user, require_tenant

# Peers allowed to call with a service assertion, comma-separated. Empty: only
# Kong-verified user requests until a service-to-service caller exists.
get_current_user = make_get_current_user("INDICATOR_API_ALLOWED_SERVICES")


def current_tenant(caller: dict[str, Any] = Depends(get_current_user)) -> str:
    return require_tenant(caller)
