"""A2 group "Theo dõi, báo cáo, thông báo", schema notify: the Notification Centre, delivery
preferences, channel bindings and the channel callback (UC-08-06, UC-08-07).

Notifications are written by the Notification service from other modules' events; the api reads
them. Sending a card, a test notification or a binding code is the channel gateway's work: the
endpoints write the event to notify.outbox in the same transaction (D1 rule 8). A decision taken on
a card resolves the HITL request through inbox, which acts on `channel_delivery.acted`.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import inbox, notify
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import (
    Order,
    Page,
    PageRequest,
    keyset,
    next_page,
    order_by,
    page_request,
)
from services.api.kernel.problems import FieldError, Problem
from services.api.modules.notify.channels import (
    Secrets,
    VerifiedChannel,
    code_matches,
    verify_channel,
)
from services.api.modules.notify.schemas import (
    KINDS,
    ChannelBindingView,
    ChannelCallback,
    ChannelKind,
    ChannelPut,
    ChannelRulesView,
    NotificationView,
    PrefsPut,
    PrefView,
    TestNotificationView,
)

router = APIRouter()
ROLE = "svc_notify"
DEFAULT_CHANNELS = ["in_app"]
# HITL requests still waiting for a person (inbox PENDING).
PENDING_HITL = ("open", "reassigned")
# BR-67: a decision may be taken on these channels; email cards only carry a link.
DECIDING_CHANNELS = ("teams", "zalo", "mobile_push")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _emit(
    session: AsyncSession,
    org_id: uuid.UUID,
    table: str,
    row_id: uuid.UUID,
    event_type: str,
    **payload: Any,
) -> None:
    """One notify.outbox row in the caller's transaction (D1 rule 8): ids and states only."""
    session.add(
        notify.Outbox(
            id=uuid7(),
            org_id=org_id,
            aggregate_type=table,
            aggregate_id=row_id,
            event_type=event_type,
            payload={k: str(v) if isinstance(v, uuid.UUID) else v for k, v in payload.items()},
        )
    )


def _invalid(
    field: str, message: str, status: int = 422, code: str = "validation_failed"
) -> Problem:
    return Problem(
        status, code, message, errors=[FieldError(field=field, code="invalid", message=message)]
    )


# ---------------------------------------------------------------- notifications (UC-08-07)


@router.get(
    "/v1/me/notifications",
    response_model=Page[NotificationView],
    **endpoint("GET", "/v1/me/notifications", operation_id="listMyNotifications"),
)
async def list_notifications(
    principal: CurrentPrincipal,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order, Depends(order_by("created_at", "kind", "priority", default="created_at desc"))
    ],
    unread: bool | None = None,
    kind: str | None = None,
) -> Page[NotificationView]:
    n = notify.Notification
    query = select(n).where(n.org_id == principal.org_id, n.user_id == principal.user_id)
    if unread is True:
        query = query.where(n.read_at.is_(None))
    elif unread is False:
        query = query.where(n.read_at.is_not(None))
    if kind:
        query = query.where(n.kind == kind)
    query = keyset(
        query,
        columns={"created_at": n.created_at, "kind": n.kind, "priority": n.priority},
        tiebreak=n.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    return Page(
        items=[NotificationView.model_validate(r, from_attributes=True) for r in rows],
        next_page_token=token,
    )


async def _prefs(session: AsyncSession, principal: Principal) -> Page[PrefView]:
    rows = {
        r.kind: r
        for r in (
            await session.scalars(
                select(notify.NotificationPref).where(
                    notify.NotificationPref.org_id == principal.org_id,
                    notify.NotificationPref.user_id == principal.user_id,
                )
            )
        ).all()
    }
    # UC-08-07 step 1: the whole table of kinds by channels, defaults where nothing was chosen.
    items = [
        PrefView.model_validate(rows[kind], from_attributes=True)
        if kind in rows
        else PrefView(
            id=None,
            kind=kind,
            channels=list(DEFAULT_CHANNELS),
            quiet_hours=None,
            batch_mode="instant",
            created_at=None,
            updated_at=None,
        )
        for kind in KINDS
    ]
    return Page(items=items, next_page_token="")


@router.get(
    "/v1/me/notification-prefs",
    response_model=Page[PrefView],
    **endpoint("GET", "/v1/me/notification-prefs", operation_id="listMyNotificationPrefs"),
)
async def list_prefs(principal: CurrentPrincipal) -> Page[PrefView]:
    async with transaction(ROLE, principal) as session:
        return await _prefs(session, principal)


@router.put(
    "/v1/me/notification-prefs",
    response_model=Page[PrefView],
    **endpoint("PUT", "/v1/me/notification-prefs", operation_id="putMyNotificationPrefs"),
)
async def put_prefs(body: PrefsPut, principal: CurrentPrincipal) -> Page[PrefView]:
    for pref in body.prefs:
        if pref.kind == "hitl" and "in_app" not in pref.channels:
            # UC-08-07 E1: a HITL request always reaches the app (the table's CHECK holds it too).
            raise _invalid(
                "prefs",
                "HITL requests always reach the app: in_app cannot be turned off.",
                code="business_rule_violated",
            )
    at = _now()
    async with transaction(ROLE, principal) as session:
        current = {
            r.kind: r
            for r in (
                await session.scalars(
                    select(notify.NotificationPref)
                    .where(
                        notify.NotificationPref.org_id == principal.org_id,
                        notify.NotificationPref.user_id == principal.user_id,
                    )
                    .with_for_update()
                )
            ).all()
        }
        given = {p.kind for p in body.prefs}
        # PUT replaces the set: a kind left out goes back to the defaults.
        dropped = [r for kind, r in current.items() if kind not in given]
        if dropped:
            await session.execute(
                delete(notify.NotificationPref).where(
                    notify.NotificationPref.id.in_([r.id for r in dropped])
                )
            )
        for pref in body.prefs:
            channels: list[str] = list(dict.fromkeys(pref.channels))
            quiet = pref.quiet_hours.model_dump(by_alias=True) if pref.quiet_hours else None
            row = current.get(pref.kind)
            before = list(row.channels) if row else list(DEFAULT_CHANNELS)
            if row is None:
                row = notify.NotificationPref(
                    id=uuid7(),
                    org_id=principal.org_id,
                    user_id=principal.user_id,
                    kind=pref.kind,
                    created_by=principal.user_id,
                )
                session.add(row)
            elif (row.channels, row.quiet_hours, row.batch_mode) == (
                channels,
                quiet,
                pref.batch_mode,
            ):
                continue
            row.channels, row.quiet_hours, row.batch_mode = channels, quiet, pref.batch_mode
            row.updated_by, row.updated_at = principal.user_id, at
            await session.flush()
            # UC-08-07 step 3: the gateway sends a test notification on each channel just enabled.
            _emit(
                session,
                principal.org_id,
                "notification_pref",
                row.id,
                "notification_pref.updated",
                user_id=principal.user_id,
                kind=row.kind,
                channels=channels,
                added_channels=[c for c in channels if c not in before],
                batch_mode=row.batch_mode,
            )
        for row in dropped:
            _emit(
                session,
                principal.org_id,
                "notification_pref",
                row.id,
                "notification_pref.reset",
                user_id=principal.user_id,
                kind=row.kind,
            )
        await session.flush()
        return await _prefs(session, principal)


@router.post(
    "/v1/me/notification-preferences:test",
    response_model=TestNotificationView,
    **endpoint(
        "POST", "/v1/me/notification-preferences:test", operation_id="testMyNotificationPreferences"
    ),
)
async def test_notification_preferences(principal: CurrentPrincipal) -> TestNotificationView:
    async with transaction(ROLE, principal) as session:
        prefs = await _prefs(session, principal)
        channels = sorted(
            {channel for pref in prefs.items for channel in pref.channels if channel != "in_app"}
            | {"in_app"}
        )
        event_id = uuid7()
        _emit(
            session,
            principal.org_id,
            "notification_test",
            event_id,
            "notification.test_requested",
            user_id=principal.user_id,
            channels=channels,
        )
        await session.flush()
    return TestNotificationView(channels=channels)


@router.get(
    "/v1/channel-rules",
    response_model=ChannelRulesView,
    **endpoint("GET", "/v1/channel-rules", operation_id="getChannelRules"),
)
async def get_channel_rules(principal: CurrentPrincipal) -> ChannelRulesView:
    """Return the policy matrix used to explain each delivery channel to administrators."""
    del principal
    channels = [
        {"key": "in_app", "label": "Trong ứng dụng"},
        {"key": "email", "label": "Email"},
        {"key": "teams", "label": "Microsoft Teams"},
        {"key": "zalo", "label": "Zalo"},
        {"key": "mobile_push", "label": "Thông báo đẩy"},
    ]
    cells = {
        "in_app": {"text": "Luôn gửi", "tone": "success"},
        "email": {"text": "Có thể gửi", "tone": "success"},
        "teams": {"text": "Có thể gửi", "tone": "success"},
        "zalo": {"text": "Cần liên kết", "tone": "warning"},
        "mobile_push": {"text": "Cần liên kết", "tone": "warning"},
    }
    rows = [
        {"kind": kind, "label": kind, "cells": cells, "note": "Phụ thuộc cài đặt cá nhân."}
        for kind in KINDS
    ]
    return ChannelRulesView(
        channels=channels,
        rows=rows,
        principles=[
            {
                "title": "HITL luôn vào ứng dụng",
                "body": "Không thể tắt kênh in_app cho việc cần quyết.",
            },
            {"title": "Kênh ngoài cần liên kết", "body": "Chỉ gửi khi kênh đã được xác minh."},
        ],
    )


# ---------------------------------------------------------------- channel bindings (UC-08-06)


@router.put(
    "/v1/me/channels/{channel}",
    response_model=ChannelBindingView,
    **endpoint("PUT", "/v1/me/channels/{channel}", operation_id="putMyChannelBinding"),
)
async def put_channel(
    body: ChannelPut,
    channel: Annotated[ChannelKind, Path()],
    principal: CurrentPrincipal,
    secrets: Secrets,
) -> ChannelBindingView:
    if body.external_address is None and body.verification_code is None:
        raise _invalid("external_address", "Give external_address, then verification_code.")
    secret = secrets.verification_secret()
    if not secret:
        raise Problem(503, "source_unavailable", "Channel verification is not configured.")
    at = _now()
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(
            select(notify.ChannelBinding)
            .where(
                notify.ChannelBinding.org_id == principal.org_id,
                notify.ChannelBinding.user_id == principal.user_id,
                notify.ChannelBinding.channel == channel,
            )
            .with_for_update()
        )
        new_address = body.external_address is not None and (
            row is None or row.external_address != body.external_address
        )
        if body.verification_code is None or new_address:
            if body.verification_code is not None:
                raise _invalid(
                    "verification_code", "The code belongs to the address it was sent to."
                )
            # First step (or a new address, or a resend): pending, a fresh code goes out.
            if row is None:
                row = notify.ChannelBinding(
                    id=uuid7(),
                    org_id=principal.org_id,
                    user_id=principal.user_id,
                    channel=channel,
                    created_by=principal.user_id,
                )
                session.add(row)
            if body.external_address is not None:
                row.external_address = body.external_address
            row.status, row.verified_at = "pending", None
            row.updated_by, row.updated_at = principal.user_id, at
            await session.flush()
            # The gateway derives the code (channels.verification_code) and sends it to the
            # address; neither the code nor the address travels in the event.
            _emit(
                session,
                principal.org_id,
                "channel_binding",
                row.id,
                "channel_binding.verification_requested",
                binding_id=row.id,
                user_id=principal.user_id,
                channel=channel,
                status=row.status,
                issued_at=row.updated_at.isoformat(),
            )
        else:
            if row is None:
                raise _invalid(
                    "external_address", "No binding to verify; send external_address first."
                )
            if row.status == "revoked":
                raise Problem(
                    409, "invalid_state", "The binding is revoked; bind the address again."
                )
            if row.status == "pending":
                if not code_matches(
                    secret, row.id, row.external_address, row.updated_at, body.verification_code, at
                ):
                    raise _invalid("verification_code", "Wrong or expired verification code.")
                row.status, row.verified_at = "active", at
                row.updated_by, row.updated_at = principal.user_id, at
                _emit(
                    session,
                    principal.org_id,
                    "channel_binding",
                    row.id,
                    "channel_binding.activated",
                    binding_id=row.id,
                    user_id=principal.user_id,
                    channel=channel,
                    status=row.status,
                )
        await session.flush()
        await session.refresh(row)
        return ChannelBindingView.model_validate(row, from_attributes=True)


# ---------------------------------------------------------------- channel callback (UC-08-06)


@router.post(
    "/v1/channels/{kind}/callback",
    status_code=204,
    response_class=Response,
    **endpoint(
        "POST",
        "/v1/channels/{kind}/callback",
        operation_id="channelCallback",
        authenticate=Depends(verify_channel),
    ),
)
async def channel_callback(
    body: ChannelCallback,
    kind: Annotated[ChannelKind, Path()],
    verified: Annotated[VerifiedChannel, Depends(verify_channel)],
) -> Response:
    payload, action = body.payload, body.payload.action
    if kind not in DECIDING_CHANNELS:
        # BR-67: email cards only link to the app; no decision is taken by mail.
        raise Problem(403, "forbidden", "Decisions are not taken on this channel.")
    # No user token here: the signed card names its organisation, and RLS scopes every read to it.
    # There is no user yet either, so this is the session the kernel's transaction() wraps.
    async with scoped_session(ROLE, payload.org_id) as session:
        delivery = await session.scalar(
            select(notify.ChannelDelivery)
            .where(
                notify.ChannelDelivery.id == payload.delivery_id,
                notify.ChannelDelivery.org_id == payload.org_id,
                notify.ChannelDelivery.channel == verified.kind,
            )
            .with_for_update()
        )
        if delivery is None:
            raise Problem(404, "not_found", "Card not found.")
        bound = await session.scalar(
            select(notify.ChannelBinding.id).where(
                notify.ChannelBinding.user_id == delivery.user_id,
                notify.ChannelBinding.channel == verified.kind,
                notify.ChannelBinding.status == "active",
                notify.ChannelBinding.external_address == payload.sender,
            )
        )
        if bound is None:
            # Only the person the card went to, through their verified binding, may act on it.
            raise Problem(403, "forbidden", "The sender is not the card's linked account.")
        if delivery.status in ("acted", "expired") or delivery.hitl_request_id is None:
            raise Problem(409, "invalid_state", "The card takes no action.")
        hitl = await session.scalar(
            select(inbox.HitlRequest).where(inbox.HitlRequest.id == delivery.hitl_request_id)
        )
        if hitl is None:
            raise Problem(404, "not_found", "Card not found.")
        if hitl.status not in PENDING_HITL:
            raise Problem(409, "invalid_state", f"The request is already {hitl.status}.")
        # BR-67: high-risk requests and source conflicts are decided in the app only, and an
        # approval taken outside it is a low-risk one; picking an option (answer) is allowed.
        if hitl.risk == "high" or hitl.kind == "conflict":
            raise Problem(403, "forbidden", "This request is decided in the app.")
        if action.decision != "answer" and hitl.risk != "low":
            raise Problem(403, "forbidden", "Only a low-risk request is approved outside the app.")
        at = _now()
        delivery.status, delivery.acted_at = "acted", at
        delivery.action_taken = action.model_dump(exclude_none=True)
        # inbox resolves the request for the linked user, recording the channel (UC-08-06 step 4);
        # the gateway marks the card's other copies as handled (step 5); audit takes BR-68 from here.
        _emit(
            session,
            payload.org_id,
            "channel_delivery",
            delivery.id,
            "channel_delivery.acted",
            delivery_id=delivery.id,
            hitl_request_id=delivery.hitl_request_id,
            user_id=delivery.user_id,
            channel=verified.kind,
            decision=action.decision,
            option=action.option,
            status=delivery.status,
        )
    return Response(status_code=204)
