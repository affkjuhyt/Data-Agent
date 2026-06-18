"""Unit tests for shared.cache_invalidation (Phase 2 pub/sub).

Redis isn't available in unit tests, so we inject a fake `redis.asyncio` module
(the real client is imported lazily inside the functions). We assert: publish is
best-effort (True/False, never raises), and the listener dispatches each
`message`-type payload to the callback while ignoring control frames.
"""
from __future__ import annotations

import asyncio
import sys
import types

import pytest

from services.shared.cache_invalidation import (
    GRAPH_INVALIDATE_CHANNEL,
    publish_graph_invalidation,
    run_graph_invalidation_listener,
)

pytestmark = pytest.mark.unit


def _install_fake_redis(monkeypatch, *, messages=None, publish_sink=None, publish_raises=False):
    fake = types.ModuleType("redis.asyncio")

    class FakePubSub:
        async def subscribe(self, channel):
            assert channel == GRAPH_INVALIDATE_CHANNEL

        async def listen(self):
            for m in messages or []:
                yield m
            raise asyncio.CancelledError()  # ends the listener loop deterministically

        async def aclose(self):
            pass

    class FakeRedis:
        def pubsub(self):
            return FakePubSub()

        async def publish(self, channel, data):
            if publish_raises:
                raise RuntimeError("redis down")
            if publish_sink is not None:
                publish_sink.append((channel, data))

        async def aclose(self):
            pass

    fake.from_url = lambda url, **kw: FakeRedis()
    monkeypatch.setitem(sys.modules, "redis", types.ModuleType("redis"))
    monkeypatch.setitem(sys.modules, "redis.asyncio", fake)


class TestPublish:
    async def test_publishes_message(self, monkeypatch) -> None:
        sink = []
        _install_fake_redis(monkeypatch, publish_sink=sink)
        ok = await publish_graph_invalidation("redis://x", "ver-1")
        assert ok is True
        assert sink == [(GRAPH_INVALIDATE_CHANNEL, "ver-1")]

    async def test_empty_args_are_noop(self) -> None:
        assert await publish_graph_invalidation("", "ver-1") is False
        assert await publish_graph_invalidation("redis://x", "") is False

    async def test_redis_failure_is_swallowed(self, monkeypatch) -> None:
        _install_fake_redis(monkeypatch, publish_raises=True)
        assert await publish_graph_invalidation("redis://x", "ver-1") is False


class TestListener:
    async def test_dispatches_message_payloads_only(self, monkeypatch) -> None:
        _install_fake_redis(
            monkeypatch,
            messages=[
                {"type": "subscribe", "data": 1},        # control frame — ignored
                {"type": "message", "data": b"ver-1"},   # bytes payload
                {"type": "message", "data": "ver-2"},    # str payload
                {"type": "message", "data": b""},        # empty — ignored
            ],
        )
        seen = []
        with pytest.raises(asyncio.CancelledError):
            await run_graph_invalidation_listener("redis://x", seen.append)
        assert seen == ["ver-1", "ver-2"]

    async def test_disabled_when_no_url(self) -> None:
        # Returns immediately, never touches redis.
        await run_graph_invalidation_listener("", lambda wv: None)

    async def test_callback_error_does_not_kill_listener(self, monkeypatch) -> None:
        _install_fake_redis(
            monkeypatch,
            messages=[
                {"type": "message", "data": b"boom"},
                {"type": "message", "data": b"ver-ok"},
            ],
        )
        seen = []

        def cb(wv):
            if wv == "boom":
                raise ValueError("handler blew up")
            seen.append(wv)

        with pytest.raises(asyncio.CancelledError):
            await run_graph_invalidation_listener("redis://x", cb)
        assert seen == ["ver-ok"]  # processing continued past the failing message
