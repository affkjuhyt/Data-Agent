"""Read-only proxy onto the LiteLLM gateway: model catalogue, health, spend.

Every function returns a dict the router can hand straight back, with
`available` false (and empty lists / zero totals) whenever LiteLLM is
unreachable, unauthenticated, or has the feature disabled. Numbers are never
invented: an empty spend series means LiteLLM reported no spend, not that we
guessed.

Auth: LiteLLM's admin routes (/model/info, /health, /spend/*) want the master
key; a virtual key (LITELLM_API_KEY) is the fallback and works when that key
has been granted the routes.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import time
from collections import defaultdict
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_HEALTH_CACHE_TTL_S = 60.0
_MODELS_TIMEOUT_S = 10.0
# /health actually calls each configured model, so it is slow by design.
_HEALTH_TIMEOUT_S = 30.0
_SPEND_TIMEOUT_S = 20.0

_health_cache: dict[str, Any] = {"at": 0.0, "payload": None}
_health_lock = asyncio.Lock()


def _base_url() -> str:
    return os.environ.get("LITELLM_BASE_URL", "").rstrip("/")


def _api_key() -> str:
    return os.environ.get("LITELLM_MASTER_KEY") or os.environ.get("LITELLM_API_KEY") or ""


def _headers() -> dict[str, str]:
    key = _api_key()
    return {"Authorization": f"Bearer {key}"} if key else {}


async def _get(path: str, *, params: dict[str, Any] | None = None, timeout_s: float) -> Any:
    base = _base_url()
    if not base:
        raise RuntimeError("LITELLM_BASE_URL not set")
    async with httpx.AsyncClient(timeout=timeout_s) as http:
        resp = await http.get(f"{base}{path}", params=params, headers=_headers())
    resp.raise_for_status()
    return resp.json()


def _error_text(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        body = e.response.text[:200].replace("\n", " ")
        return f"LiteLLM HTTP {e.response.status_code}: {body}"
    return f"{type(e).__name__}: {e}"[:300]


# ─── Models ───────────────────────────────────────────────────────────────────

def _model_row(entry: dict[str, Any]) -> dict[str, Any]:
    params = entry.get("litellm_params") or {}
    info = entry.get("model_info") or {}
    base_model = params.get("model") or info.get("base_model")
    provider = info.get("litellm_provider")
    if not provider and isinstance(base_model, str) and "/" in base_model:
        provider = base_model.split("/", 1)[0]
    return {
        "model_name": entry.get("model_name"),
        "litellm_provider": provider,
        "base_model": base_model,
        "max_tokens": info.get("max_tokens") or info.get("max_output_tokens"),
        "input_cost_per_token": info.get("input_cost_per_token"),
        "output_cost_per_token": info.get("output_cost_per_token"),
        "mode": info.get("mode"),
    }


async def list_models() -> dict[str, Any]:
    try:
        payload = await _get("/model/info", timeout_s=_MODELS_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 — availability is the answer, not an error
        logger.warning("litellm /model/info unavailable: %s", _error_text(e))
        return {"items": [], "available": False, "error": _error_text(e)}
    data = payload.get("data") if isinstance(payload, dict) else payload
    items = [_model_row(m) for m in (data or []) if isinstance(m, dict)]
    return {"items": items, "available": True}


# ─── Health ───────────────────────────────────────────────────────────────────

def _health_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for e in payload.get("healthy_endpoints") or []:
        items.append({
            "model_name": e.get("model_name") or e.get("model"),
            "status": "ok",
            # LiteLLM /health reports pass/fail per endpoint, not timing.
            "latency_ms": None,
            "error": None,
        })
    for e in payload.get("unhealthy_endpoints") or []:
        err = e.get("error")
        items.append({
            "model_name": e.get("model_name") or e.get("model"),
            "status": "down",
            "latency_ms": None,
            "error": str(err)[:500] if err else "unhealthy",
        })
    return items


async def models_health(*, force: bool = False) -> dict[str, Any]:
    now = time.monotonic()
    cached = _health_cache["payload"]
    if cached is not None and not force and now - _health_cache["at"] < _HEALTH_CACHE_TTL_S:
        return cached
    async with _health_lock:
        cached = _health_cache["payload"]
        if cached is not None and not force and time.monotonic() - _health_cache["at"] < _HEALTH_CACHE_TTL_S:
            return cached
        try:
            payload = await _get("/health", timeout_s=_HEALTH_TIMEOUT_S)
            result = {"items": _health_items(payload if isinstance(payload, dict) else {}), "available": True}
        except Exception as e:  # noqa: BLE001
            logger.warning("litellm /health unavailable: %s", _error_text(e))
            result = {"items": [], "available": False, "error": _error_text(e)}
        _health_cache["payload"] = result
        _health_cache["at"] = time.monotonic()
        return result


def reset_health_cache() -> None:
    _health_cache["payload"] = None
    _health_cache["at"] = 0.0


# ─── Spend ────────────────────────────────────────────────────────────────────

def _empty_spend(note: str, available: bool = False) -> dict[str, Any]:
    return {
        "currency": "USD",
        "available": available,
        "total": 0.0,
        "series": [],
        "by_model": [],
        "note": note,
    }


def _day_of(raw: Any) -> str | None:
    if not raw:
        return None
    s = str(raw)
    return s[:10] if len(s) >= 10 else None


def aggregate_spend_logs(rows: list[dict[str, Any]], start: dt.date, end: dt.date) -> dict[str, Any]:
    """Fold LiteLLM /spend/logs rows (one per request) into per-day and per-model totals."""
    by_day: dict[str, dict[str, float]] = defaultdict(lambda: {"spend": 0.0, "tokens": 0})
    by_model: dict[str, dict[str, float]] = defaultdict(lambda: {"spend": 0.0, "tokens": 0})
    total = 0.0
    for r in rows:
        if not isinstance(r, dict):
            continue
        spend = float(r.get("spend") or 0.0)
        tokens = int(r.get("total_tokens") or 0)
        day = _day_of(r.get("startTime") or r.get("start_time") or r.get("date"))
        model = r.get("model") or r.get("model_group") or "unknown"
        total += spend
        if day:
            by_day[day]["spend"] += spend
            by_day[day]["tokens"] += tokens
        by_model[model]["spend"] += spend
        by_model[model]["tokens"] += tokens

    series = []
    d = start
    while d <= end:
        key = d.isoformat()
        series.append({
            "date": key,
            "spend": round(by_day[key]["spend"], 6) if key in by_day else 0.0,
            "tokens": int(by_day[key]["tokens"]) if key in by_day else 0,
        })
        d += dt.timedelta(days=1)
    models = sorted(
        ({"model": m, "spend": round(v["spend"], 6), "tokens": int(v["tokens"])} for m, v in by_model.items()),
        key=lambda x: x["spend"], reverse=True,
    )
    return {"total": round(total, 6), "series": series, "by_model": models}


def aggregate_spend_report(rows: list[dict[str, Any]], start: dt.date, end: dt.date) -> dict[str, Any]:
    """Fold /global/spend/report rows ({group_by_day, total_spend, breakdown}) — no token or model detail."""
    by_day: dict[str, float] = defaultdict(float)
    total = 0.0
    for r in rows:
        if not isinstance(r, dict):
            continue
        day = _day_of(r.get("group_by_day"))
        spend = float(r.get("total_spend") or 0.0)
        total += spend
        if day:
            by_day[day] += spend
    series = []
    d = start
    while d <= end:
        key = d.isoformat()
        series.append({"date": key, "spend": round(by_day.get(key, 0.0), 6), "tokens": 0})
        d += dt.timedelta(days=1)
    return {"total": round(total, 6), "series": series, "by_model": []}


async def spend(days: int = 30) -> dict[str, Any]:
    if not _base_url():
        return _empty_spend("LITELLM_BASE_URL not set; spend tracking unavailable")
    if not _api_key():
        return _empty_spend("LITELLM_MASTER_KEY/LITELLM_API_KEY not set; LiteLLM spend endpoints require a key")

    end = dt.datetime.now(dt.UTC).date()
    start = end - dt.timedelta(days=max(days, 1) - 1)
    params = {"start_date": start.isoformat(), "end_date": end.isoformat()}

    # Per-request logs carry model + tokens — the richest source when LiteLLM
    # has a database attached (spend logging is DB-backed).
    try:
        rows = await _get("/spend/logs", params=params, timeout_s=_SPEND_TIMEOUT_S)
        if isinstance(rows, list):
            agg = aggregate_spend_logs(rows, start, end)
            return {
                "currency": "USD", "available": True, **agg,
                "note": f"LiteLLM /spend/logs, {start.isoformat()}..{end.isoformat()} (UTC)",
            }
        first_error = f"unexpected /spend/logs payload type {type(rows).__name__}"
    except Exception as e:  # noqa: BLE001
        first_error = _error_text(e)

    # Daily report is available even where per-request logs are not exposed.
    try:
        rows = await _get("/global/spend/report", params=params, timeout_s=_SPEND_TIMEOUT_S)
        if isinstance(rows, list):
            agg = aggregate_spend_report(rows, start, end)
            return {
                "currency": "USD", "available": True, **agg,
                "note": (
                    f"LiteLLM /global/spend/report, {start.isoformat()}..{end.isoformat()} (UTC); "
                    "per-model and token breakdown not available via this endpoint "
                    f"(/spend/logs failed: {first_error})"
                ),
            }
        second_error = f"unexpected /global/spend/report payload type {type(rows).__name__}"
    except Exception as e:  # noqa: BLE001
        second_error = _error_text(e)

    logger.warning("litellm spend unavailable: %s | %s", first_error, second_error)
    return _empty_spend(
        "LiteLLM spend tracking unreachable or not enabled (needs LiteLLM DATABASE_URL + master key). "
        f"/spend/logs: {first_error}; /global/spend/report: {second_error}"
    )
