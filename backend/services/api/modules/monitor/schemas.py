"""Request and response shapes of the monitor module (tab 4 A3, group "Theo dõi, báo cáo, thông báo").

Responses are the table's columns minus org_id, created_by, updated_by, deleted_at (A3 "Trả về").
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from services.api.modules.monitor import rules

RuleKind = Literal["threshold", "change_vs_previous", "seasonal_anomaly"]
NotifyMode = Literal["alert_only", "with_analysis"]
DraftStatus = Literal["draft", "active", "paused"]
ReportFormat = Literal["pdf", "xlsx", "html"]
ReportChannel = Literal["email", "teams", "zalo", "in_app"]
EventChannel = Literal["in_app", "email", "teams", "zalo", "mobile_push"]
BulletinKind = Literal["pre", "post"]
CalendarStatus = Literal["active", "paused", "retired"]


class Accepted(BaseModel):
    """A1.6: the body of a 202, next to the Location header."""

    id: uuid.UUID
    status: str
    poll_after_ms: int


def _cron(value: str | None) -> str | None:
    if value is not None and not rules.valid_cron(value):
        raise ValueError("expected a five-field cron expression, e.g. '30 7 * * 1'")
    return value


# ---------------------------------------------------------------- monitors (UC-08-01)


class MonitorCreate(BaseModel):
    metric_id: uuid.UUID
    scope_filter: dict[str, Any] = Field(default_factory=dict)
    rule_kind: RuleKind
    rule: dict[str, Any]
    schedule_cron: str = Field(max_length=120)
    notify_mode: NotifyMode = "alert_only"
    recipients: list[dict[str, Any]] = Field(default_factory=list, max_length=200)

    _check_cron = field_validator("schedule_cron")(_cron)


class MonitorPatch(BaseModel):
    scope_filter: dict[str, Any] | None = None
    rule: dict[str, Any] | None = None
    schedule_cron: str | None = Field(default=None, max_length=120)
    notify_mode: NotifyMode | None = None
    recipients: list[dict[str, Any]] | None = Field(default=None, max_length=200)
    status: DraftStatus | None = None

    _check_cron = field_validator("schedule_cron")(_cron)


class BacktestRequest(BaseModel):
    periods: int = Field(default=rules.BACKTEST_PERIODS, ge=1, le=rules.MAX_BACKTEST_PERIODS)


class MonitorView(BaseModel):
    id: uuid.UUID
    data_space_id: uuid.UUID
    owner_id: uuid.UUID
    agent_def_id: uuid.UUID | None
    metric_id: uuid.UUID
    scope_filter: Any
    rule_kind: str
    rule: Any
    schedule_cron: str
    notify_mode: str
    recipients: Any
    backtest: Any
    # BR-32: the backtest fired on more than 30% of its periods; None until it has run.
    noise_warning: bool | None = None
    status: str
    paused_reason: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


# ---------------------------------------------------------------- alerts (UC-08-02)


class AlertView(BaseModel):
    id: uuid.UUID
    monitor_id: uuid.UUID
    fired_at: datetime.datetime
    period_label: str | None
    value: decimal.Decimal | None
    threshold_value: decimal.Decimal | None
    summary: str
    skill_run_id: uuid.UUID | None
    dedup_key: str
    occurrences: int
    kind: str
    status: str
    assigned_to: uuid.UUID | None
    close_note: str | None
    closed_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class AlertAssign(BaseModel):
    assignee_id: uuid.UUID


class AlertClose(BaseModel):
    close_note: str | None = Field(default=None, max_length=4_000)


class AlertReopen(BaseModel):
    reason: str = Field(min_length=1, max_length=2_000)


# ---------------------------------------------------------------- reports (UC-02-04, UC-08-03/04)


class RecipientIn(BaseModel):
    principal_type: Literal["user", "group"] = "user"
    principal_id: uuid.UUID
    channels: list[ReportChannel] = Field(default_factory=lambda: ["email"], min_length=1)


class RecipientView(BaseModel):
    id: uuid.UUID
    principal_type: str
    principal_id: uuid.UUID
    channels: list[str]
    permission_group: str
    created_at: datetime.datetime


def _unique_recipients(value: list[RecipientIn] | None) -> list[RecipientIn] | None:
    if value is not None:
        keys = [(r.principal_type, r.principal_id) for r in value]
        if len(keys) != len(set(keys)):
            raise ValueError("a recipient is listed twice")
    return value


class ReportCreate(BaseModel):
    kind: Literal["report", "digest"] = "report"
    workspace_id: uuid.UUID | None = None
    title: str = Field(min_length=1, max_length=300)
    cron: str = Field(max_length=120)
    work_calendar_id: uuid.UUID | None = None
    cutoff_time: datetime.time | None = None
    as_of_rule: str = Field(min_length=1, max_length=120)
    formats: list[ReportFormat] = Field(default_factory=lambda: ["pdf"], min_length=1)
    require_publish_check: bool = True
    recipients: list[RecipientIn] = Field(default_factory=list, max_length=500)

    _check_cron = field_validator("cron")(_cron)
    _check_recipients = field_validator("recipients")(_unique_recipients)


class ReportPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    cron: str | None = Field(default=None, max_length=120)
    work_calendar_id: uuid.UUID | None = None
    cutoff_time: datetime.time | None = None
    as_of_rule: str | None = Field(default=None, min_length=1, max_length=120)
    formats: list[ReportFormat] | None = Field(default=None, min_length=1)
    status: DraftStatus | None = None
    recipients: list[RecipientIn] | None = Field(default=None, max_length=500)

    _check_cron = field_validator("cron")(_cron)
    _check_recipients = field_validator("recipients")(_unique_recipients)


class RunNow(BaseModel):
    as_of: datetime.date | None = None


class ReportScheduleView(BaseModel):
    id: uuid.UUID
    data_space_id: uuid.UUID
    kind: str
    workspace_id: uuid.UUID | None
    owner_id: uuid.UUID
    title: str
    cron: str
    work_calendar_id: uuid.UUID | None
    cutoff_time: datetime.time | None
    as_of_rule: str
    formats: list[str]
    require_publish_check: bool
    status: str
    recipients: list[RecipientView] = Field(default_factory=list)
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ReportRunView(BaseModel):
    id: uuid.UUID
    report_schedule_id: uuid.UUID
    kind: str
    as_of: datetime.date
    workflow_id: str | None
    trigger: str
    checks: Any
    # One entry per permission group the caller may see (BR-34); storage keys stay inside.
    outputs: list[dict[str, Any]]
    status: str
    finished_at: datetime.datetime | None
    created_at: datetime.datetime


class ReportScheduleDetail(ReportScheduleView):
    # The newest run, with only the outputs the caller's permission group may open (BR-34).
    latest_run: ReportRunView | None = None


class DigestPut(BaseModel):
    enabled: bool = True
    schedule: str | None = Field(default=None, max_length=120)
    metrics: list[uuid.UUID] | None = Field(default=None, max_length=50)

    _check_cron = field_validator("schedule")(_cron)


# ---------------------------------------------------------------- external calendars (UC-08-08)


def _zone(value: str | None) -> str | None:
    if value is not None and not rules.valid_timezone(value):
        raise ValueError("not an IANA time zone, e.g. 'Asia/Ho_Chi_Minh'")
    return value


class CalendarCreate(BaseModel):
    code: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1, max_length=300)
    legal_basis: str | None = Field(default=None, max_length=1_000)
    recurrence_hint: str | None = Field(default=None, max_length=500)
    default_local_time: datetime.time | None = None
    timezone: str = "Asia/Ho_Chi_Minh"
    work_calendar_id: uuid.UUID | None = None
    detection_source_id: uuid.UUID | None = None
    forecast_model_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    pre_bulletin_days_before: int = Field(default=1, ge=0, le=7)
    pre_bulletin_local_time: datetime.time = datetime.time(20, 0)
    forecast_lead: datetime.timedelta = datetime.timedelta(hours=2)
    post_bulletin: bool = True
    post_wait_max: datetime.timedelta = datetime.timedelta(hours=12)

    _check_zone = field_validator("timezone")(_zone)

    @model_validator(mode="after")
    def _a_way_to_know_the_periods(self) -> CalendarCreate:
        # A3: periods come from a recurrence hint or a detection source, never from code (BR-187).
        if self.recurrence_hint is None and self.detection_source_id is None:
            raise ValueError("give recurrence_hint or detection_source_id")
        return self


class CalendarPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    recurrence_hint: str | None = Field(default=None, max_length=500)
    default_local_time: datetime.time | None = None
    detection_source_id: uuid.UUID | None = None
    forecast_model_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)
    pre_bulletin_local_time: datetime.time | None = None
    forecast_lead: datetime.timedelta | None = None
    post_bulletin: bool | None = None
    post_wait_max: datetime.timedelta | None = None
    status: CalendarStatus | None = None


class NextEvent(BaseModel):
    id: uuid.UUID
    period_label: str
    expected_at: datetime.datetime
    status: str
    pre_bulletin_at: datetime.datetime | None


class CalendarView(BaseModel):
    id: uuid.UUID
    data_space_id: uuid.UUID
    code: str
    name: str
    owner_id: uuid.UUID
    legal_basis: str | None
    recurrence_hint: str | None
    default_local_time: datetime.time | None
    timezone: str
    work_calendar_id: uuid.UUID | None
    detection_source_id: uuid.UUID | None
    forecast_model_ids: list[uuid.UUID]
    pre_bulletin_days_before: int
    pre_bulletin_local_time: datetime.time
    forecast_lead: datetime.timedelta
    post_bulletin: bool
    post_wait_max: datetime.timedelta
    status: str
    temporal_schedule_id: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class CalendarSummary(CalendarView):
    next_event: NextEvent | None = None
    subscriber_count: int = 0


class BulletinState(BaseModel):
    id: uuid.UUID
    status: str
    sent_at: datetime.datetime | None


class EventView(BaseModel):
    id: uuid.UUID
    calendar_id: uuid.UUID
    period_label: str
    expected_at: datetime.datetime
    origin: str
    status: str
    confirmed_by: uuid.UUID | None
    evidence: Any
    shifted_from: datetime.datetime | None
    occurred_at: datetime.datetime | None
    pre_bulletin_at: datetime.datetime | None
    workflow_id: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class EventWithBulletins(EventView):
    pre_bulletin: BulletinState | None = None
    post_bulletin: BulletinState | None = None


class EventConfirm(BaseModel):
    action: Literal["confirm", "shift", "cancel"]
    expected_at: datetime.datetime | None = None
    note: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def _shift_needs_a_time(self) -> EventConfirm:
        if self.action == "shift" and self.expected_at is None:
            raise ValueError("action shift needs expected_at")
        if self.expected_at is not None and self.expected_at.tzinfo is None:
            raise ValueError("expected_at needs a time zone (RFC 3339)")
        return self


class SubscriptionPut(BaseModel):
    kinds: list[BulletinKind] = Field(min_length=1)
    channels: list[EventChannel] = Field(min_length=1)
    status: Literal["active", "paused"] = "active"

    @model_validator(mode="after")
    def _in_app_stays_on(self) -> SubscriptionPut:
        # A3: in_app is always among the channels (BR-127 keeps the Notification Centre complete).
        if "in_app" not in self.channels:
            raise ValueError("channels must include in_app")
        self.kinds = list(dict.fromkeys(self.kinds))
        self.channels = list(dict.fromkeys(self.channels))
        return self


class SubscriptionView(BaseModel):
    id: uuid.UUID
    calendar_id: uuid.UUID
    user_id: uuid.UUID
    kinds: list[str]
    channels: list[str]
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime


class RunBulletin(BaseModel):
    reason: str | None = Field(default=None, max_length=2_000)


class BulletinView(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    kind: str
    status: str
    ledger_entry_ids: list[uuid.UUID]
    # Where each forecast number of the bulletin comes from (GET /v1/forecast-ledger/{id}).
    ledger_entry_links: list[str] = Field(default_factory=list)
    gate_result: Any
    content: Any
    recipients_count: int
    sent_at: datetime.datetime | None
    web_link: str | None
    created_at: datetime.datetime
