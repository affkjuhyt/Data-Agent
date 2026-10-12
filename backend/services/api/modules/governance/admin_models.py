"""Models per task, AI settings, system health and agent usage (UC-11-05, UC-11-04, BR-140,
BR-180), and the schedule preview every "Lịch chạy" dialog shares (UC-06-03, UC-04-05, UC-02-04).

A new model for a task is a `testing` version of governance.model_config with its eval run id
reserved; the eval module runs every Data Space's standard set against it and the version goes
active only when no Data Space loses correct answers (BR-140). The old version keeps serving.

Health is read from Prometheus at PROMETHEUS_URL (operator configuration, never a caller's URL);
a section that cannot be read is `no_data`, never a stale number (UC-11-04 E1).
"""

from __future__ import annotations

import asyncio
import datetime
import decimal
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zoneinfo
from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import governance, org
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.problems import FieldError, Problem
from services.api.modules.agent import agents_of_runs, run_usage
from services.api.modules.eval import eval_runs

router = APIRouter()
ROLE = "svc_governance"
POLL_AFTER_MS = 2_000
PROMETHEUS_TIMEOUT_S = 3.0

Task = Literal[
    "planning",
    "sql_generation",
    "answer_writing",
    "doc_reading",
    "embedding",
    "reranking",
    "ocr",
    "pii_classification",
]
# Tasks that never go to a frontier model (model_config CHECK, BR-180).
LOCAL_ONLY_TASKS = {"embedding", "reranking", "ocr", "pii_classification", "sql_generation"}
SETTING_KEYS = {
    "max_cost_per_run",
    "input_filter",
    "output_filter",
    "prompt_log_retention_days",
    "frontier_routing",
}
HEALTH_QUERIES = {
    "services": "up",
    "gpu": "avg by (gpu) (DCGM_FI_DEV_GPU_UTIL)",
    "queues": "sum by (consumergroup) (kafka_consumergroup_lag)",
    "backup": "time() - max by (job) (backup_last_success_timestamp_seconds)",
    "dr": "max by (instance) (pg_replication_lag_seconds)",
    "alerts": 'ALERTS{alertstate="firing"}',
}


class _View(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ModelChange(BaseModel):
    model_name: str = Field(min_length=1, max_length=200)
    endpoint: str = Field(min_length=1, max_length=500)
    fallback_model: str | None = Field(default=None, max_length=200)
    limits: dict[str, Any] | None = None
    rollout_pct: int | None = Field(default=None, ge=1, le=100)


class ModelConfigView(_View):
    id: uuid.UUID
    task: str
    version: int
    model_name: str
    endpoint: str
    fallback_model: str | None
    limits: Any
    rollout_pct: int
    eval_run_id: uuid.UUID | None
    status: str
    activated_at: datetime.datetime | None
    activated_by: uuid.UUID | None
    provider_id: uuid.UUID
    route_role: str
    created_at: datetime.datetime
    updated_at: datetime.datetime
    latency_p95_ms: int | None = None
    latest_eval: dict[str, Any] | None = None


class ModelChangeAccepted(BaseModel):
    id: uuid.UUID
    status: str
    poll_after_ms: int
    model_config_id: uuid.UUID
    version: int


class SettingPut(BaseModel):
    value: Any


class AiSettingView(_View):
    id: uuid.UUID
    key: str
    value: Any
    version: int
    created_at: datetime.datetime
    updated_at: datetime.datetime


class HealthSection(BaseModel):
    status: Literal["ok", "no_data"]
    series: list[dict[str, Any]] = Field(default_factory=list)


class SystemHealth(BaseModel):
    checked_at: datetime.datetime
    status: Literal["ok", "degraded", "no_data"]
    sections: dict[str, HealthSection]


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _invalid(field: str, message: str) -> Problem:
    return Problem(
        422,
        "validation_failed",
        message,
        errors=[FieldError(field=field, code="invalid", message=message)],
    )


def _event(
    session: AsyncSession,
    principal: Principal,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    event_type: str,
    **payload: Any,
) -> None:
    session.add(
        governance.Outbox(
            id=uuid7(),
            org_id=principal.org_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=json.loads(json.dumps(payload, default=str)),
        )
    )


def _same_origin(url: str, base: str) -> bool:
    a, b = urllib.parse.urlsplit(url), urllib.parse.urlsplit(base)
    return (
        a.scheme in ("http", "https")
        and (a.scheme, a.hostname, a.port) == (b.scheme, b.hostname, b.port)
        and not a.username
        and not a.password
    )


# ---------------------------------------------------------------- models per task


@router.get(
    "/v1/admin/models",
    response_model=list[ModelConfigView],
    **endpoint("GET", "/v1/admin/models", operation_id="listModelConfigs"),
)
async def list_model_configs(principal: CurrentPrincipal) -> list[ModelConfigView]:
    mc, usage = governance.ModelConfig, governance.ModelUsage
    since = _now() - datetime.timedelta(hours=24)
    async with transaction(ROLE, principal) as session:
        rows = (
            await session.scalars(
                select(mc)
                .where(mc.org_id == principal.org_id, mc.status.in_(("active", "testing")))
                .order_by(mc.task, mc.route_role, mc.version.desc())
            )
        ).all()
        p95 = {
            (task, model): value
            for task, model, value in (
                await session.execute(
                    select(
                        usage.task,
                        usage.model_name,
                        func.percentile_cont(0.95).within_group(usage.latency_ms),
                    )
                    .where(usage.org_id == principal.org_id, usage.called_at >= since)
                    .group_by(usage.task, usage.model_name)
                )
            ).all()
        }
    runs = await eval_runs(principal, [r.eval_run_id for r in rows if r.eval_run_id])
    out = []
    for row in rows:
        view = ModelConfigView.model_validate(row)
        latency = p95.get((row.task, row.model_name))
        view.latency_p95_ms = round(latency) if latency is not None else None
        view.latest_eval = runs.get(row.eval_run_id) if row.eval_run_id else None
        out.append(view)
    return out


@router.put(
    "/v1/admin/models/{task}",
    status_code=202,
    response_model=ModelChangeAccepted,
    **endpoint("PUT", "/v1/admin/models/{task}", operation_id="changeModel"),
)
async def change_model(
    task: Annotated[Task, Path()],
    body: ModelChange,
    principal: CurrentPrincipal,
    response: Response,
) -> ModelChangeAccepted:
    mc = governance.ModelConfig
    stamp = _now()
    async with transaction(ROLE, principal) as session:
        # A new task model is an on-prem primary; frontier candidates come with their provider
        # (UC-11-05 1b). The installation creates the on-prem provider (ADR-41).
        provider = await session.scalar(
            select(governance.ModelProvider)
            .where(
                governance.ModelProvider.org_id == principal.org_id,
                governance.ModelProvider.kind == "on_prem",
                governance.ModelProvider.status == "active",
            )
            .order_by(governance.ModelProvider.created_at)
            .limit(1)
        )
        if provider is None:
            raise Problem(
                422, "business_rule_violated", "No active on-prem model provider to serve it."
            )
        # An on-prem primary is served by the on-prem provider's own cluster: an endpoint
        # elsewhere would send every call off the premises without BR-180's checks.
        if not _same_origin(body.endpoint, provider.endpoint):
            raise _invalid(
                "endpoint",
                f"The endpoint is served by the on-prem provider at {provider.endpoint}.",
            )
        versions = (
            await session.scalars(
                select(mc)
                .where(mc.org_id == principal.org_id, mc.task == task)
                .order_by(mc.version.desc())
                .with_for_update()
            )
        ).all()
        if any(v.status == "testing" and v.route_role == "primary" for v in versions):
            raise Problem(409, "invalid_state", f"A model change for {task} is being evaluated.")
        current = next(
            (v for v in versions if v.status == "active" and v.route_role == "primary"), None
        )
        eval_run_id = uuid7()
        row = mc(
            id=uuid7(),
            org_id=principal.org_id,
            task=task,
            version=(versions[0].version + 1) if versions else 1,
            model_name=body.model_name,
            endpoint=body.endpoint,
            fallback_model=body.fallback_model,
            limits=body.limits if body.limits is not None else (current.limits if current else {}),
            rollout_pct=body.rollout_pct or 100,
            eval_run_id=eval_run_id,
            status="testing",
            provider_id=provider.id,
            route_role="primary",
            created_by=principal.user_id,
            updated_by=principal.user_id,
            created_at=stamp,
            updated_at=stamp,
        )
        session.add(row)
        await session.flush()
        # The eval module runs every Data Space's standard set and activates the version only
        # when none loses correct answers (BR-140); otherwise the owners decide in Cần bạn.
        _event(
            session,
            principal,
            "model_config",
            row.id,
            "model_config.eval_requested",
            model_config_id=row.id,
            task=task,
            version=row.version,
            eval_run_id=eval_run_id,
            baseline_model_config_id=current.id if current else None,
            rollout_pct=row.rollout_pct,
            status=row.status,
        )
    response.headers["Location"] = f"/v1/eval-runs/{eval_run_id}"
    return ModelChangeAccepted(
        id=eval_run_id,
        status="queued",
        poll_after_ms=POLL_AFTER_MS,
        model_config_id=row.id,
        version=row.version,
    )


# ---------------------------------------------------------------- AI settings


def _check_setting(key: str, value: Any) -> None:
    if key not in SETTING_KEYS:
        raise _invalid("key", f"Unknown setting; one of {sorted(SETTING_KEYS)}.")
    if key in ("max_cost_per_run", "prompt_log_retention_days"):
        if not isinstance(value, int | float) or isinstance(value, bool) or value < 0:
            raise _invalid("value", f"{key} is a number of zero or more.")
    elif key == "frontier_routing":
        if not isinstance(value, dict) or not isinstance(value.get("enabled"), bool):
            raise _invalid("value", "frontier_routing is {enabled, tasks, data_space_ids}.")
        tasks = value.get("tasks", [])
        if not isinstance(tasks, list) or set(tasks) & LOCAL_ONLY_TASKS:
            raise _invalid(
                "value", f"These tasks never route to a frontier model: {sorted(LOCAL_ONLY_TASKS)}."
            )
        spaces = value.get("data_space_ids", [])
        if not isinstance(spaces, list):
            raise _invalid("value", "data_space_ids is a list.")
        for space in spaces:
            try:
                uuid.UUID(str(space))
            except ValueError as exc:
                raise _invalid("value", "data_space_ids are ids.") from exc


@router.get(
    "/v1/admin/ai-settings",
    response_model=list[AiSettingView],
    **endpoint("GET", "/v1/admin/ai-settings", operation_id="listAiSettings"),
)
async def list_ai_settings(principal: CurrentPrincipal) -> list[AiSettingView]:
    # A key never set has no row: the gateway applies its built-in default for it.
    async with transaction(ROLE, principal) as session:
        rows = (
            await session.scalars(
                select(governance.AiSetting)
                .where(governance.AiSetting.org_id == principal.org_id)
                .order_by(governance.AiSetting.key)
            )
        ).all()
    return [AiSettingView.model_validate(r) for r in rows]


@router.put(
    "/v1/admin/ai-settings/{key}",
    response_model=AiSettingView,
    **endpoint("PUT", "/v1/admin/ai-settings/{key}", operation_id="putAiSetting"),
)
async def put_ai_setting(
    key: Annotated[str, Path(max_length=100)], body: SettingPut, principal: CurrentPrincipal
) -> AiSettingView:
    _check_setting(key, body.value)
    stamp = _now()
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(
            select(governance.AiSetting)
            .where(governance.AiSetting.org_id == principal.org_id, governance.AiSetting.key == key)
            .with_for_update()
        )
        before = None
        if row is None:
            row = governance.AiSetting(
                id=uuid7(),
                org_id=principal.org_id,
                key=key,
                value=body.value,
                version=1,
                created_by=principal.user_id,
                updated_by=principal.user_id,
                created_at=stamp,
                updated_at=stamp,
            )
            session.add(row)
        else:
            before = {"value": row.value, "version": row.version}
            row.value = body.value
            row.version += 1
            row.updated_by, row.updated_at = principal.user_id, stamp
        await session.flush()
        # The old value goes to the audit trail (tab 3b ai_setting.version); the gateway reloads
        # its rules from the change event.
        _event(
            session,
            principal,
            "ai_setting",
            row.id,
            "audit.requested",
            action="ai_setting.put",
            object_kind="ai_setting",
            object_id=row.id,
            actor_id=principal.user_id,
            client_id=principal.client_id,
            before=before,
            after={"value": row.value, "version": row.version},
        )
        _event(
            session,
            principal,
            "ai_setting",
            row.id,
            "ai_setting.changed",
            key=key,
            version=row.version,
        )
        await session.refresh(row)
        return AiSettingView.model_validate(row)


# ---------------------------------------------------------------- system health


def _prometheus(base: str, query: str) -> list[dict[str, Any]]:
    # urllib also opens file:// and ftp://; only an http(s) Prometheus is read.
    if urllib.parse.urlsplit(base).scheme not in ("http", "https"):
        raise ValueError("PROMETHEUS_URL is not http(s)")
    url = f"{base.rstrip('/')}/api/v1/query?" + urllib.parse.urlencode({"query": query})
    with urllib.request.urlopen(url, timeout=PROMETHEUS_TIMEOUT_S) as reply:
        payload = json.loads(reply.read())
    if payload.get("status") != "success":
        raise ValueError("prometheus refused the query")
    return [
        {"labels": r.get("metric", {}), "value": r.get("value", [None, None])[1]}
        for r in payload.get("data", {}).get("result", [])
    ]


async def _section(base: str | None, query: str) -> HealthSection:
    if not base:
        return HealthSection(status="no_data")
    try:
        series = await asyncio.to_thread(_prometheus, base, query)
    except (OSError, ValueError, urllib.error.URLError):
        return HealthSection(status="no_data")
    return HealthSection(status="ok", series=series)


@router.get(
    "/v1/admin/system/health",
    response_model=SystemHealth,
    **endpoint("GET", "/v1/admin/system/health", operation_id="getSystemHealth"),
)
async def system_health(principal: CurrentPrincipal) -> SystemHealth:
    base = os.environ.get("PROMETHEUS_URL")
    names = list(HEALTH_QUERIES)
    results = await asyncio.gather(*(_section(base, HEALTH_QUERIES[n]) for n in names))
    sections = dict(zip(names, results, strict=True))
    if all(s.status == "no_data" for s in results):
        status: Literal["ok", "degraded", "no_data"] = "no_data"
    else:
        down = [s for s in sections["services"].series if str(s.get("value")) == "0"]
        firing = sections["alerts"].series
        status = "degraded" if down or firing else "ok"
    return SystemHealth(checked_at=_now(), status=status, sections=sections)


# ---------------------------------------------------------------- agent usage (UC-11-04, UC-13-06)

# A window longer than a year is a report, not this screen; it would also scan every partition.
MAX_USAGE_WINDOW = datetime.timedelta(days=366)
DEFAULT_USAGE_WINDOW = datetime.timedelta(days=7)


class UsageFigures(BaseModel):
    runs: int = 0
    # Runs that stopped for a person at least once, and their share of the runs.
    needs_person: int = 0
    needs_person_rate: float | None = None
    errors: int = 0
    error_rate: float | None = None
    # Mean wall time of the runs that finished.
    avg_duration_s: float | None = None
    cost: decimal.Decimal = decimal.Decimal(0)
    # Model calls of the runs, from governance.model_usage.
    model_calls: int = 0
    model_errors: int = 0
    tokens: int = 0


class UsageDay(UsageFigures):
    day: datetime.date


class AgentUsage(UsageFigures):
    agent_id: uuid.UUID
    name: str
    data_space_id: uuid.UUID | None
    status: str | None
    monthly_budget: decimal.Decimal | None
    spent_this_month: decimal.Decimal | None
    budget_used_ratio: float | None
    days: list[UsageDay]


class AgentUsageReport(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: datetime.datetime = Field(alias="from")
    to: datetime.datetime
    granularity: Literal["day"]
    totals: UsageFigures
    days: list[UsageDay]
    agents: list[AgentUsage]


class _Tally:
    def __init__(self) -> None:
        self.runs = self.needs = self.errors = self.finished = 0
        self.duration = 0.0
        self.cost = decimal.Decimal(0)
        self.calls = self.model_errors = self.tokens = 0

    def figures(self) -> dict[str, Any]:
        return {
            "runs": self.runs,
            "needs_person": self.needs,
            "needs_person_rate": self.needs / self.runs if self.runs else None,
            "errors": self.errors,
            "error_rate": self.errors / self.runs if self.runs else None,
            "avg_duration_s": self.duration / self.finished if self.finished else None,
            "cost": self.cost,
            "model_calls": self.calls,
            "model_errors": self.model_errors,
            "tokens": self.tokens,
        }


@router.get(
    "/v1/admin/agent-usage",
    response_model=AgentUsageReport,
    **endpoint("GET", "/v1/admin/agent-usage", operation_id="getAgentUsage"),
)
async def agent_usage(
    principal: CurrentPrincipal,
    from_: Annotated[datetime.datetime | None, Query(alias="from")] = None,
    to: datetime.datetime | None = None,
    granularity: Literal["day"] = "day",
) -> AgentUsageReport:
    end = to or _now()
    start = from_ or end - DEFAULT_USAGE_WINDOW
    if start.tzinfo is None or end.tzinfo is None:
        raise _invalid("from", "from and to need a time zone.")
    if start >= end:
        raise _invalid("from", "from is before to.")
    if end - start > MAX_USAGE_WINDOW:
        raise _invalid("from", "The window is at most 366 days.")
    usage = governance.ModelUsage
    day = func.date(func.timezone("UTC", usage.called_at))
    async with transaction(ROLE, principal) as session:
        calls = (
            await session.execute(
                select(
                    day,
                    usage.caller_id,
                    func.count(),
                    func.count().filter(usage.outcome == "error"),
                    func.coalesce(func.sum(usage.input_tokens + usage.output_tokens), 0),
                )
                .where(
                    usage.org_id == principal.org_id,
                    usage.caller_kind == "agent_run",
                    usage.caller_id.is_not(None),
                    usage.called_at >= start,
                    usage.called_at < end,
                )
                .group_by(day, usage.caller_id)
            )
        ).all()
    # Run counts are agent.agent_run's; svc_governance does not read that schema (D1.2).
    owners = await agents_of_runs(principal, {r[1] for r in calls})
    runs = await run_usage(principal, start, end, set(owners.values()))

    total = _Tally()
    by_day: dict[datetime.date, _Tally] = {}
    by_agent: dict[uuid.UUID, dict[datetime.date, _Tally]] = {}

    def tallies(agent_id: uuid.UUID, on: datetime.date) -> tuple[_Tally, ...]:
        return (
            total,
            by_day.setdefault(on, _Tally()),
            by_agent.setdefault(agent_id, {}).setdefault(on, _Tally()),
        )

    for r in runs.days:
        for t in tallies(r.agent_id, r.day):
            t.runs += r.runs
            t.needs += r.needs_person
            t.errors += r.failed
            t.duration += r.duration_s_sum
            t.finished += r.finished
            t.cost += r.cost
    for on, run_id, count, failed, tokens in calls:
        agent_id = owners.get(run_id)
        if agent_id is None:
            continue
        for t in tallies(agent_id, on):
            t.calls += int(count)
            t.model_errors += int(failed)
            t.tokens += int(tokens)

    agents = {a.agent_id: a for a in runs.agents}
    rows = []
    for agent_id, days in by_agent.items():
        a = agents.get(agent_id)
        overall = _Tally()
        for t in days.values():
            for name in vars(overall):
                setattr(overall, name, getattr(overall, name) + getattr(t, name))
        budget = a.monthly_budget if a else None
        rows.append(
            AgentUsage(
                agent_id=agent_id,
                name=a.name if a else "",
                data_space_id=a.data_space_id if a else None,
                status=a.status if a else None,
                monthly_budget=budget,
                spent_this_month=a.spent_this_month if a else None,
                budget_used_ratio=float(a.spent_this_month / budget) if a and budget else None,
                days=[UsageDay(day=d, **t.figures()) for d, t in sorted(days.items())],
                **overall.figures(),
            )
        )
    rows.sort(key=lambda r: (-r.runs, r.name, str(r.agent_id)))
    # "from" is a Python keyword: the report is built from its wire names.
    return AgentUsageReport.model_validate(
        {
            "from": start,
            "to": end,
            "granularity": granularity,
            "totals": UsageFigures(**total.figures()),
            "days": [UsageDay(day=d, **t.figures()) for d, t in sorted(by_day.items())],
            "agents": rows,
        }
    )


# ---------------------------------------------------------------- schedule preview


MAX_PREVIEW = 50
MAX_SKIPPED = 100
# How far ahead a schedule is searched: a cron for 29 February finds its runs within it.
PREVIEW_HORIZON_DAYS = 366 * 5
_CRON_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))


class SchedulePreviewIn(BaseModel):
    cron: str = Field(min_length=1, max_length=200)
    work_calendar_id: uuid.UUID | None = None
    # IANA name; defaults to the calendar's, else UTC.
    timezone: str | None = Field(default=None, min_length=1, max_length=100)
    count: int = Field(default=5, ge=1, le=MAX_PREVIEW)


class SchedulePreview(BaseModel):
    cron: str
    timezone: str
    work_calendar_id: uuid.UUID | None
    # The next runs, in the schedule's time zone.
    items: list[datetime.datetime]
    # Days the cron matched but the calendar does not work, up to the last run shown.
    skipped_days: list[datetime.date]


def _cron_field(text: str, low: int, high: int) -> frozenset[int]:
    values: set[int] = set()
    for part in text.split(","):
        body, _, step_text = part.partition("/")
        step = int(step_text) if step_text else 1
        if body == "*":
            start, end = low, high
        elif "-" in body:
            a, b = body.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = int(body)
            end = high if step_text else start
        # Day-of-week accepts 7 for Sunday, as most crons do.
        top = high + 1 if high == 6 else high
        if step < 1 or start < low or end > top or start > end:
            raise ValueError(part)
        values.update(range(start, end + 1, step))
    return frozenset(0 if (high == 6 and v == 7) else v for v in values)


def parse_cron(expr: str) -> tuple[frozenset[int], ...]:
    """A five-field cron (minute hour day-of-month month day-of-week); ValueError otherwise."""
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(expr)
    return tuple(
        _cron_field(f, low, high) for f, (low, high) in zip(fields, _CRON_RANGES, strict=True)
    )


def _cron_day(fields: tuple[frozenset[int], ...], expr: str, day: datetime.date) -> bool:
    _, _, dom, month, dow = fields
    if day.month not in month:
        return False
    parts = expr.split()
    in_dom, in_dow = day.day in dom, (day.isoweekday() % 7) in dow
    # Classic cron: when both day fields are restricted, either one matching is enough.
    if parts[2] != "*" and parts[4] != "*":
        return in_dom or in_dow
    return in_dom and in_dow


def next_runs(
    expr: str,
    after: datetime.datetime,
    zone: zoneinfo.ZoneInfo,
    count: int,
    working: Callable[[datetime.date], bool] | None = None,
) -> tuple[list[datetime.datetime], list[datetime.date]]:
    """The next `count` runs of `expr` read in `zone`, strictly after `after`; `working(day)`
    False skips the day. Local times that do not exist (a DST gap) are skipped too."""
    fields = parse_cron(expr)
    minutes, hours = sorted(fields[0]), sorted(fields[1])
    now = after.astimezone(zone)
    day = now.date()
    runs: list[datetime.datetime] = []
    skipped: list[datetime.date] = []
    for _ in range(PREVIEW_HORIZON_DAYS):
        if _cron_day(fields, expr, day):
            if working is not None and not working(day):
                if len(skipped) < MAX_SKIPPED:
                    skipped.append(day)
            else:
                for hour in hours:
                    for minute in minutes:
                        local = datetime.datetime(day.year, day.month, day.day, hour, minute)
                        at = local.replace(tzinfo=zone)
                        if (
                            at.astimezone(datetime.UTC).astimezone(zone).replace(tzinfo=None)
                            != local
                            or at <= now
                        ):
                            continue
                        runs.append(at)
                        if len(runs) == count:
                            return runs, skipped
        day += datetime.timedelta(days=1)
    return runs, skipped


@router.post(
    "/v1/schedules:preview",
    response_model=SchedulePreview,
    **endpoint("POST", "/v1/schedules:preview", operation_id="previewSchedule"),
)
async def preview_schedule(body: SchedulePreviewIn, principal: CurrentPrincipal) -> SchedulePreview:
    try:
        parse_cron(body.cron)
    except ValueError as exc:
        raise _invalid("cron", "cron is not a five-field cron expression.") from exc
    weekend: set[int] = set()
    days: dict[datetime.date, str] = {}
    zone_name = body.timezone
    if body.work_calendar_id is not None:
        # Reads org (cross_reads grants svc_governance org); nothing is written.
        async with transaction(ROLE, principal) as session:
            calendar = await session.scalar(
                select(org.WorkCalendar).where(
                    org.WorkCalendar.id == body.work_calendar_id,
                    org.WorkCalendar.org_id == principal.org_id,
                )
            )
            if calendar is None:
                raise _invalid("work_calendar_id", "No work calendar of this organisation.")
            days = dict(
                (
                    await session.execute(
                        select(org.WorkCalendarDay.day, org.WorkCalendarDay.kind).where(
                            org.WorkCalendarDay.calendar_id == calendar.id
                        )
                    )
                )
                .tuples()
                .all()
            )
        weekend = set(calendar.weekend_days)
        zone_name = zone_name or calendar.time_zone
    zone_name = zone_name or "UTC"
    try:
        zone = zoneinfo.ZoneInfo(zone_name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise _invalid("timezone", "timezone is not an IANA time zone name.") from exc

    def working(day: datetime.date) -> bool:
        kind = days.get(day)
        if kind == "working_override":
            return True
        return kind != "holiday" and day.isoweekday() not in weekend

    runs, skipped = next_runs(
        body.cron,
        _now(),
        zone,
        body.count,
        working if body.work_calendar_id is not None else None,
    )
    last = runs[-1].date() if runs else None
    return SchedulePreview(
        cron=body.cron,
        timezone=zone_name,
        work_calendar_id=body.work_calendar_id,
        items=runs,
        skipped_days=[d for d in skipped if last is None or d <= last],
    )
