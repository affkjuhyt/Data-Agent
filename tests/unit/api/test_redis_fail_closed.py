"""PL-08: the guards Redis backs fail closed. Without a store, or with one that does not answer, a
write gets 503 source_unavailable with Retry-After before it runs; a read still goes through.
API_REDIS_FAIL_OPEN=true (every other api test) keeps the earlier fail-open."""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.config import Settings
from services.api.kernel import ratelimit
from services.api.kernel.problems import Problem

from .conftest import TOKENS, USERS, FakeIdentities, FakeVerifier, bearer, probe_router

pytestmark = [pytest.mark.unit]


class DownCounters:
    async def hit(self, key: str, ttl_seconds: int) -> int:
        raise ConnectionError("Connection refused")


class UpCounters:
    async def hit(self, key: str, ttl_seconds: int) -> int:
        return 1


class DownResponses:
    def __init__(self) -> None:
        self.puts = 0

    async def put(self, ref: str, value: str, ttl: datetime.timedelta) -> None:
        self.puts += 1
        raise ConnectionError("Connection refused")

    async def get(self, ref: str) -> str | None:
        raise ConnectionError("Connection refused")

    async def ping(self) -> None:
        raise ConnectionError("Connection refused")


def api(**stores: Any) -> TestClient:
    app: FastAPI = create_app(
        Settings(),
        token_verifier=FakeVerifier(TOKENS),
        identity_resolver=FakeIdentities(USERS),
        **stores,
    )
    probes = probe_router()
    app.include_router(probes)
    n = len(probes.routes)
    app.router.routes[:] = app.router.routes[-n:] + app.router.routes[:-n]
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("API_REDIS_FAIL_OPEN", raising=False)


def refused(r: Any) -> bool:
    return (
        r.status_code == 503
        and r.json()["code"] == "source_unavailable"
        and int(r.headers["Retry-After"]) > 0
    )


@pytest.mark.parametrize("value", ["", "false", "0", "no"])
def test_anything_but_true_is_fail_closed(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    from services.api.kernel.redis_client import fail_open

    monkeypatch.setenv("API_REDIS_FAIL_OPEN", value)
    assert not fail_open()
    monkeypatch.setenv("API_REDIS_FAIL_OPEN", "True")
    assert fail_open()


def test_without_redis_a_write_is_refused_and_a_read_goes_through(closed: None) -> None:
    c = api()
    assert refused(c.post("/v1/metrics", headers=bearer("steward")))
    assert refused(c.post("/v1/metrics", headers={**bearer("steward"), "Idempotency-Key": "k-1"}))
    assert c.get("/v1/skills", headers=bearer("asker")).status_code == 200


def test_a_store_that_is_down_refuses_writes_too(closed: None) -> None:
    down = DownResponses()
    c = api(counter_store=DownCounters(), response_store=down)
    assert refused(c.post("/v1/metrics", headers=bearer("steward")))
    assert c.get("/v1/skills", headers=bearer("asker")).status_code == 200
    # The counters answer but the response store does not: the keyed write is not run either.
    c = api(counter_store=UpCounters(), response_store=down)
    assert refused(c.post("/v1/metrics", headers={**bearer("steward"), "Idempotency-Key": "k-2"}))
    assert down.puts == 0
    # An unkeyed write needs only the counters.
    assert c.post("/v1/metrics", headers=bearer("steward")).status_code == 200


def test_an_unauthenticated_write_is_still_401_not_503(closed: None) -> None:
    r = api().post("/v1/metrics", headers={"Idempotency-Key": "k-3"})
    assert r.status_code == 401


def test_fail_open_skips_the_guards_as_before() -> None:
    c = api(counter_store=DownCounters(), response_store=DownResponses())
    assert c.post("/v1/metrics", headers=bearer("steward")).status_code == 200
    assert (
        api()
        .post("/v1/metrics", headers={**bearer("steward"), "Idempotency-Key": "k-4"})
        .status_code
        == 200
    )


class _App:
    def __init__(self, store: Any) -> None:
        self.state = type("State", (), {"counter_store": store})()


class _Request:
    def __init__(self, store: Any) -> None:
        self.app = _App(store)


@pytest.mark.parametrize("store", [None, DownCounters()])
async def test_a_quota_refuses_without_its_counter(closed: None, store: Any) -> None:
    with pytest.raises(Problem) as raised:
        await ratelimit.within_quota(_Request(store), "questions", "u", 10)  # type: ignore[arg-type]
    assert raised.value.status == 503 and raised.value.code == "source_unavailable"


@pytest.mark.parametrize("store", [None, DownCounters()])
async def test_a_quota_counts_nothing_when_fail_open(store: Any) -> None:
    await ratelimit.within_quota(_Request(store), "questions", "u", 10)  # type: ignore[arg-type]
