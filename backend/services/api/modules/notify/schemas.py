"""Request and response shapes of the notify module (tab 4 A3, group "Theo dõi, báo cáo, thông báo").

Responses are the table's columns minus org_id, created_by, updated_by (A3 "Trả về").
"""

from __future__ import annotations

import datetime
import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

NotificationKind = Literal[
    "alert", "hitl", "digest", "task_done", "comment", "share", "report", "system"
]
KINDS: tuple[str, ...] = NotificationKind.__args__  # type: ignore[attr-defined]
PrefChannel = Literal["in_app", "email", "teams", "zalo", "mobile_push"]
ChannelKind = Literal["teams", "zalo", "email", "mobile_push"]
BatchMode = Literal["instant", "15m", "daily"]

_CLOCK = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class NotificationView(BaseModel):
    id: uuid.UUID
    data_space_id: uuid.UUID | None
    kind: str
    title: str
    body: str | None
    link: str | None
    source_ref: uuid.UUID | None
    priority: str
    batched_into: uuid.UUID | None
    read_at: datetime.datetime | None
    created_at: datetime.datetime


class QuietHours(BaseModel):
    from_: str = Field(alias="from")
    to: str

    model_config = {"populate_by_name": True}

    @field_validator("from_", "to")
    @classmethod
    def _clock(cls, value: str) -> str:
        if not _CLOCK.match(value):
            raise ValueError("expected HH:MM")
        return value


class PrefIn(BaseModel):
    kind: NotificationKind
    channels: list[PrefChannel]
    quiet_hours: QuietHours | None = None
    batch_mode: BatchMode = "instant"


class PrefsPut(BaseModel):
    prefs: list[PrefIn] = Field(max_length=len(KINDS))

    @model_validator(mode="after")
    def _one_per_kind(self) -> PrefsPut:
        kinds = [p.kind for p in self.prefs]
        if len(kinds) != len(set(kinds)):
            raise ValueError("each kind appears once")
        return self


class PrefView(BaseModel):
    # None for a kind still on the defaults (no row yet).
    id: uuid.UUID | None
    kind: str
    channels: list[str]
    quiet_hours: Any
    batch_mode: str
    created_at: datetime.datetime | None
    updated_at: datetime.datetime | None


class TestNotificationView(BaseModel):
    channels: list[str]


class ChannelRuleCell(BaseModel):
    text: str
    tone: Literal["success", "warning", "error", "idle"]


class ChannelRuleRow(BaseModel):
    kind: str
    label: str
    cells: dict[str, ChannelRuleCell]
    note: str


class ChannelRulesView(BaseModel):
    channels: list[dict[str, str]]
    rows: list[ChannelRuleRow]
    principles: list[dict[str, str]]


class ChannelPut(BaseModel):
    external_address: str | None = Field(default=None, min_length=1, max_length=320)
    verification_code: str | None = Field(default=None, min_length=4, max_length=12)


class ChannelBindingView(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    channel: str
    external_address: str
    verified_at: datetime.datetime | None
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ChannelAction(BaseModel):
    """The option the person pressed on the card (UC-08-06 step 3)."""

    decision: Literal["answer", "approve", "reject", "request_changes"]
    option: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=4_000)


class ChannelPayload(BaseModel):
    """What the channel gateway relays from a card. The card carries its delivery and
    organisation, and the whole body is covered by the channel's signature."""

    org_id: uuid.UUID
    delivery_id: uuid.UUID
    # The sender's id on the channel; it must be the delivery's user's active binding.
    sender: str = Field(min_length=1, max_length=320)
    action: ChannelAction


class ChannelCallback(BaseModel):
    payload: ChannelPayload
