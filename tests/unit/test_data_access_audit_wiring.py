"""Data-access audit wiring — that events are actually emitted from the query path.

libs/shared/audit.py is covered on its own. What matters here is that
`_sandbox_sql` — the single choke point every agent query passes through — emits
on EVERY exit path, that a denied query is recorded as loudly as a successful
one, and that a broken audit sink still cannot fail the turn.
"""
from __future__ import annotations

import pathlib
import sys
from unittest.mock import AsyncMock

import pytest

from libs.shared import audit

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "services" / "workflow-runtime")

pytestmark = pytest.mark.unit

CONN = {"host": "h", "database": "d", "user": "u", "password": "p"}
CTX = {
    "tenant_id": "t-1",
    "user_id": "u-42",
    "user_roles": ["business-operator"],
    "auth_source": "gateway",
    "session_id": "s-1",
    "agent_id": "a-1",
}


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)
    audit.set_sink(None)


def _data():
    import nodes.sandbox.tool_executor.handlers.data as data
    return data


def _capture():
    sent: list[dict] = []
    audit.set_sink(lambda topic, key, value: sent.append(value))
    return sent


def _submit(monkeypatch, data, result):
    async def fake(http, path, body, overall_timeout_s):
        return result
    monkeypatch.setattr(data, "submit_and_poll", fake)


class TestEveryExitPathIsRecorded:
    async def test_successful_query_records_allow_with_row_count(self, monkeypatch):
        data = _data()
        sent = _capture()
        _submit(monkeypatch, data, {"success": True, "rows": [{"n": 1}, {"n": 2}], "is_truncated": False})

        await data._sandbox_sql("SELECT n FROM orders", CONN, audit_ctx=CTX, target="Sales")

        assert len(sent) == 1
        assert sent[0]["decision"] == audit.DECISION_ALLOW
        assert sent[0]["row_count"] == 2
        assert sent[0]["target"] == "Sales"
        assert sent[0]["target_objects"] == ["orders"]
        assert sent[0]["user_id"] == "u-42"

    async def test_guard_rejection_is_recorded_as_a_denial(self, monkeypatch):
        """A refused write is a policy event. Recording it is what makes "how
        often does the agent try to write?" answerable."""
        data = _data()
        sent = _capture()
        _submit(monkeypatch, data, {
            "success": False, "error": "SQL rejected: DDL token not allowed: DROP",
            "rejected_by_guard": True,
        })

        await data._sandbox_sql("DROP TABLE orders", CONN, audit_ctx=CTX, target="Sales")

        assert sent[0]["decision"] == audit.DECISION_DENY
        assert "DDL token not allowed" in sent[0]["reason"]

    async def test_execution_failure_is_recorded_as_an_error(self, monkeypatch):
        data = _data()
        sent = _capture()
        _submit(monkeypatch, data, {"success": False, "error": 'column "x" does not exist'})

        await data._sandbox_sql("SELECT x FROM orders", CONN, audit_ctx=CTX, target="Sales")

        assert sent[0]["decision"] == audit.DECISION_ERROR

    async def test_unreachable_executor_is_recorded(self, monkeypatch):
        """The failure mode most likely to hide an access attempt entirely."""
        data = _data()
        sent = _capture()

        async def boom(http, path, body, overall_timeout_s):
            raise RuntimeError("connection refused")
        monkeypatch.setattr(data, "submit_and_poll", boom)

        await data._sandbox_sql("SELECT 1 FROM orders", CONN, audit_ctx=CTX, target="Sales")

        assert sent[0]["decision"] == audit.DECISION_ERROR
        assert "unreachable" in sent[0]["reason"]

    async def test_latency_is_recorded(self, monkeypatch):
        data = _data()
        sent = _capture()
        _submit(monkeypatch, data, {"success": True, "rows": [], "is_truncated": False})

        await data._sandbox_sql("SELECT 1", CONN, audit_ctx=CTX)

        assert isinstance(sent[0]["latency_ms"], int)


class TestRedactionAtTheCallSite:
    async def test_the_emitted_record_never_holds_the_literal(self, monkeypatch):
        data = _data()
        sent = _capture()
        _submit(monkeypatch, data, {"success": True, "rows": [], "is_truncated": False})

        await data._sandbox_sql(
            "SELECT * FROM users WHERE email = 'alice@example.com'",
            CONN, audit_ctx=CTX, target="HR",
        )

        assert "alice@example.com" not in sent[0]["statement_redacted"]
        assert "users" in sent[0]["statement_redacted"]


class TestActionClassification:
    async def test_schema_introspection_is_its_own_action(self, monkeypatch):
        """Reading information_schema is a real access, but filing it as a
        business query would drown the trail."""
        data = _data()
        sent = _capture()
        _submit(monkeypatch, data, {"success": True, "rows": [], "is_truncated": False})

        await data._sandbox_sql(
            "SELECT table_name FROM information_schema.columns", CONN,
            audit_ctx=CTX, action=audit.ACTION_SCHEMA_INTROSPECT,
        )

        assert sent[0]["action"] == audit.ACTION_SCHEMA_INTROSPECT

    async def test_metric_ref_is_carried_when_the_contract_produced_the_sql(self, monkeypatch):
        """Lets the trail show WHY a statement had the shape it did."""
        data = _data()
        sent = _capture()
        _submit(monkeypatch, data, {"success": True, "rows": [], "is_truncated": False})

        await data._sandbox_sql(
            "SELECT SUM(amount) AS value FROM orders", CONN,
            audit_ctx=CTX, metric_ref="revenue_net@3",
        )

        assert sent[0]["metric_ref"] == "revenue_net@3"


class TestAuditNeverFailsTheTurn:
    async def test_a_broken_sink_does_not_break_the_query(self, monkeypatch):
        """The explicit trade-off: this records access, it does not gate it."""
        data = _data()
        audit.set_sink(lambda *a, **k: (_ for _ in ()).throw(RuntimeError("kafka down")))
        _submit(monkeypatch, data, {"success": True, "rows": [{"n": 1}], "is_truncated": False})

        rows, error, _rejected, _trunc = await data._sandbox_sql("SELECT 1", CONN, audit_ctx=CTX)

        assert rows == [{"n": 1}]
        assert error is None

    async def test_no_audit_context_is_silent_not_fatal(self, monkeypatch):
        """Paths that legitimately have no caller (a health probe, a test) must
        still work."""
        data = _data()
        sent = _capture()
        _submit(monkeypatch, data, {"success": True, "rows": [], "is_truncated": False})

        _rows, error, _, _ = await data._sandbox_sql("SELECT 1", CONN)

        assert error is None
        assert sent == []


class TestRetriesAreEachRecorded:
    async def test_every_attempt_produces_its_own_record(self, monkeypatch):
        """A trail showing only the final statement would hide that the agent
        ran three different queries to answer one question."""
        data = _data()
        sent = _capture()
        monkeypatch.setattr(data, "_sandbox_sql", data._sandbox_sql)

        calls = iter([
            {"success": False, "error": "syntax error"},
            {"success": False, "error": "syntax error"},
            {"success": True, "rows": [{"n": 1}], "is_truncated": False},
        ])

        async def fake(http, path, body, overall_timeout_s):
            return next(calls)
        monkeypatch.setattr(data, "submit_and_poll", fake)
        monkeypatch.setattr(data.litellm, "acompletion", AsyncMock(
            return_value=type("R", (), {"choices": [type("C", (), {
                "message": type("M", (), {"content": "SELECT n FROM orders"})()})()]})(),
        ))

        await data._run_one_query(
            "Sales", "SELECT bad", CONN, "q", "schema", "model", audit_ctx=CTX,
        )

        assert len(sent) == 3
        assert [e["decision"] for e in sent] == [
            audit.DECISION_ERROR, audit.DECISION_ERROR, audit.DECISION_ALLOW,
        ]
