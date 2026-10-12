"""Read warehouse credentials from OpenBao using the sandbox AppRole."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def resolve_connection_secret(tenant_id: str, connection_ref: str) -> str | None:
    """Read only `value` from `warehouse/<tenant>/<connection_ref>`."""
    if not tenant_id or not connection_ref or "/" in connection_ref or ".." in connection_ref:
        return None
    try:
        import hvac
    except ImportError:
        logger.error("hvac is not installed; cannot resolve sandbox warehouse credential")
        return None

    role_id = os.environ.get("OPENBAO_ROLE_ID", "")
    secret_id = os.environ.get("OPENBAO_SECRET_ID", "")
    if not role_id or not secret_id:
        logger.warning("OpenBao AppRole is not configured for sandbox warehouse credentials")
        return None

    try:
        client: Any = hvac.Client(url=os.environ.get("OPENBAO_ADDR", "http://openbao:8200"))
        client.auth.approle.login(role_id=role_id, secret_id=secret_id)
        response = client.secrets.kv.v2.read_secret_version(
            mount_point="secret",
            path=f"warehouse/{tenant_id}/{connection_ref}",
            raise_on_deleted_version=True,
        )
        return response["data"]["data"].get("value")
    except Exception as exc:
        logger.warning("failed to read sandbox warehouse secret: %s", exc)
        return None
