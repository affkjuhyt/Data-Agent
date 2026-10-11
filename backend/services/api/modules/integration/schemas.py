"""Wire shapes of the Tích hợp group (tab 4 A3)."""

from __future__ import annotations

import datetime
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Body(BaseModel):
    # A field A3 does not name is a client bug, not something to ignore silently.
    model_config = ConfigDict(extra="forbid")


class ApiClientCreate(_Body):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["rest", "mcp"]
    identity_mode: Literal["on_behalf_of_user", "own_identity"] = "on_behalf_of_user"
    owner_user_id: uuid.UUID
    tools: list[str] = Field(default_factory=list, max_length=6)
    data_space_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    quota_per_minute: int = Field(default=60, ge=1, le=10_000)
    quota_per_day: int = Field(default=5_000, ge=1, le=10_000_000)
    expires_at: datetime.datetime


class ApiClientPatch(_Body):
    tools: list[str] | None = Field(default=None, max_length=6)
    data_space_ids: list[uuid.UUID] | None = Field(default=None, max_length=100)
    quota_per_minute: int | None = Field(default=None, ge=1, le=10_000)
    quota_per_day: int | None = Field(default=None, ge=1, le=10_000_000)
    expires_at: datetime.datetime | None = None
    # revoked goes through :revoke (A3), expired is set by the expiry job.
    status: Literal["active", "paused"] | None = None


class Revoke(_Body):
    reason: str = Field(min_length=1, max_length=2_000)


class ApiClientView(BaseModel):
    """ApiClient (A3). The secret is never part of it; secret_ref is an OpenBao path, not a value."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: str
    identity_mode: str
    owner_user_id: uuid.UUID
    keycloak_client_id: str
    tools: list[str]
    data_space_ids: list[uuid.UUID]
    quota_per_minute: int
    quota_per_day: int
    expires_at: datetime.datetime
    status: str
    secret_rotated_at: datetime.datetime | None
    revoked_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ApiClientIssued(ApiClientView):
    """A3: "Phản hồi kèm client_secret một lần duy nhất"."""

    client_secret: str


class UsageRow(BaseModel):
    """One UsageCounter bucket, summed to the requested granularity."""

    bucket: datetime.datetime
    tool: str
    data_space_id: uuid.UUID | None
    calls: int
    errors: int
    blocked_by_permission: int
    blocked_by_quota: int
    p95_ms: int | None


class WebhookCreate(_Body):
    api_client_id: uuid.UUID
    event_types: list[str] = Field(min_length=1, max_length=50)
    target_url: str = Field(min_length=9, max_length=2_000)


class WebhookView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    api_client_id: uuid.UUID
    event_types: list[str]
    target_url: str
    status: str
    consecutive_failures: int
    paused_reason: str | None
    created_at: datetime.datetime


class WebhookIssued(WebhookView):
    """A3: "Phản hồi kèm khoá ký HMAC một lần"."""

    signing_key: str


class WebhookDeliveryView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    subscription_id: uuid.UUID
    event_id: uuid.UUID
    event_type: str
    attempt_count: int
    status: str
    last_status_code: int | None
    last_error: str | None
    next_attempt_at: datetime.datetime | None
    delivered_at: datetime.datetime | None
    created_at: datetime.datetime


class Accepted(BaseModel):
    """A1.6."""

    id: uuid.UUID
    status: str
    poll_after_ms: int = 2_000
