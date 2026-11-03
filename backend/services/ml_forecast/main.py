"""ml-forecast: fits a forecast for a series worker-agent sends (libs/forecast_fit). Stateless:
no database, no secret, no call to any other service; its NetworkPolicy admits worker-agent in
and lets nothing out.

POST /fit {"series": [{"period": "2024-01-01", "value": 1.0}, ...], "frequency": "month",
           "horizon": 6} → the FitResult as JSON; 422 with the reason when the series is refused.
GET /healthz → 200
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
from dataclasses import asdict
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from libs.forecast_fit import Point, fit

# 50 years of months; the longest a backtest of five models should be asked to chew.
MAX_POINTS = 600
MAX_HORIZON = 60

app = FastAPI(title="ml-forecast")
# One fit at a time: five models over a dozen cutoffs is CPU- and memory-heavy, and the pod is
# small. Concurrent callers wait their turn (the client's timeout bounds the wait); scale with
# replicas, not with this.
# An asyncio semaphore: a waiter that is cancelled leaves nothing held.
SLOTS = asyncio.BoundedSemaphore(int(os.environ.get("ML_FORECAST_CONCURRENCY", "1")))


class PointIn(BaseModel):
    period: dt.date
    value: float = Field(allow_inf_nan=False)


class FitIn(BaseModel):
    series: list[PointIn] = Field(max_length=MAX_POINTS)
    frequency: Literal["month", "quarter"]
    horizon: int = Field(ge=1, le=MAX_HORIZON)


@app.get("/healthz")
async def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.post("/fit")
async def run_fit(body: FitIn, request: Request) -> JSONResponse:
    series = [Point(p.period, p.value) for p in body.series]
    async with SLOTS:
        # The client's httpx timeout is per phase (connect, write, read), not a deadline for
        # the call, so a caller can wait here past it and leave; its fit would be wasted work.
        if await request.is_disconnected():
            return JSONResponse({"detail": "caller gone"}, status_code=499)
        try:
            result = await asyncio.to_thread(
                fit, series, frequency=body.frequency, horizon=body.horizon
            )
        except ValueError as exc:
            # The series is the problem, not the service: the reason is for the person asking.
            return JSONResponse({"detail": str(exc)}, status_code=422)
    return JSONResponse(jsonable_encoder(asdict(result)))


def main() -> None:
    import uvicorn

    uvicorn.run(
        app, host="0.0.0.0", port=int(os.environ.get("ML_FORECAST_PORT", "8150")), workers=1
    )


if __name__ == "__main__":
    main()
