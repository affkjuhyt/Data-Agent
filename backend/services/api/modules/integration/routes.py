"""A2 group Tích hợp (UC-12-01, UC-12-03, UC-12-04): API clients and webhooks.

An api_client is a Keycloak service-account client (ADR-30); creating, rotating and revoking it
go through the ClientCredentials port, and only the OpenBao path of its secret is stored
(secret_ref). Both secrets this group hands out (a client's, a webhook's signing key) appear in
one response and nowhere else (A3). Revoking flips status in the same transaction that disables
the Keycloak client, and the MCP check reads that status on every call, so a token still in its
lifetime is refused at once (AC-12-01-03).

Deliveries are made by a worker; this group records subscriptions and asks for retries.
POST /mcp lives in mcp.py.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import integration as ig
from libs.dataagent_db.models import org
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import (
    Page,
    PageRequest,
    encode_page_token,
    invalid_page_token,
    page_request,
)
from services.api.kernel.problems import Problem
from services.api.modules.integration import rules
from services.api.modules.integration.ports import (
    ClientCredentials,
    SigningKeys,
    credentials,
    signing_keys,
)
from services.api.modules.integration.schemas import (
    Accepted,
    ApiClientCreate,
    ApiClientIssued,
    ApiClientPatch,
    ApiClientView,
    Revoke,
    UsageRow,
    WebhookCreate,
    WebhookDeliveryView,
    WebhookIssued,
    WebhookView,
)

router = APIRouter()
ROLE = "svc_integration"

PathId = Annotated[uuid.UUID, Path(alias="id")]
Credentials = Annotated[ClientCredentials, Depends(credentials)]
Keys = Annotated[SigningKeys, Depends(signing_keys)]


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _emit(
    session: AsyncSession,
    principal: Principal,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    event_type: str,
    **payload: Any,
) -> None:
    session.add(
        ig.Outbox(
            id=uuid7(),
            org_id=principal.org_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload={k: str(v) if isinstance(v, uuid.UUID) else v for k, v in payload.items()},
        )
    )


async def _client(
    session: AsyncSession, client_id: uuid.UUID, *, lock: bool = False
) -> ig.ApiClient:
    query = select(ig.ApiClient).where(ig.ApiClient.id == client_id)
    row = await session.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise Problem(404, "not_found", "No such client.")
    return row


async def _check_owner(session: AsyncSession, owner_id: uuid.UUID) -> None:
    found = await session.scalar(
        select(org.AppUser.id).where(
            org.AppUser.id == owner_id,
            org.AppUser.status == "active",
            org.AppUser.kind == "human",
        )
    )
    if found is None:
        raise rules.invalid(
            "owner_user_id", "owner_user_id is not an active person of the organisation."
        )


async def _check_spaces(session: AsyncSession, ids: list[uuid.UUID]) -> list[uuid.UUID]:
    wanted = sorted(set(ids))
    if not wanted:
        return []
    found = set(
        (
            await session.scalars(
                select(org.DataSpace.id).where(
                    org.DataSpace.id.in_(wanted), org.DataSpace.deleted_at.is_(None)
                )
            )
        ).all()
    )
    missing = [str(i) for i in wanted if i not in found]
    if missing:
        raise rules.invalid("data_space_ids", f"Unknown Data Spaces: {', '.join(missing[:3])}.")
    return wanted


@router.post(
    "/v1/admin/clients",
    status_code=201,
    response_model=ApiClientIssued,
    **endpoint("POST", "/v1/admin/clients", operation_id="createApiClient"),
)
async def create_api_client(
    body: ApiClientCreate, principal: CurrentPrincipal, creds: Credentials
) -> ApiClientIssued:
    tools = rules.check_tools(body.tools, body.identity_mode)
    now = _now()
    expires_at = rules.check_expiry(body.expires_at, now)
    async with transaction(ROLE, principal) as session:
        await _check_owner(session, body.owner_user_id)
        spaces = await _check_spaces(session, body.data_space_ids)
        # The Keycloak client exists before the row commits; if the insert then fails, the
        # orphan has no row and so no org, tools or status: the MCP check refuses its tokens.
        issued = await creds.create(org_id=principal.org_id, name=body.name.strip())
        row = ig.ApiClient(
            id=uuid7(),
            org_id=principal.org_id,
            name=body.name.strip(),
            kind=body.kind,
            identity_mode=body.identity_mode,
            owner_user_id=body.owner_user_id,
            keycloak_client_id=issued.keycloak_client_id,
            secret_ref=issued.secret_ref,
            tools=tools,
            data_space_ids=spaces,
            quota_per_minute=body.quota_per_minute,
            quota_per_day=body.quota_per_day,
            expires_at=expires_at,
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(row)
        await session.flush()
        await session.refresh(row)
        _emit(session, principal, "api_client", row.id, "api_client.created", kind=row.kind)
        view = ApiClientView.model_validate(row)
    return ApiClientIssued(**view.model_dump(), client_secret=issued.secret)


@router.patch(
    "/v1/admin/clients/{id}",
    response_model=ApiClientView,
    **endpoint("PATCH", "/v1/admin/clients/{id}", operation_id="patchApiClient"),
)
async def patch_api_client(
    body: ApiClientPatch, client_id: PathId, principal: CurrentPrincipal
) -> ApiClientView:
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        if value is None:
            raise rules.invalid(field, f"{field} cannot be null.")
    async with transaction(ROLE, principal) as session:
        row = await _client(session, client_id, lock=True)
        if row.status in ("revoked", "expired"):
            raise Problem(409, "invalid_state", f"A {row.status} client cannot be changed.")
        if "tools" in changes:
            changes["tools"] = rules.check_tools(changes["tools"], row.identity_mode)
        if "data_space_ids" in changes:
            changes["data_space_ids"] = await _check_spaces(session, changes["data_space_ids"])
        if "expires_at" in changes:
            rules.check_expiry(changes["expires_at"], _now(), granted_at=row.created_at)
        for field, value in changes.items():
            setattr(row, field, value)
        row.updated_by, row.updated_at = principal.user_id, _now()
        await session.flush()
        await session.refresh(row)
        _emit(
            session, principal, "api_client", row.id, "api_client.updated", changed=sorted(changes)
        )
        return ApiClientView.model_validate(row)


@router.post(
    "/v1/admin/clients/{id}:rotate-secret",
    response_model=ApiClientIssued,
    **endpoint(
        "POST", "/v1/admin/clients/{id}:rotate-secret", operation_id="rotateApiClientSecret"
    ),
)
async def rotate_secret(
    client_id: PathId, principal: CurrentPrincipal, creds: Credentials
) -> ApiClientIssued:
    async with transaction(ROLE, principal) as session:
        row = await _client(session, client_id, lock=True)
        if row.status not in ("active", "paused"):
            raise Problem(409, "invalid_state", f"A {row.status} client has no secret to rotate.")
        secret = await creds.rotate(
            org_id=principal.org_id,
            keycloak_client_id=row.keycloak_client_id,
            secret_ref=row.secret_ref,
        )
        row.secret_rotated_at = row.updated_at = _now()
        row.updated_by = principal.user_id
        await session.flush()
        await session.refresh(row)
        _emit(session, principal, "api_client", row.id, "api_client.secret_rotated")
        view = ApiClientView.model_validate(row)
    return ApiClientIssued(**view.model_dump(), client_secret=secret)


@router.post(
    "/v1/admin/clients/{id}:revoke",
    response_model=ApiClientView,
    **endpoint("POST", "/v1/admin/clients/{id}:revoke", operation_id="revokeApiClient"),
)
async def revoke_client(
    body: Revoke, client_id: PathId, principal: CurrentPrincipal, creds: Credentials
) -> ApiClientView:
    async with transaction(ROLE, principal) as session:
        row = await _client(session, client_id, lock=True)
        if row.status == "revoked":
            return ApiClientView.model_validate(row)
        await creds.revoke(org_id=principal.org_id, keycloak_client_id=row.keycloak_client_id)
        row.status = "revoked"
        row.revoked_at = row.updated_at = _now()
        row.updated_by = principal.user_id
        await session.flush()
        await session.refresh(row)
        _emit(
            session,
            principal,
            "api_client",
            row.id,
            "api_client.revoked",
            reason=body.reason.strip(),
        )
        return ApiClientView.model_validate(row)


@router.get(
    "/v1/admin/clients/{id}/usage",
    response_model=Page[UsageRow],
    **endpoint("GET", "/v1/admin/clients/{id}/usage", operation_id="getApiClientUsage"),
)
async def client_usage(
    client_id: PathId,
    principal: CurrentPrincipal,
    from_: Annotated[datetime.datetime | None, Query(alias="from")] = None,
    to: datetime.datetime | None = None,
    granularity: Literal["minute", "hour", "day"] = "hour",
    tool: Annotated[str | None, Query(max_length=64)] = None,
) -> Page[UsageRow]:
    end = to or _now()
    start = from_ or end - datetime.timedelta(days=1)
    if start >= end:
        raise rules.invalid("from", "from must be before to.")
    uc = ig.UsageCounter
    bucket = func.date_trunc(granularity, uc.minute).label("bucket")
    query = (
        select(
            bucket,
            uc.tool,
            uc.data_space_id,
            func.sum(uc.calls),
            func.sum(uc.errors),
            func.sum(uc.blocked_by_permission),
            func.sum(uc.blocked_by_quota),
            # p95 does not add up; the worst minute of the bucket is the honest bound.
            func.max(uc.p95_ms),
        )
        .where(uc.api_client_id == client_id, uc.minute >= start, uc.minute < end)
        .group_by(bucket, uc.tool, uc.data_space_id)
        .order_by(bucket, uc.tool)
    )
    if tool is not None:
        query = query.where(uc.tool == tool)
    async with transaction(ROLE, principal) as session:
        await _client(session, client_id)
        rows = (await session.execute(query)).all()
    return Page[UsageRow](
        items=[
            UsageRow(
                bucket=r[0],
                tool=r[1],
                data_space_id=r[2],
                calls=int(r[3] or 0),
                errors=int(r[4] or 0),
                blocked_by_permission=int(r[5] or 0),
                blocked_by_quota=int(r[6] or 0),
                p95_ms=r[7],
            )
            for r in rows
        ]
    )


@router.post(
    "/v1/webhooks",
    status_code=201,
    response_model=WebhookIssued,
    **endpoint("POST", "/v1/webhooks", operation_id="createWebhook"),
)
async def create_webhook(
    body: WebhookCreate, principal: CurrentPrincipal, keys: Keys
) -> WebhookIssued:
    event_types = rules.check_event_types(body.event_types)
    target = rules.check_target(body.target_url)
    async with transaction(ROLE, principal) as session:
        client = await _client(session, body.api_client_id)
        if client.status != "active":
            raise Problem(409, "invalid_state", f"A {client.status} client cannot subscribe.")
        subscription_id = uuid7()
        issued = await keys.create(org_id=principal.org_id, subscription_id=subscription_id)
        row = ig.WebhookSubscription(
            id=subscription_id,
            org_id=principal.org_id,
            api_client_id=client.id,
            event_types=event_types,
            target_url=target,
            signing_key_ref=issued.key_ref,
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(row)
        await session.flush()
        await session.refresh(row)
        _emit(
            session,
            principal,
            "webhook_subscription",
            row.id,
            "webhook_subscription.created",
            api_client_id=client.id,
            event_types=event_types,
        )
        view = WebhookView.model_validate(row)
    return WebhookIssued(**view.model_dump(), signing_key=issued.key)


@router.get(
    "/v1/webhooks/{id}/deliveries",
    response_model=Page[WebhookDeliveryView],
    **endpoint("GET", "/v1/webhooks/{id}/deliveries", operation_id="listWebhookDeliveries"),
)
async def list_deliveries(
    subscription_id: PathId,
    principal: CurrentPrincipal,
    page: Annotated[PageRequest, Depends(page_request)],
    status: Annotated[str | None, Query(pattern="^(pending|delivered|retrying|failed)$")] = None,
    from_: Annotated[datetime.datetime | None, Query(alias="from")] = None,
    to: datetime.datetime | None = None,
) -> Page[WebhookDeliveryView]:
    d = ig.WebhookDelivery
    query = (
        select(d)
        .where(d.subscription_id == subscription_id)
        .order_by(d.created_at.desc(), d.id.desc())
        .limit(page.page_size + 1)
    )
    if status is not None:
        query = query.where(d.status == status)
    if from_ is not None:
        query = query.where(d.created_at >= from_)
    if to is not None:
        query = query.where(d.created_at < to)
    if page.cursor:
        try:
            after = (
                datetime.datetime.fromisoformat(str(page.cursor["at"])),
                uuid.UUID(str(page.cursor["id"])),
            )
        except (KeyError, ValueError) as exc:
            raise invalid_page_token() from exc
        query = query.where(tuple_(d.created_at, d.id) < after)
    async with transaction(ROLE, principal) as session:
        if await session.get(ig.WebhookSubscription, subscription_id) is None:
            raise Problem(404, "not_found", "No such webhook.")
        rows = list((await session.scalars(query)).all())
    more = len(rows) > page.page_size
    rows = rows[: page.page_size]
    last = rows[-1] if more else None
    return Page[WebhookDeliveryView](
        items=[WebhookDeliveryView.model_validate(r) for r in rows],
        next_page_token=encode_page_token(
            {"at": last.created_at.isoformat(), "id": str(last.id)} if last else None
        ),
    )


@router.post(
    "/v1/webhook-deliveries/{id}:retry",
    status_code=202,
    response_model=Accepted,
    **endpoint("POST", "/v1/webhook-deliveries/{id}:retry", operation_id="retryWebhookDelivery"),
)
async def retry_delivery(
    delivery_id: PathId, principal: CurrentPrincipal, response: Response
) -> Accepted:
    d = ig.WebhookDelivery
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(select(d).where(d.id == delivery_id).with_for_update())
        if row is None:
            raise Problem(404, "not_found", "No such delivery.")
        if row.status != "failed":
            raise Problem(
                409, "invalid_state", f"Only a failed delivery is retried, not {row.status}."
            )
        # Same event id on the retry, so the receiver can drop a duplicate (AC-12-03-02).
        row.status = "retrying"
        row.next_attempt_at = _now()
        _emit(
            session,
            principal,
            "webhook_delivery",
            row.id,
            "webhook_delivery.retry_requested",
            subscription_id=row.subscription_id,
            event_id=row.event_id,
        )
    response.headers["Location"] = f"/v1/webhook-deliveries/{delivery_id}"
    return Accepted(id=delivery_id, status="retrying")
