"""Tenant-scoped ROI use cases for the workflow runtime.

The product promise is five business workflows, not a single chat endpoint:
Text-to-SQL, why-analysis, data-quality, scheduled reporting, and anomalies.
This module makes those capabilities concrete on top of data the runtime
already owns: conversation/message/trace metrics and platform settings.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import math
import os
from typing import Any

import httpx

try:
    from sqlalchemy import select

    from libs.basemodel.services_databaseconnector.postgres_orm.workflow_orm import (
        PlatformSettingsORM,
    )
    from libs.database_connector.postgres_connector import client
    from services.workflow_runtime.application.runtime_services import platform_queries
except Exception:  # pragma: no cover - unit hosts may not install service deps
    select = None
    PlatformSettingsORM = None
    client = None

    class _MissingPlatformQueries:
        async def get_setting(self, *_a: Any, **_k: Any) -> dict[str, Any]:
            raise RuntimeError("platform query dependencies are not installed")

        async def put_setting(self, *_a: Any, **_k: Any) -> dict[str, Any]:
            raise RuntimeError("platform query dependencies are not installed")

        async def platform_metrics(self, *_a: Any, **_k: Any) -> dict[str, Any]:
            raise RuntimeError("platform query dependencies are not installed")

        async def list_traces(self, *_a: Any, **_k: Any) -> dict[str, Any]:
            raise RuntimeError("platform query dependencies are not installed")

    platform_queries = _MissingPlatformQueries()

logger = logging.getLogger(__name__)

_SCHEDULE_KEY = "schedules"
_OUTBOX_KEY = "delivery_outbox"
_DELIVERY_KEY = "delivery_channels"
_SCHEDULER_INTERVAL_SECONDS = int(os.getenv("ROI_SCHEDULER_INTERVAL_SECONDS", "60"))


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _iso(value: dt.datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _parse_dt(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)
    except ValueError:
        return None


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = _mean(values)
    return math.sqrt(sum((x - avg) ** 2 for x in values) / (len(values) - 1))


def _linear_projection(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    xs = list(range(len(values)))
    x_avg = _mean([float(x) for x in xs])
    y_avg = _mean(values)
    denom = sum((x - x_avg) ** 2 for x in xs)
    if denom == 0:
        return values[-1]
    slope = sum((x - x_avg) * (y - y_avg) for x, y in zip(xs, values, strict=True)) / denom
    return max(0.0, values[-1] + slope)


def _field_match(expr: str, value: int) -> bool:
    if expr == "*":
        return True
    for part in expr.split(","):
        if part.startswith("*/"):
            try:
                step = int(part[2:])
            except ValueError:
                return False
            if step > 0 and value % step == 0:
                return True
        elif "-" in part:
            try:
                start, end = [int(x) for x in part.split("-", 1)]
            except ValueError:
                return False
            if start <= value <= end:
                return True
        else:
            try:
                if int(part) == value:
                    return True
            except ValueError:
                return False
    return False


def _cron_due(cron: str, base: dt.datetime, now: dt.datetime) -> bool:
    try:
        from croniter import croniter

        return croniter(cron, base).get_next(dt.datetime) <= now
    except Exception:
        parts = cron.split()
        if len(parts) != 5:
            return False
        cursor = base.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
        limit = min(now, base + dt.timedelta(days=32))
        while cursor <= limit:
            if (
                _field_match(parts[0], cursor.minute)
                and _field_match(parts[1], cursor.hour)
                and _field_match(parts[2], cursor.day)
                and _field_match(parts[3], cursor.month)
                and _field_match(parts[4], (cursor.weekday() + 1) % 7)
            ):
                return cursor <= now
            cursor += dt.timedelta(minutes=1)
        return False


async def _setting_value(tenant_id: str, key: str, fallback: Any) -> Any:
    row = await platform_queries.get_setting(tenant_id, key)
    return row.get("value") if row.get("value") is not None else fallback


async def _save_setting(tenant_id: str, key: str, value: Any, updated_by: str | None = None) -> None:
    await platform_queries.put_setting(tenant_id, key, value, updated_by)


async def weekly_narrative(tenant_id: str, days: int = 7) -> dict[str, Any]:
    metrics = await platform_queries.platform_metrics(tenant_id, days=days)
    series = metrics.get("series") or []
    conversations = [float(x.get("conversations") or 0) for x in series]
    messages = [float(x.get("messages") or 0) for x in series]
    errors = [float(x.get("errors") or 0) for x in series]
    conv_total = int(sum(conversations))
    msg_total = int(sum(messages))
    err_total = int(sum(errors))
    avg_latency = metrics.get("latency_ms", {}).get("avg")
    error_rate = float(metrics.get("error_rate") or 0)
    busiest = max(series, key=lambda x: int(x.get("messages") or 0), default=None)
    narrative = (
        f"{days} ngày qua có {conv_total} hội thoại và {msg_total} message. "
        f"Ngày cao nhất là {busiest.get('date') if busiest else '—'} "
        f"với {int(busiest.get('messages') or 0) if busiest else 0} message. "
        f"Error rate hiện tại {error_rate:.1%}; latency trung bình "
        f"{round(float(avg_latency), 1) if avg_latency is not None else '—'} ms."
    )
    return {
        "kind": "weekly_report",
        "title": f"Narrative {days} ngày",
        "generated_at": _iso(),
        "summary": narrative,
        "metrics": {
            "conversations": conv_total,
            "messages": msg_total,
            "errors": err_total,
            "error_rate": error_rate,
            "avg_latency_ms": avg_latency,
        },
        "source": "Conversations, Messages, AgentTraces",
    }


async def data_quality_summary(tenant_id: str, days: int = 7) -> dict[str, Any]:
    metrics = await platform_queries.platform_metrics(tenant_id, days=days)
    traces = await platform_queries.list_traces(tenant_id, limit=200, status=None)
    items = traces.get("items") or []
    failed = [t for t in items if t.get("status") != "success"]
    missing_latency = [t for t in items if t.get("latency_ms") is None]
    tool_counts: dict[str, int] = {}
    for trace in failed:
        tool = str(trace.get("tool_name") or "unknown")
        tool_counts[tool] = tool_counts.get(tool, 0) + 1
    issues = []
    if failed:
        issues.append({"rule": "trace_success", "severity": "high", "count": len(failed), "detail": "Tool trace không thành công"})
    if missing_latency:
        issues.append({"rule": "trace_latency", "severity": "medium", "count": len(missing_latency), "detail": "Trace thiếu latency_ms"})
    if float(metrics.get("error_rate") or 0) > 0.05:
        issues.append({"rule": "error_rate", "severity": "high", "count": metrics.get("error_rate"), "detail": "Error rate vượt 5%"})
    return {
        "kind": "data_quality",
        "title": "Data-quality agent",
        "generated_at": _iso(),
        "status": "needs_attention" if issues else "healthy",
        "issues": issues,
        "failed_tools": sorted(tool_counts.items(), key=lambda x: x[1], reverse=True)[:10],
        "checked_traces": len(items),
        "source": "AgentTraces + platform metrics",
    }


async def anomaly_summary(tenant_id: str, days: int = 14) -> dict[str, Any]:
    metrics = await platform_queries.platform_metrics(tenant_id, days=days)
    series = metrics.get("series") or []
    signals: list[dict[str, Any]] = []
    for field in ("conversations", "messages", "errors"):
        values = [float(x.get(field) or 0) for x in series]
        baseline = values[:-1]
        current = values[-1] if values else 0.0
        avg = _mean(baseline)
        sd = _stdev(baseline)
        z = (current - avg) / sd if sd else 0.0
        ratio_spike = avg > 0 and current >= avg * 2
        if abs(z) >= 2.0 or ratio_spike or (field == "errors" and current > max(1.0, avg * 2)):
            signals.append({
                "metric": field,
                "current": current,
                "baseline_avg": round(avg, 2),
                "z_score": round(z, 2),
                "direction": "up" if current >= avg else "down",
            })
    return {
        "kind": "anomaly",
        "title": "Bất thường",
        "generated_at": _iso(),
        "status": "detected" if signals else "clear",
        "signals": signals,
        "window_days": days,
        "source": "daily platform metrics",
    }


async def forecast_summary(tenant_id: str, days: int = 14) -> dict[str, Any]:
    metrics = await platform_queries.platform_metrics(tenant_id, days=days)
    series = metrics.get("series") or []
    projections: dict[str, Any] = {}
    for field in ("conversations", "messages", "tokens"):
        values = [float(x.get(field) or 0) for x in series]
        projected = _linear_projection(values)
        projections[field] = None if projected is None else round(projected, 1)
    return {
        "kind": "forecast",
        "title": "Dự báo ngắn hạn",
        "generated_at": _iso(),
        "projection_next_day": projections,
        "method": "linear trend over recent platform metrics",
        "confidence": "low" if len(series) < 14 else "medium",
        "source": "daily platform metrics",
    }


async def summarize_use_cases(tenant_id: str) -> dict[str, Any]:
    schedules = await _setting_value(tenant_id, _SCHEDULE_KEY, [])
    channels = await _setting_value(tenant_id, _DELIVERY_KEY, [])
    schedules = schedules if isinstance(schedules, list) else []
    channels = channels if isinstance(channels, list) else []
    metrics = await platform_queries.platform_metrics(tenant_id, days=7)
    traces = await platform_queries.list_traces(tenant_id, limit=100)
    text_sql_runs = [
        t for t in traces.get("items", [])
        if "sql" in str(t.get("tool_name") or "").lower() or "data" in str(t.get("tool_name") or "").lower()
    ]
    dq, narrative, anomaly, forecast = await asyncio.gather(
        data_quality_summary(tenant_id),
        weekly_narrative(tenant_id),
        anomaly_summary(tenant_id),
        forecast_summary(tenant_id),
    )
    return {
        "generated_at": _iso(),
        "use_cases": [
            {"id": "text_to_sql", "name": "Text-to-SQL", "status": "ready", "evidence": f"{len(text_sql_runs)} data/SQL trace gần nhất"},
            {"id": "why_analysis", "name": "Phân tích vì sao", "status": "ready", "evidence": "Evidence/citation + trace drilldown đã bật"},
            {"id": "data_quality", "name": "Data-quality agent", "status": dq["status"], "evidence": f"{dq['checked_traces']} trace được kiểm tra"},
            {"id": "scheduled_reports", "name": "Báo cáo định kỳ", "status": "ready" if schedules else "needs_setup", "evidence": f"{len(schedules)} lịch, {len(channels)} kênh gửi"},
            {"id": "anomaly", "name": "Bất thường", "status": anomaly["status"], "evidence": f"{len(anomaly['signals'])} signal"},
            {"id": "forecast", "name": "Dự báo", "status": "ready", "evidence": f"projection: {forecast['projection_next_day'].get('messages')} messages/ngày"},
        ],
        "latest": {"metrics": metrics, "data_quality": dq, "narrative": narrative, "anomaly": anomaly, "forecast": forecast},
    }


async def deliver_report(tenant_id: str, report: dict[str, Any], *, updated_by: str | None = None) -> list[dict[str, Any]]:
    channels = await _setting_value(tenant_id, _DELIVERY_KEY, [])
    channels = channels if isinstance(channels, list) else []
    attempts: list[dict[str, Any]] = []
    for channel in channels:
        if not isinstance(channel, dict) or not channel.get("enabled", True):
            continue
        kind = channel.get("kind")
        target = channel.get("target") or channel.get("webhook_url") or ""
        status = "queued"
        detail = "stored in delivery outbox"
        if kind == "slack" and channel.get("webhook_url"):
            try:
                async with httpx.AsyncClient(timeout=5.0) as http:
                    resp = await http.post(str(channel["webhook_url"]), json={"text": report.get("summary") or report.get("title")})
                status = "sent" if 200 <= resp.status_code < 300 else "failed"
                detail = f"slack webhook HTTP {resp.status_code}"
            except Exception as e:  # noqa: BLE001 - delivery failure must not fail the job
                status = "failed"
                detail = str(e)[:200]
        attempts.append({"kind": kind, "target": target, "status": status, "detail": detail, "at": _iso()})
    outbox = await _setting_value(tenant_id, _OUTBOX_KEY, [])
    outbox = outbox if isinstance(outbox, list) else []
    await _save_setting(tenant_id, _OUTBOX_KEY, [{"report": report, "attempts": attempts, "at": _iso()}, *outbox[:49]], updated_by)
    return attempts


async def run_use_case(tenant_id: str, kind: str, *, updated_by: str | None = None) -> dict[str, Any]:
    if kind == "data_quality":
        report = await data_quality_summary(tenant_id)
    elif kind == "anomaly":
        report = await anomaly_summary(tenant_id)
    elif kind == "forecast":
        report = await forecast_summary(tenant_id)
    else:
        report = await weekly_narrative(tenant_id)
    attempts = await deliver_report(tenant_id, report, updated_by=updated_by)
    return {"report": report, "delivery_attempts": attempts}


def _schedule_due(schedule: dict[str, Any], now: dt.datetime) -> bool:
    if not schedule.get("enabled", True):
        return False
    last = _parse_dt(schedule.get("last_run"))
    base = last or _parse_dt(schedule.get("created_at")) or (now - dt.timedelta(days=365))
    return _cron_due(str(schedule.get("cron") or ""), base, now)


async def run_due_schedules(tenant_id: str, *, updated_by: str | None = None, now: dt.datetime | None = None) -> dict[str, Any]:
    now = now or _now()
    schedules = await _setting_value(tenant_id, _SCHEDULE_KEY, [])
    schedules = schedules if isinstance(schedules, list) else []
    ran: list[dict[str, Any]] = []
    next_schedules = []
    for item in schedules:
        schedule = item if isinstance(item, dict) else {}
        if schedule and _schedule_due(schedule, now):
            kind = str(schedule.get("report_kind") or "weekly_report")
            result = await run_use_case(tenant_id, kind, updated_by=updated_by)
            schedule = {
                **schedule,
                "last_run": now.isoformat(),
                "last_status": "success",
                "last_summary": result["report"].get("summary") or result["report"].get("title"),
                "delivery_attempts": result["delivery_attempts"],
            }
            ran.append({"schedule_id": schedule.get("id"), "name": schedule.get("name"), "kind": kind, "result": result})
        next_schedules.append(schedule)
    if ran:
        await _save_setting(tenant_id, _SCHEDULE_KEY, next_schedules, updated_by)
    return {"ran": ran, "checked": len(schedules), "generated_at": now.isoformat()}


async def run_due_schedules_for_all_tenants() -> dict[str, Any]:
    if client is None or PlatformSettingsORM is None or select is None or not client.is_connected():
        return {"tenants": 0, "ran": 0}
    async with client.get_client() as session:
        rows = (await session.execute(
            select(PlatformSettingsORM.tenant_id).where(PlatformSettingsORM.key == _SCHEDULE_KEY)
        )).fetchall()
    total_ran = 0
    tenants = {str(r.tenant_id) for r in rows}
    for tenant_id in tenants:
        try:
            out = await run_due_schedules(tenant_id, updated_by="roi_scheduler")
            total_ran += len(out.get("ran") or [])
        except Exception as e:  # noqa: BLE001 - one tenant must not stop the scheduler
            logger.warning("roi scheduler failed tenant=%s: %s", tenant_id, e)
    return {"tenants": len(tenants), "ran": total_ran}


async def scheduler_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await run_due_schedules_for_all_tenants()
        except Exception as e:  # noqa: BLE001 - background loop must stay alive
            logger.warning("roi scheduler tick failed: %s", e)
        try:
            await asyncio.wait_for(stop.wait(), timeout=_SCHEDULER_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass
