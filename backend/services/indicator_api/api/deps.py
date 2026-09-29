"""Dependencies shared by the routers."""

from __future__ import annotations

from fastapi import Request

from services.indicator_api.stub.store import StubStore


def get_store(request: Request) -> StubStore:
    # One store per app instance, so each test client starts from the seed data.
    store: StubStore = request.app.state.store
    return store
