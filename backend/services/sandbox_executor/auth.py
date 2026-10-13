"""Authentication for the private sandbox execution API."""

from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException

from libs.shared.service_identity import (
    ServiceAssertionError,
    get_secret,
    verify_service_assertion,
)


def require_service_assertion(token: str) -> dict[str, Any]:
    """Verify the caller before accepting any execution or polling request."""
    allowed = {
        item.strip()
        for item in os.environ.get("SANDBOX_ALLOWED_SERVICES", "worker-agent").split(",")
        if item.strip()
    }
    try:
        return verify_service_assertion(token, secret=get_secret(), allowed_services=allowed)
    except ServiceAssertionError as exc:
        raise HTTPException(status_code=401, detail="valid service assertion required") from exc
