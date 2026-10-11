"""Models per task, AI settings and system health (UC-11-05, UC-11-04, BR-140, BR-180).

A new model for a task is a `testing` version of governance.model_config with its eval run id
reserved; the eval module runs every Data Space's standard set against it and the version goes
active only when no Data Space loses correct answers (BR-140). The old version keeps serving.

Health is read from Prometheus at PROMETHEUS_URL (operator configuration, never a caller's URL);
a section that cannot be read is `no_data`, never a stale number (UC-11-04 E1).
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import governance
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.problems import FieldError, Problem
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
