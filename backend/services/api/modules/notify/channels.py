"""How an external channel proves who is calling, and how a binding proves its address.

A1.2: a `channel` endpoint is called by Teams, Zalo, email or the mobile app through the channel
gateway, signed with HMAC instead of a user token. The scheme: header X-Signature
`sha256=<hex>` over `<X-Timestamp>.<raw body>` with the channel's secret; a timestamp more than
MAX_SKEW from now is refused, so a captured call cannot be replayed later.

The secrets come from configuration (OpenBao → env at deploy, CLAUDE.md §6) through
`ChannelSecrets`; tests override the `channel_secrets` dependency.

Binding verification (PUT /v1/me/channels/{channel}) needs a one-time code and the table has no
column for it, so the code is derived, not stored: HMAC of (binding id, address, issue time) with
the verification secret, valid CODE_TTL after the binding's updated_at. The gateway that sends the
code derives it with `verification_code` and the same secret.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import os
import time
import uuid
from dataclasses import dataclass
from typing import Annotated, Protocol

from fastapi import Depends, Path, Request

from services.api.kernel.problems import Problem

CHANNELS = ("teams", "zalo", "email", "mobile_push")
SIGNATURE_HEADER = "X-Signature"
TIMESTAMP_HEADER = "X-Timestamp"
MAX_SKEW = 300  # seconds
CODE_TTL = datetime.timedelta(minutes=15)
CALLBACK_ENV = "NOTIFY_CHANNEL_SECRET_"
VERIFICATION_ENV = "NOTIFY_VERIFICATION_SECRET"


class ChannelSecrets(Protocol):
    def callback_secret(self, kind: str) -> bytes | None:
        """The HMAC key the channel signs callbacks with; None when not configured."""
        ...

    def verification_secret(self) -> bytes | None:
        """The key binding codes are derived with; None when not configured."""
        ...


class EnvChannelSecrets:
    def callback_secret(self, kind: str) -> bytes | None:
        value = os.environ.get(CALLBACK_ENV + kind.upper())
        return value.encode() if value else None

    def verification_secret(self) -> bytes | None:
        value = os.environ.get(VERIFICATION_ENV)
        return value.encode() if value else None


def channel_secrets() -> ChannelSecrets:
    return EnvChannelSecrets()


Secrets = Annotated[ChannelSecrets, Depends(channel_secrets)]


def sign(secret: bytes, timestamp: str, body: bytes) -> str:
    return (
        "sha256=" + hmac.new(secret, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    )


def _unauthenticated(detail: str) -> Problem:
    return Problem(401, "unauthenticated", detail)


@dataclass(frozen=True)
class VerifiedChannel:
    kind: str


async def verify_channel(
    request: Request, kind: Annotated[str, Path()], secrets: Secrets
) -> VerifiedChannel:
    """The `authenticate=` of POST /v1/channels/{kind}/callback: a valid, fresh signature."""
    if kind not in CHANNELS:
        raise Problem(404, "not_found", "Unknown channel.")
    secret = secrets.callback_secret(kind)
    if not secret:
        # Never accept an unsigned call because the secret is missing.
        raise _unauthenticated("The channel is not configured.")
    signature = request.headers.get(SIGNATURE_HEADER, "")
    stamp = request.headers.get(TIMESTAMP_HEADER, "")
    try:
        skew = abs(time.time() - int(stamp))
    except ValueError as exc:
        raise _unauthenticated("Missing or malformed timestamp.") from exc
    if skew > MAX_SKEW:
        raise _unauthenticated("Stale timestamp.")
    expected = sign(secret, stamp, await request.body())
    if not hmac.compare_digest(signature.encode(), expected.encode()):
        raise _unauthenticated("Invalid signature.")
    return VerifiedChannel(kind=kind)


def verification_code(
    secret: bytes, binding_id: uuid.UUID, address: str, issued_at: datetime.datetime
) -> str:
    """Six digits for one binding, address and issue time."""
    message = f"{binding_id}|{address}|{issued_at.astimezone(datetime.UTC).isoformat()}"
    digest = hmac.new(secret, message.encode(), hashlib.sha256).digest()
    return f"{int.from_bytes(digest[:4], 'big') % 1_000_000:06d}"


def code_matches(
    secret: bytes,
    binding_id: uuid.UUID,
    address: str,
    issued_at: datetime.datetime,
    code: str,
    now: datetime.datetime,
) -> bool:
    if now - issued_at > CODE_TTL:
        return False
    expected = verification_code(secret, binding_id, address, issued_at)
    return hmac.compare_digest(code.encode(), expected.encode())
