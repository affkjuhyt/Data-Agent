"""A1.8 "Mọi lời gọi ghi vào audit.audit_log": one row per /v1 call, written by the kernel once
the response status is known, so no module has to remember it.

The row names the caller (the resolved app_user, or the token subject's organisation when the
route refused it before resolving anyone), the operation (its A2 operation id, `api.<id>`), the
object when the path names one, and the outcome: ok for 2xx/3xx, denied for 401/403/429 (A1.8:
"lời gọi bị chặn ghi outcome = denied"), error otherwise. Bodies are never logged: before/after
belong to the modules that know what is personal data (BR-138).

The row is appended in its own svc_audit transaction in the audit database after the handler
ran; the chain trigger serialises inserts per organisation (BR-47). A failure to write is logged
and does not fail the call, which has already happened: the modules that must not act unaudited
(audit, integration's /mcp) write their own rows inside the action instead.
"""

from __future__ import annotations

import ipaddress
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import Request, Response

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import audit
from services.api.kernel.auth import Principal, bearer_claims

logger = logging.getLogger(__name__)

ROLE = "svc_audit"
SERVICE = "api"


def _outcome(status: int) -> Literal["ok", "denied", "error"]:
    if status < 400:
        return "ok"
    if status in (401, 403, 429):
        return "denied"
    return "error"


def _object_id(request: Request) -> uuid.UUID | None:
    raw = request.scope.get("path_params", {}).get("id")
    try:
        return uuid.UUID(str(raw)) if raw is not None else None
    except ValueError:
        return None


def _ip(request: Request) -> str | None:
    """The caller's address as the gateway saw it.

    Kong appends the peer it accepted the connection from to X-Forwarded-For, so the last entry
    is the one a client cannot forge; anything before it is whatever the client sent. A value that
    is not an address is dropped rather than written: inet would refuse it, and a refused insert
    would lose the row."""
    forwarded = request.headers.get("X-Forwarded-For")
    candidate = (
        forwarded.split(",")[-1].strip()
        if forwarded
        else (request.client.host if request.client else None)
    )
    try:
        return str(ipaddress.ip_address(candidate)) if candidate else None
    except ValueError:
        return None


async def access_log_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    if not request.url.path.startswith("/v1/"):
        return response
    principal: Principal | None = getattr(request.state, "principal", None)
    if principal is not None:
        org_id, actor_id = principal.org_id, principal.user_id
        actor_kind: Literal["user", "client"] = "client" if principal.client_id else "user"
        data_space_id = principal.data_space_id
    else:
        claims = await bearer_claims(request)
        try:
            org_id = uuid.UUID(str((claims or {})["org_id"]))
        except (KeyError, ValueError):
            # No organisation to file it under (no or a foreign token): RLS has nowhere to put
            # the row, and the route already answered 401.
            return response
        actor_id, actor_kind, data_space_id = None, "user", None
    route = request.scope.get("route")
    # Only names the api chose: a path that matched no route is the caller's own text, which must
    # not reach the action column or the application log.
    operation = getattr(route, "operation_id", None) or "unmatched"
    try:
        async with scoped_session(ROLE, org_id, actor_id) as session:
            session.add(
                audit.AuditLog(
                    org_id=org_id,
                    # seq, prev_hash and hash are the chain trigger's.
                    seq=0,
                    prev_hash="",
                    hash="",
                    actor_kind=actor_kind,
                    actor_id=actor_id,
                    data_space_id=data_space_id,
                    service=SERVICE,
                    action=f"api.{operation}",
                    object_kind=None,
                    object_id=_object_id(request),
                    outcome=_outcome(response.status_code),
                    after={"method": request.method, "status": response.status_code},
                    ip=_ip(request),
                )
            )
    except Exception:
        logger.exception("api | access log not written for %s", operation)
    return response
