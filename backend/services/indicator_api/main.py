"""FastAPI entrypoint. The stub routers of P0-09 are added in api/."""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="indicator-api", version="0.1.0")


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    return {"status": "ok"}
