"""workflow_runtime platform/billing/llm/conversations/traces endpoints.

These back the admin dashboards that previously rendered sample data. Nothing
here touches Postgres or LiteLLM: the DB client and outbound HTTP are stubbed,
and what is pinned is the contract shape — zero-filled series, {items, total}
listings, "available": false on an unreachable gateway, and probes that report
instead of raising.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import pathlib
import sys
import types
from contextlib import asynccontextmanager
from typing import Any

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "workflow_runtime")

pytestmark = pytest.mark.unit

pytest.importorskip("fastapi", reason="fastapi not installed in this environment")
pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed in this environment")

TENANT = "00000000-0000-0000-0000-0000000000aa"
CALLER = {"user_id": "u1", "user_name": "u1@example.com", "roles": ["tenant-admin"], "tenant_id": TENANT}


# Top-level module names this service shares with other services (auth_api and
# kb_backend also have `routers`, `auth`, `db`, `config`…). Tests of those
# services leave their copies in sys.modules; importing ours would then silently
# resolve to theirs ("cannot import name 'billing' from 'routers'").
_SHARED_TOPLEVEL = ("routers", "auth", "db", "config", "schemas", "runtime_services", "node_registry", "nodes")


def _evict_foreign_modules() -> None:
    for name in list(sys.modules):
        if name.split(".")[0] not in _SHARED_TOPLEVEL:
            continue
        file = getattr(sys.modules[name], "__file__", None) or ""
        if file and not file.startswith(_SVC_DIR):
            del sys.modules[name]


@pytest.fixture(autouse=True)
def _sys_path():
    # This service dir must be FIRST: other tests leave sibling service dirs
    # (auth_api, kb_backend…) on sys.path, and a later entry would lose the
    # `routers` / `db` / `auth` lookup to them.
    was_present = _SVC_DIR in sys.path
    if was_present:
        sys.path.remove(_SVC_DIR)
    sys.path.insert(0, _SVC_DIR)
    _evict_foreign_modules()
    yield
    if not was_present and _SVC_DIR in sys.path:
        sys.path.remove(_SVC_DIR)


def _queries():
    import services.workflow_runtime.runtime_services.platform_queries as m
    return m


# ─── DB stub ──────────────────────────────────────────────────────────────────

class _Row:
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)

    def __iter__(self):
        return iter(self.__dict__.values())


class _Result:
    def __init__(self, rows: list[Any] | None = None, scalar: Any = 0, one: Any = None) -> None:
        self._rows = rows or []
        self._scalar = scalar
        self._one = one

    def fetchall(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._scalar

    def one(self):
        return self._one


class _StubClient:
    """Answers each execute() from a queue; records compiled statements."""

    def __init__(self, results: list[_Result], connected: bool = True) -> None:
        self.results = list(results)
        self.connected = connected
        self.statements: list[Any] = []
        self.tenants: list[str | None] = []

    def is_connected(self) -> bool:
        return self.connected

    @asynccontextmanager
    async def get_client(self, tenant_id: str | None = None):
        self.tenants.append(tenant_id)
        yield self

    async def execute(self, stmt: Any, *_a: Any) -> _Result:
        # Compile against the Postgres dialect so a malformed construct fails here.
        from sqlalchemy.dialects import postgresql
        stmt.compile(dialect=postgresql.dialect())
        self.statements.append(stmt)
        return self.results.pop(0) if self.results else _Result()

    async def commit(self) -> None:
        pass


# ─── Conversations list ───────────────────────────────────────────────────────

class TestListConversations:
    def test_not_connected_is_empty_not_sample(self, monkeypatch):
        q = _queries()
        monkeypatch.setattr(q, "client", _StubClient([], connected=False))
        out = asyncio.run(q.list_conversations(TENANT))
        assert out == {"items": [], "total": 0}

    def test_row_shape_and_tenant_scoping(self, monkeypatch):
        q = _queries()
        now = dt.datetime(2026, 8, 30, 10, 0, tzinfo=dt.UTC)
        import uuid
        conv_id, agent_id = uuid.uuid4(), uuid.uuid4()
        row = _Row(
            id=conv_id, user_ref="alice", channel="web", status="active",
            started_at=None, ended_at=None, inserted_at=now,
            message_count=4, last_message_at=now, tokens=1234, avg_latency_ms=512.25,
            agent_id=agent_id, agent_name="Sales Agent",
        )
        stub = _StubClient([_Result(rows=[row]), _Result(scalar=1)])
        monkeypatch.setattr(q, "client", stub)

        out = asyncio.run(q.list_conversations(TENANT, limit=10, offset=0, status="active", q="ali", agent_id=str(agent_id)))

        assert out["total"] == 1
        item = out["items"][0]
        assert item == {
            "id": str(conv_id), "user_ref": "alice", "channel": "web", "status": "active",
            "started_at": now.isoformat(), "ended_at": None, "message_count": 4,
            "last_message_at": now.isoformat(), "agent_id": str(agent_id), "agent_name": "Sales Agent",
            "tokens": 1234, "avg_latency_ms": 512.2,
        }
        assert stub.tenants == [TENANT]
        from sqlalchemy.dialects import postgresql
        sql = str(stub.statements[0].compile(dialect=postgresql.dialect()))
        assert '"Conversations".tenant_id =' in sql
        assert '"AgentVersions"' in sql and "->>" in sql  # agent derived from message metadata

    def test_agent_absent_is_null(self, monkeypatch):
        q = _queries()
        import uuid
        row = _Row(
            id=uuid.uuid4(), user_ref=None, channel=None, status="active",
            started_at=None, ended_at=None, inserted_at=dt.datetime.now(dt.UTC),
            message_count=None, last_message_at=None, tokens=None, avg_latency_ms=None,
            agent_id=None, agent_name=None,
        )
        monkeypatch.setattr(q, "client", _StubClient([_Result(rows=[row]), _Result(scalar=1)]))
        item = asyncio.run(q.list_conversations(TENANT))["items"][0]
        assert item["agent_id"] is None and item["agent_name"] is None
        assert item["message_count"] == 0 and item["tokens"] == 0 and item["avg_latency_ms"] is None

    def test_bad_agent_id_filter_is_empty(self, monkeypatch):
        q = _queries()
        monkeypatch.setattr(q, "client", _StubClient([]))
        assert asyncio.run(q.list_conversations(TENANT, agent_id="not-a-uuid")) == {"items": [], "total": 0}


class TestMessagesWithTraces:
    def test_foreign_conversation_is_empty(self, monkeypatch):
        q = _queries()
        import uuid
        stub = _StubClient([_Result(rows=[])])  # ownership check finds nothing
        monkeypatch.setattr(q, "client", stub)
        assert asyncio.run(q.get_messages_with_traces(str(uuid.uuid4()), TENANT)) == []

    def test_traces_attached_and_legacy_fields_kept(self, monkeypatch):
        q = _queries()
        import uuid
        conv_id, m1, m2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        t0 = dt.datetime(2026, 8, 30, tzinfo=dt.UTC)
        msgs = [
            _Row(id=m1, role="user", content={"text": "hi"}, msg_metadata=None, created_at=t0, tokens_used=0, latency_ms=0),
            _Row(id=m2, role="assistant", content={"text": "hello"}, msg_metadata={"intent": "q"}, created_at=t0, tokens_used=42, latency_ms=900),
            _Row(id=uuid.uuid4(), role="system", content={"text": "x"}, msg_metadata=None, created_at=t0, tokens_used=0, latency_ms=0),
        ]
        traces = [
            _Row(message_id=m2, trace_index=0, tool_name="db_query", input={"q": 1}, output={"rows": 2}, status="success", latency_ms=300),
        ]
        stub = _StubClient([_Result(rows=[_Row(id=conv_id)]), _Result(rows=msgs), _Result(rows=traces)])
        monkeypatch.setattr(q, "client", stub)

        out = asyncio.run(q.get_messages_with_traces(str(conv_id), TENANT))

        assert [m["role"] for m in out] == ["user", "assistant"]  # system rows still filtered
        assert out[0]["content"] == "hi" and out[0]["metadata"] == {} and out[0]["traces"] == []
        assert out[1]["tokens_used"] == 42 and out[1]["latency_ms"] == 900
        assert out[1]["created_at"] == t0.isoformat()
        assert out[1]["traces"] == [{
            "trace_index": 0, "tool_name": "db_query", "input": {"q": 1}, "output": {"rows": 2},
            "status": "success", "latency_ms": 300,
        }]


class TestListTraces:
    def test_shape(self, monkeypatch):
        q = _queries()
        import uuid
        t0 = dt.datetime(2026, 8, 30, tzinfo=dt.UTC)
        row = _Row(
            id=uuid.uuid4(), conversation_id=uuid.uuid4(), message_id=uuid.uuid4(), tool_name="db_query",
            status="error", latency_ms=12, created_at=t0, input={}, output={"error": "boom"}, user_ref="bob",
        )
        stub = _StubClient([_Result(rows=[row]), _Result(scalar=7)])
        monkeypatch.setattr(q, "client", stub)
        out = asyncio.run(q.list_traces(TENANT, status="error", q="db"))
        assert out["total"] == 7
        assert set(out["items"][0]) == {
            "id", "conversation_id", "message_id", "tool_name", "status", "latency_ms",
            "created_at", "input", "output", "user_ref",
        }
        assert out["items"][0]["created_at"] == t0.isoformat()


# ─── Metrics ──────────────────────────────────────────────────────────────────

class TestMetricsSeries:
    def test_empty_series_is_one_entry_per_day_ending_today(self):
        q = _queries()
        today = dt.date(2026, 8, 30)
        series = q.empty_series(7, today)
        assert [e["date"] for e in series] == [
            "2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28", "2026-08-29", "2026-08-30",
        ]
        assert all(
            e["conversations"] == 0 and e["messages"] == 0 and e["tokens"] == 0
            and e["avg_latency_ms"] is None and e["errors"] == 0
            for e in series
        )

    def test_merge_fills_gaps_with_zeros(self):
        q = _queries()
        today = dt.date(2026, 8, 30)
        series = q.merge_series(
            3,
            conversations={"2026-08-29": 2},
            messages={"2026-08-30": {"count": 5, "tokens": 900, "avg_latency_ms": 123.456}},
            errors={"2026-08-28": 1, "2026-01-01": 99},  # outside window is dropped
            today=today,
        )
        assert series == [
            {"date": "2026-08-28", "conversations": 0, "messages": 0, "tokens": 0, "avg_latency_ms": None, "errors": 1},
            {"date": "2026-08-29", "conversations": 2, "messages": 0, "tokens": 0, "avg_latency_ms": None, "errors": 0},
            {"date": "2026-08-30", "conversations": 0, "messages": 5, "tokens": 900, "avg_latency_ms": 123.5, "errors": 0},
        ]

    def test_metrics_zero_tenant_shape(self, monkeypatch):
        q = _queries()
        stub = _StubClient([
            _Result(one=(0, 0)),                      # agents
            _Result(one=(0, 0, 0)),                   # conversations
            _Result(one=(0, 0, 0, 0, None, None)),    # messages
            _Result(one=(0, 0)),                      # traces
            _Result(rows=[]), _Result(rows=[]), _Result(rows=[]),
        ])
        monkeypatch.setattr(q, "client", stub)
        out = asyncio.run(q.platform_metrics(TENANT, days=5))
        assert out["agents"] == {"total": 0, "published": 0}
        assert out["conversations"] == {"total": 0, "last_24h": 0, "active": 0}
        assert out["messages"] == {"total": 0, "last_24h": 0}
        assert out["tokens"] == {"total": 0, "last_24h": 0}
        assert out["latency_ms"] == {"avg": None, "p95": None}
        assert out["error_rate"] == 0.0
        assert len(out["series"]) == 5
        assert stub.tenants == [TENANT]

    def test_error_rate_from_trace_counts(self, monkeypatch):
        q = _queries()
        stub = _StubClient([
            _Result(one=(3, 1)),
            _Result(one=(10, 2, 4)),
            _Result(one=(40, 6, 5000, 700, 812.6, 1900.0)),
            _Result(one=(8, 2)),
            _Result(rows=[(dt.date(2026, 8, 30), 3)]),
            _Result(rows=[(dt.date(2026, 8, 30), 6, 700, 500.0)]),
            _Result(rows=[(dt.date(2026, 8, 30), 2)]),
        ])
        monkeypatch.setattr(q, "client", stub)
        out = asyncio.run(q.platform_metrics(TENANT, days=2))
        assert out["error_rate"] == 0.25
        assert out["latency_ms"] == {"avg": 812.6, "p95": 1900.0}
        assert out["series"][-1] == {
            "date": dt.datetime.now(dt.UTC).date().isoformat(),
            "conversations": 3, "messages": 6, "tokens": 700, "avg_latency_ms": 500.0, "errors": 2,
        }


# ─── Service probes ───────────────────────────────────────────────────────────

class TestServiceProbes:
    def test_probe_all_never_raises(self, monkeypatch):
        import services.workflow_runtime.runtime_services.service_probes as sp

        async def ok():
            return "fine"

        async def boom():
            raise ConnectionError("refused")

        async def slow():
            await asyncio.sleep(5)
            return "late"

        async def unknown():
            raise sp._Unknown("FOO_URL not set")

        async def degraded():
            raise sp._Degraded("HTTP 503")

        monkeypatch.setattr(sp, "PROBE_TIMEOUT_S", 0.05)
        monkeypatch.setattr(sp, "_PROBES", {
            "postgres": ok, "redis": boom, "kafka": slow, "kb-backend": unknown, "litellm": degraded,
        })
        items = asyncio.run(sp.probe_all())
        by_name = {i["name"]: i for i in items}
        assert by_name["postgres"]["status"] == "ok" and by_name["postgres"]["kind"] == "database"
        assert by_name["redis"]["status"] == "down" and "refused" in by_name["redis"]["detail"]
        assert by_name["kafka"]["status"] == "down" and "timeout" in by_name["kafka"]["detail"]
        assert by_name["kb-backend"] == {
            "name": "kb-backend", "kind": "service", "status": "unknown", "latency_ms": None, "detail": "FOO_URL not set",
        }
        assert by_name["litellm"]["status"] == "degraded" and by_name["litellm"]["kind"] == "llm"
        assert all(set(i) == {"name", "kind", "status", "latency_ms", "detail"} for i in items)

    def test_http_probe_reports_unknown_without_env(self, monkeypatch):
        import services.workflow_runtime.runtime_services.service_probes as sp
        monkeypatch.delenv("KB_BACKEND_URL", raising=False)
        res = asyncio.run(sp._timed("kb-backend", sp._PROBES["kb-backend"]))
        assert res["status"] == "unknown" and "KB_BACKEND_URL" in res["detail"]

    def test_orchestrator_is_not_probed(self):
        """The orchestrator runs inside this process (orchestrator/ package) —
        an HTTP probe of it would only ever report on ourselves."""
        import services.workflow_runtime.runtime_services.service_probes as sp
        assert "orchestrator" not in sp._PROBES

    def test_real_probes_against_nothing_do_not_raise(self, monkeypatch):
        """With no DB/Redis/Mongo/Kafka open and no URLs set, every probe still answers."""
        import services.workflow_runtime.runtime_services.service_probes as sp
        for var in ("REDIS_URL", "REDIS_HOST", "KAFKA_BOOTSTRAP_SERVERS", "LITELLM_BASE_URL",
                    "SANDBOX_EXECUTOR_URL", "KB_BACKEND_URL"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setattr(sp, "PROBE_TIMEOUT_S", 0.5)
        items = asyncio.run(sp.probe_all())
        assert {i["name"] for i in items} == {
            "postgres", "redis", "mongo", "kafka", "litellm", "sandbox_executor", "kb-backend",
        }
        assert all(i["status"] in ("ok", "degraded", "down", "unknown") for i in items)
        assert {i["name"]: i["status"] for i in items if i["name"] in ("litellm", "kb-backend")} == {
            "litellm": "unknown", "kb-backend": "unknown",
        }


# ─── LiteLLM proxy ────────────────────────────────────────────────────────────

class TestLiteLLMProxy:
    def test_spend_unavailable_without_base_url(self, monkeypatch):
        import services.workflow_runtime.runtime_services.litellm_proxy as lp
        monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
        out = asyncio.run(lp.spend(30))
        assert out["available"] is False
        assert out["total"] == 0.0 and out["series"] == [] and out["by_model"] == []
        assert out["currency"] == "USD" and "LITELLM_BASE_URL" in out["note"]

    def test_spend_unavailable_when_both_endpoints_fail(self, monkeypatch):
        import services.workflow_runtime.runtime_services.litellm_proxy as lp
        monkeypatch.setenv("LITELLM_BASE_URL", "http://litellm.test:4000")
        monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-master")
        calls: list[str] = []

        async def failing_get(path, *, params=None, timeout_s):
            calls.append(path)
            raise RuntimeError("Database not connected")

        monkeypatch.setattr(lp, "_get", failing_get)
        out = asyncio.run(lp.spend(7))
        assert out["available"] is False and out["total"] == 0.0
        assert calls == ["/spend/logs", "/global/spend/report"]
        assert "Database not connected" in out["note"]

    def test_spend_aggregates_logs_per_day_and_model(self, monkeypatch):
        import services.workflow_runtime.runtime_services.litellm_proxy as lp
        monkeypatch.setenv("LITELLM_BASE_URL", "http://litellm.test:4000")
        monkeypatch.setenv("LITELLM_API_KEY", "sk-virtual")
        today = dt.datetime.now(dt.UTC).date()
        rows = [
            {"model": "gpt-4o", "spend": 0.5, "total_tokens": 100, "startTime": f"{today.isoformat()}T01:00:00"},
            {"model": "gpt-4o", "spend": 0.25, "total_tokens": 50, "startTime": f"{today.isoformat()}T02:00:00"},
            {"model": "claude", "spend": 1.0, "total_tokens": 10, "startTime": f"{(today - dt.timedelta(days=1)).isoformat()}T02:00:00"},
        ]

        async def fake_get(path, *, params=None, timeout_s):
            assert path == "/spend/logs" and set(params) == {"start_date", "end_date"}
            return rows

        monkeypatch.setattr(lp, "_get", fake_get)
        out = asyncio.run(lp.spend(3))
        assert out["available"] is True and out["total"] == 1.75
        assert len(out["series"]) == 3 and out["series"][-1] == {"date": today.isoformat(), "spend": 0.75, "tokens": 150}
        assert out["series"][0]["spend"] == 0.0
        assert out["by_model"] == [
            {"model": "claude", "spend": 1.0, "tokens": 10},
            {"model": "gpt-4o", "spend": 0.75, "tokens": 150},
        ]

    def test_models_unavailable_on_failure(self, monkeypatch):
        import services.workflow_runtime.runtime_services.litellm_proxy as lp
        monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
        out = asyncio.run(lp.list_models())
        assert out == {"items": [], "available": False, "error": "RuntimeError: LITELLM_BASE_URL not set"}

    def test_models_row_mapping(self, monkeypatch):
        import services.workflow_runtime.runtime_services.litellm_proxy as lp

        async def fake_get(path, *, params=None, timeout_s):
            return {"data": [{
                "model_name": "responder",
                "litellm_params": {"model": "anthropic/claude-sonnet-4-5"},
                "model_info": {"max_tokens": 8192, "input_cost_per_token": 3e-6, "output_cost_per_token": 1.5e-5, "mode": "chat"},
            }]}

        monkeypatch.setattr(lp, "_get", fake_get)
        out = asyncio.run(lp.list_models())
        assert out["available"] is True
        assert out["items"] == [{
            "model_name": "responder", "litellm_provider": "anthropic", "base_model": "anthropic/claude-sonnet-4-5",
            "max_tokens": 8192, "input_cost_per_token": 3e-6, "output_cost_per_token": 1.5e-5, "mode": "chat",
        }]

    def test_health_is_cached_for_a_minute(self, monkeypatch):
        import services.workflow_runtime.runtime_services.litellm_proxy as lp
        lp.reset_health_cache()
        calls = []

        async def fake_get(path, *, params=None, timeout_s):
            calls.append(path)
            return {
                "healthy_endpoints": [{"model": "openai/gpt-4o"}],
                "unhealthy_endpoints": [{"model": "azure/x", "error": "401"}],
            }

        monkeypatch.setattr(lp, "_get", fake_get)
        first = asyncio.run(lp.models_health())
        second = asyncio.run(lp.models_health())
        assert calls == ["/health"]
        assert first is second
        assert first["available"] is True
        assert first["items"] == [
            {"model_name": "openai/gpt-4o", "status": "ok", "latency_ms": None, "error": None},
            {"model_name": "azure/x", "status": "down", "latency_ms": None, "error": "401"},
        ]
        lp.reset_health_cache()


# ─── Routers over the FastAPI app ─────────────────────────────────────────────

@pytest.fixture
def api(monkeypatch):
    """The five routers mounted like app.py does, with auth resolved to CALLER."""
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    # conversations.py imports the streaming runner (LangGraph et al.) — not
    # needed here. Must be registered before routers.conversations is first
    # imported; modules are then cached and shared with the tests' monkeypatches.
    if "services.workflow_runtime.routers.conversations" not in sys.modules:
        stub = types.SimpleNamespace(run_conversation_stream=None)
        monkeypatch.setitem(sys.modules, "runtime_services.conversation_runner", stub)
        monkeypatch.setitem(sys.modules, "services.workflow_runtime.runtime_services.conversation_runner", stub)
    from services.workflow_runtime.auth import get_current_user
    from services.workflow_runtime.routers import billing, conversations, llm, platform, traces

    app = FastAPI()
    authed = [Depends(get_current_user)]
    for r in (llm.router, conversations.router, traces.router, platform.router, billing.router):
        app.include_router(r, dependencies=authed)
    app.dependency_overrides[get_current_user] = lambda: CALLER
    return TestClient(app)


class TestSettingsRoute:
    @pytest.mark.parametrize("key", ["Bad", "with space", "a" * 101, "ünï", "dots.no"])
    def test_invalid_key_is_422(self, api, key):
        assert api.get(f"/api/platform/settings/{key}").status_code == 422
        assert api.put(f"/api/platform/settings/{key}", json={"value": 1}).status_code == 422

    def test_unset_key_is_200_with_null(self, api, monkeypatch):
        q = _queries()
        monkeypatch.setattr(q, "client", _StubClient([_Result(rows=[])]))
        resp = api.get("/api/platform/settings/dashboard_layout")
        assert resp.status_code == 200
        assert resp.json() == {"key": "dashboard_layout", "value": None, "updated_at": None}

    def test_put_upserts_with_caller_identity(self, api, monkeypatch):
        q = _queries()
        seen: dict[str, Any] = {}

        async def fake_put(tenant_id, key, value, updated_by):
            seen.update(tenant_id=tenant_id, key=key, value=value, updated_by=updated_by)
            return {"key": key, "value": value, "updated_at": "2026-08-30T00:00:00+00:00"}

        monkeypatch.setattr(q, "put_setting", fake_put)
        resp = api.put("/api/platform/settings/theme-v2", json={"value": {"dark": True, "cols": [1, 2]}})
        assert resp.status_code == 200
        assert resp.json()["value"] == {"dark": True, "cols": [1, 2]}
        assert seen == {"tenant_id": TENANT, "key": "theme-v2", "value": {"dark": True, "cols": [1, 2]}, "updated_by": "u1@example.com"}

    def test_put_without_value_field_is_422(self, api):
        assert api.put("/api/platform/settings/k", json={"nope": 1}).status_code == 422

    def test_put_upsert_statement_targets_unique_constraint(self, monkeypatch):
        q = _queries()
        stub = _StubClient([_Result(one=_Row(value={"a": 1}, updated_at=dt.datetime(2026, 8, 30, tzinfo=dt.UTC)))])
        monkeypatch.setattr(q, "client", stub)
        out = asyncio.run(q.put_setting(TENANT, "k", {"a": 1}, "u1"))
        assert out == {"key": "k", "value": {"a": 1}, "updated_at": "2026-08-30T00:00:00+00:00"}
        from sqlalchemy.dialects import postgresql
        sql = str(stub.statements[0].compile(dialect=postgresql.dialect()))
        assert "ON CONFLICT ON CONSTRAINT uq_platform_settings_tenant_key DO UPDATE" in sql


class TestListingRoutes:
    def test_conversations_query_params_pass_through(self, api, monkeypatch):
        q = _queries()
        seen: dict[str, Any] = {}

        async def fake_list(tenant_id, **kw):
            seen.update(tenant_id=tenant_id, **kw)
            return {"items": [], "total": 0}

        monkeypatch.setattr(q, "list_conversations", fake_list)
        resp = api.get("/api/conversations", params={"limit": 5, "offset": 10, "status": "active", "q": "ali", "agent_id": "a1"})
        assert resp.status_code == 200 and resp.json() == {"items": [], "total": 0}
        assert seen == {"tenant_id": TENANT, "limit": 5, "offset": 10, "status": "active", "q": "ali", "agent_id": "a1"}

    def test_conversations_defaults(self, api, monkeypatch):
        q = _queries()
        seen: dict[str, Any] = {}

        async def fake_list(tenant_id, **kw):
            seen.update(kw)
            return {"items": [], "total": 0}

        monkeypatch.setattr(q, "list_conversations", fake_list)
        assert api.get("/api/conversations").status_code == 200
        assert seen == {"limit": 50, "offset": 0, "status": None, "q": None, "agent_id": None}
        assert api.get("/api/conversations", params={"limit": 0}).status_code == 422

    def test_traces_collection_route_precedes_conv_id_route(self, api, monkeypatch):
        q = _queries()

        async def fake_list(tenant_id, **kw):
            return {"items": [{"id": "t1"}], "total": 1}

        monkeypatch.setattr(q, "list_traces", fake_list)
        resp = api.get("/api/traces", params={"status": "error"})
        assert resp.status_code == 200 and resp.json() == {"items": [{"id": "t1"}], "total": 1}

        # The Langfuse-backed per-conversation route is untouched.
        import services.workflow_runtime.routers.traces as tr

        async def fake_obs(conv_id, trace_id=None):
            return {"observations": [], "conv": conv_id}

        monkeypatch.setattr(tr, "get_trace_observations", fake_obs)
        assert api.get("/api/traces/conv-123").json() == {"observations": [], "conv": "conv-123"}

    def test_messages_route_returns_extended_rows(self, api, monkeypatch):
        q = _queries()

        async def fake_msgs(conv_id, tenant_id, limit=50):
            assert tenant_id == TENANT
            return [{"id": "m", "role": "user", "content": "hi", "metadata": {}, "created_at": None,
                     "tokens_used": 0, "latency_ms": 0, "traces": []}]

        monkeypatch.setattr(q, "get_messages_with_traces", fake_msgs)
        resp = api.get("/api/conversations/abc/messages")
        assert resp.status_code == 200
        assert resp.json()["messages"][0]["traces"] == []

    def test_metrics_route_zero_tenant(self, api, monkeypatch):
        q = _queries()
        monkeypatch.setattr(q, "client", _StubClient([
            _Result(one=(0, 0)), _Result(one=(0, 0, 0)), _Result(one=(0, 0, 0, 0, None, None)), _Result(one=(0, 0)),
            _Result(rows=[]), _Result(rows=[]), _Result(rows=[]),
        ]))
        body = api.get("/api/platform/metrics", params={"days": 3}).json()
        assert len(body["series"]) == 3 and body["error_rate"] == 0.0

    def test_metrics_route_degrades_to_zeros_on_db_error(self, api, monkeypatch):
        q = _queries()

        async def broken(tenant_id, days=7):
            raise RuntimeError("connection reset")

        monkeypatch.setattr(q, "platform_metrics", broken)
        body = api.get("/api/platform/metrics", params={"days": 7}).json()
        assert body["agents"] == {"total": 0, "published": 0} and len(body["series"]) == 7
        assert "connection reset" in body["error"]

    def test_services_route(self, api, monkeypatch):
        import services.workflow_runtime.runtime_services.service_probes as sp

        async def fake_probe_all():
            return [{"name": "postgres", "kind": "database", "status": "ok", "latency_ms": 1.0, "detail": ""}]

        monkeypatch.setattr(sp, "probe_all", fake_probe_all)
        assert api.get("/api/platform/services").json() == {"items": [
            {"name": "postgres", "kind": "database", "status": "ok", "latency_ms": 1.0, "detail": ""},
        ]}

    def test_billing_and_llm_routes(self, api, monkeypatch):
        import services.workflow_runtime.runtime_services.litellm_proxy as lp
        monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
        lp.reset_health_cache()
        assert api.get("/api/billing/spend", params={"days": 30}).json()["available"] is False
        assert api.get("/api/llm-providers/models").json() == {
            "items": [], "available": False, "error": "RuntimeError: LITELLM_BASE_URL not set",
        }
        health = api.get("/api/llm-providers/health").json()
        assert health["available"] is False and health["items"] == []
        lp.reset_health_cache()


class TestResolveTenant:
    def test_uuid_passes_and_placeholder_falls_back(self):
        q = _queries()
        assert q.resolve_tenant({"tenant_id": TENANT}) == TENANT
        assert q.resolve_tenant({"tenant_id": "dev-tenant"}) == q._DEV_TENANT_ID
        assert q.resolve_tenant({"tenant_id": None}) == q._DEV_TENANT_ID
