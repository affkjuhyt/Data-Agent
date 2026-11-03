"""The worker's side of the ml-forecast service: a series goes in, one FitResult comes back.

A 422 is the service refusing the series (its message is the ValueError `fit` raised, already
in Vietnamese) and becomes the same ValueError here, so a caller handles an in-process fit and a
remote one alike. Anything else that goes wrong (no answer, a 5xx, a body of the wrong shape or
size) is TrainerUnavailable: the series may be fine, the service is not.

The answer is read as untrusted: the body is capped while it is read and its shape checked before
anything in it is used."""

from __future__ import annotations

import datetime as dt
import json
import math
from typing import Any, Literal

import httpx

from .shapes import Candidate, FitResult, Forecast, Point

# A fit of a long series runs a few seconds to tens of seconds (five models, a dozen cutoffs).
REQUEST_TIMEOUT_S = 60.0
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_ITEMS = 1000
MAX_PARAMS_DEPTH = 4
MAX_PARAMS_KEYS = 200
UNAVAILABLE = "Dịch vụ dự báo không phản hồi"


class TrainerUnavailable(RuntimeError):
    """The forecast service could not give an answer (not: it refused the series)."""


def _number(value: Any, *, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError("number")
    return float(value)


def _date(value: Any) -> dt.date:
    if not isinstance(value, str):
        raise ValueError("date")
    return dt.date.fromisoformat(value)


def _text(value: Any, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise ValueError("text")
    return value


def _items(value: Any) -> list[Any]:
    if not isinstance(value, list) or len(value) > MAX_ITEMS:
        raise ValueError("list")
    return value


def _reject_constant(name: str) -> Any:
    raise ValueError(name)


def _json_value(value: Any, depth: int, budget: list[int]) -> Any:
    """A finite JSON scalar, or objects and lists of them: at most MAX_PARAMS_DEPTH containers deep and
    MAX_PARAMS_KEYS keys and items in all. `params` goes into a jsonb column later."""
    budget[0] -= 1
    if budget[0] < 0 or (isinstance(value, dict | list) and depth > MAX_PARAMS_DEPTH):
        raise ValueError("params")
    if isinstance(value, dict):
        if not all(isinstance(k, str) for k in value):
            raise ValueError("params")
        return {k: _json_value(v, depth + 1, budget) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_value(v, depth + 1, budget) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("params")
    if value is None or isinstance(value, str | bool | int | float):
        return value
    raise ValueError("params")


def _shaped(body: Any) -> FitResult:
    """FitResult from the decoded JSON, or ValueError/TypeError/KeyError when it is not one."""
    if not isinstance(body, dict) or not isinstance(body["params"], dict):
        raise ValueError("object")
    params = _json_value(body["params"], 1, [MAX_PARAMS_KEYS])
    return FitResult(
        chosen=_text(body["chosen"], optional=True),
        reason=_text(body["reason"], optional=True),
        candidates=[
            Candidate(
                algorithm=_text(c["algorithm"]) or "",
                mase=_number(c["mase"], optional=True),
                smape=_number(c["smape"], optional=True),
                coverage80=_number(c["coverage80"], optional=True),
                coverage95=_number(c["coverage95"], optional=True),
                in_sample_mase=_number(c["in_sample_mase"], optional=True),
            )
            for c in _items(body["candidates"])
        ],
        cutoffs=[_date(c) for c in _items(body["cutoffs"])],
        predictions=[
            Forecast(
                period=_date(p["period"]),
                point=_number(p["point"]) or 0.0,
                lo80=_number(p["lo80"]) or 0.0,
                hi80=_number(p["hi80"]) or 0.0,
                lo95=_number(p["lo95"]) or 0.0,
                hi95=_number(p["hi95"]) or 0.0,
            )
            for p in _items(body["predictions"])
        ],
        interval_method=_text(body["interval_method"], optional=True),
        params=params,
    )


async def fit_remote(
    url: str,
    series: list[Point],
    *,
    frequency: Literal["month", "quarter"],
    horizon: int,
) -> FitResult:
    payload = {
        "series": [{"period": p.period.isoformat(), "value": p.value} for p in series],
        "frequency": frequency,
        "horizon": horizon,
    }
    try:
        async with (
            httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client,
            client.stream("POST", url.rstrip("/") + "/fit", json=payload) as response,
        ):
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > MAX_RESPONSE_BYTES:
                    raise TrainerUnavailable(UNAVAILABLE)
            status = response.status_code
    except httpx.HTTPError as exc:
        raise TrainerUnavailable(UNAVAILABLE) from exc
    try:
        decoded = json.loads(body, parse_constant=_reject_constant)
    except ValueError as exc:
        raise TrainerUnavailable(UNAVAILABLE) from exc
    if status == 422:
        # The service's own refusal: the ValueError `fit` raised, passed on as it was.
        detail = decoded.get("detail") if isinstance(decoded, dict) else None
        if isinstance(detail, str):
            raise ValueError(detail)
        raise TrainerUnavailable(UNAVAILABLE)
    if status != 200:
        raise TrainerUnavailable(UNAVAILABLE)
    try:
        return _shaped(decoded)
    except (ValueError, TypeError, KeyError) as exc:
        raise TrainerUnavailable(UNAVAILABLE) from exc
