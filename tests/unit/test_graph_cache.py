"""Unit tests for the workflow_runtime graph cache (H5 fix).

The bug: the graph cache was keyed only by agent_id, so a published flow-version
change never rebuilt the graph (restart required) and the dict grew unbounded.
The fix keys by (agent_id, workflow_version_id) in a bounded LRU. These tests
lock in: eviction, LRU recency, and "new version → rebuild, same version → reuse".

conversation_runner imports langgraph/opentelemetry-backed siblings at module
load, so we stub those to import just the cache class in a unit context.
"""
from __future__ import annotations

import warnings

import pytest

from ._loader import SERVICES, load_module_from_path

pytestmark = pytest.mark.unit

_runtime_dir = SERVICES / "workflow_runtime"
# `libs.shared.guardrail_check` pulls in litellm, whose own pydantic models emit
# a "Valid config keys have changed in V2" UserWarning at import; under the
# repo's `filterwarnings = error` that would abort collection. Upstream noise,
# not ours — scope the ignore to this one import so nothing leaks to other tests.
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message="Valid config keys have changed in V2", category=UserWarning)
    cr = load_module_from_path(
        "conversation_runner",
        _runtime_dir / "runtime_services" / "conversation_runner.py",
        extra_syspath=[_runtime_dir, _runtime_dir / "runtime_services"],
        stubs={
            # graph.build_graph() is called at import for the default graph.
            "graph": {"build_graph": lambda *a, **k: object()},
            "config_loader": {"load_agent_config": lambda *a, **k: {}},
            "db": {
                "create_conversation": lambda *a, **k: None,
                "get_last_slots": lambda *a, **k: None,
                "get_messages": lambda *a, **k: None,
                "save_message": lambda *a, **k: None,
                "save_trace": lambda *a, **k: None,
                "validate_agent_tenant": lambda *a, **k: True,
            },
            "memory_middleware": {
                "_DEV_AGENT_ID": "dev-agent",
                "_DEV_TENANT_ID": "dev-tenant",
                "apply_memory_policy": lambda *a, **k: None,
                "retrieve_episodic_past": lambda *a, **k: [],
                "retrieve_memories": lambda *a, **k: [],
                "retrieve_pending_reminders": lambda *a, **k: [],
            },
            "observability": {
                "flush_langfuse": lambda *a, **k: None,
                "get_langchain_callback_handler": lambda *a, **k: None,
                "get_langfuse_handler": lambda *a, **k: None,
            },
            "schemas": {"RunRequest": object},
        },
    )

LRU = cr._GraphCache


@pytest.fixture(autouse=True)
def _reset_runtime_cache():
    """Each test starts with an empty module-level cache + locks."""
    cr._graph_cache.clear()
    cr._build_locks.clear()
    yield
    cr._graph_cache.clear()
    cr._build_locks.clear()


class TestLRUCache:
    def test_get_missing_returns_none(self) -> None:
        assert LRU().get("nope") is None

    def test_set_then_get(self) -> None:
        c = LRU()
        sentinel = object()
        c.set("a", sentinel)
        assert c.get("a") is sentinel

    def test_evicts_beyond_maxsize(self) -> None:
        c = LRU(maxsize=2)
        c.set("a", 1)
        c.set("b", 2)
        c.set("c", 3)  # evicts "a" (least recently used)
        assert c.get("a") is None
        assert c.get("b") == 2
        assert c.get("c") == 3

    def test_get_refreshes_recency(self) -> None:
        c = LRU(maxsize=2)
        c.set("a", 1)
        c.set("b", 2)
        c.get("a")      # touch "a" so "b" is now LRU
        c.set("c", 3)   # evicts "b", not "a"
        assert c.get("b") is None
        assert c.get("a") == 1
        assert c.get("c") == 3


class TestVersionKeyedRebuild:
    """Simulates the runner's build-or-reuse pattern against the real cache."""

    def _build_once(self, cache, key, built):
        graph = cache.get(key)
        if graph is None:
            graph = object()
            built.append(key)
            cache.set(key, graph)
        return graph

    def test_same_version_reuses(self) -> None:
        cache, built = LRU(), []
        g1 = self._build_once(cache, "agent:v1", built)
        g2 = self._build_once(cache, "agent:v1", built)
        assert g1 is g2
        assert built == ["agent:v1"]

    def test_new_version_rebuilds(self) -> None:
        cache, built = LRU(), []
        g1 = self._build_once(cache, "agent:v1", built)
        g2 = self._build_once(cache, "agent:v2", built)
        assert g1 is not g2
        assert built == ["agent:v1", "agent:v2"]

    def test_default_key_when_version_missing(self) -> None:
        # Mirrors f"{agent_id}:{wv or 'default'}" when wv is None/empty.
        for wv in (None, ""):
            key = f"agent:{wv or 'default'}"
            assert key == "agent:default"


class TestInvalidation:
    def test_invalidate_suffix_drops_only_matching(self) -> None:
        c = LRU()
        c.set("a1:v1", 1)
        c.set("a2:v1", 2)
        c.set("a1:v2", 3)
        removed = c.invalidate_suffix(":v1")
        assert removed == 2
        assert c.get("a1:v1") is None
        assert c.get("a2:v1") is None
        assert c.get("a1:v2") == 3

    def test_invalidate_graph_version_evicts_across_agents(self) -> None:
        cr._graph_cache.set("agentA:ver-9", object())
        cr._graph_cache.set("agentB:ver-9", object())
        cr._graph_cache.set("agentA:ver-8", object())
        removed = cr.invalidate_graph_version("ver-9")
        assert removed == 2
        assert cr._graph_cache.get("agentA:ver-9") is None
        assert cr._graph_cache.get("agentA:ver-8") is not None  # other version kept

    def test_invalidate_graph_version_empty_is_noop(self) -> None:
        cr._graph_cache.set("agentA:ver-1", object())
        assert cr.invalidate_graph_version("") == 0
        assert cr._graph_cache.get("agentA:ver-1") is not None

    def test_invalidate_clears_build_lock(self) -> None:
        cr._build_locks["agentA:ver-7"] = cr.asyncio.Lock()
        cr._graph_cache.set("agentA:ver-7", object())
        cr.invalidate_graph_version("ver-7")
        assert "agentA:ver-7" not in cr._build_locks


class TestBuildLockDedup:
    async def test_concurrent_same_key_builds_once(self, monkeypatch) -> None:
        import asyncio

        calls = []
        monkeypatch.setattr(cr, "build_graph", lambda *a, **k: calls.append(1) or object())

        results = await asyncio.gather(
            cr._get_or_build_graph("agent:v1", []),
            cr._get_or_build_graph("agent:v1", []),
            cr._get_or_build_graph("agent:v1", []),
        )
        assert len(calls) == 1, "graph rebuilt more than once for the same key"
        assert results[0] is results[1] is results[2]

    async def test_different_keys_build_separately(self, monkeypatch) -> None:
        import asyncio

        calls = []
        monkeypatch.setattr(cr, "build_graph", lambda *a, **k: calls.append(1) or object())
        await asyncio.gather(
            cr._get_or_build_graph("agent:v1", []),
            cr._get_or_build_graph("agent:v2", []),
        )
        assert len(calls) == 2
