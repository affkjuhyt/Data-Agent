"""Caller authentication for indicator-api.

Thin binding over backend/libs/shared/auth so the trust decision is the same as
in every other service. INDICATOR_API_ALLOWED_SERVICES defaults to nothing: every
route is reached by a browser through Kong until a service caller is named.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException

from libs.shared.auth import make_get_current_user, require_tenant
from services.indicator_api.domains.errors import DomainError, ErrorCode

get_current_user = make_get_current_user("INDICATOR_API_ALLOWED_SERVICES")


def current_tenant(caller: dict[str, Any] = Depends(get_current_user)) -> str:
    """The caller's tenant, or ACCESS_DENIED from the shared error list."""
    try:
        return require_tenant(caller)
    except HTTPException as e:
        raise DomainError(ErrorCode.ACCESS_DENIED, str(e.detail)) from e
