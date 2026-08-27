"""Who read what — across every tool, not just SQL.

The data-access trail was built for the SQL path: `_sandbox_sql` records every
query, `kb_search` records every retrieval, the Kafka sink is installed at
startup and audit-consumer writes `DataAccessLog`. That half works.

What it did not cover was everything else the tool executor can do on a
tenant's data:

  * `execute_sql` — a SECOND agent-callable SQL handler, registered in node.py
    beside text_to_sql, running real queries through sandbox-executor and
    emitting nothing. For every query that came through it, "who queried what"
    was still literally unanswerable.
  * `file_read` / `grep` / `glob` / `file_write` / `file_edit` — a file under a
    tenant's allowed_dir is tenant data.
  * `execute_python` and `bash` — agent-authored code and shell commands, run
    against whatever the turn put in the sandbox. `ACTION_CODE_EXEC` was
    defined from the start with no emitter anywhere.
  * `text_to_pandas` — a tenant's CSV read into a dataframe.

Two rules the records themselves have to keep:

  * **The audit must not become the data.** A grep pattern is a search term, a
    path is often a customer name, a written file is the content itself. The
    detail is truncated and hashed, exactly as SQL is redacted and hashed, so
    the trail can prove what ran without holding a copy of it.
  * **Refusals are records.** A path traversal that `_safe_path` rejected is
    precisely the event an investigation wants, and the one a success-only
    trail discards.
"""
from __future__ import annotations

import ast
import pathlib
import sys
from typing import ClassVar

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNTIME = REPO_ROOT / "services" / "workflow-runtime"
HANDLERS = RUNTIME / "nodes" / "sandbox" / "tool_executor" / "handlers"

pytestmark = pytest.mark.unit


@pytest.fixture
def collected(monkeypatch):
    """Capture emitted events instead of sending them to Kafka."""
    from libs.shared import audit

    events: list = []
    monkeypatch.setattr(audit, "_sink", lambda topic, key, value: events.append(value))
    return events


CFG = {
    "tenant_id": "a0000000-0000-0000-0000-000000000001",
    "user_ref": "nguyen.van.a",
    "user_roles": ["business-operator"],
    "auth_source": "gateway",
    "session_id": "sess-1",
    "agent_id": "agent-1",
}


class TestEveryDataTouchingHandlerRecords:
    """The coverage invariant. Wiring one handler at a time is how this gap
    appeared — text_to_sql was wired and its neighbour in the same registry was
    not — so the check is over the registry, not over a list someone maintains.
    """

    # Functions that reach a tenant's data and must therefore leave a record.
    #
    # `_sandbox_sql` rather than `_text_to_sql_handler`: the handler delegates
    # every execution to it (directly, through _run_one_query's retry loop, and
    # through the semantic path), so it is the choke point where an access
    # actually happens. Asserting on the handler would pass while a new caller
    # of the underlying query function recorded nothing.
    _MUST_RECORD: ClassVar[set[str]] = {
        "_sandbox_sql", "_text_to_pandas_handler",
        "_execute_sql_handler", "_execute_python_handler",
        "_file_read_handler", "_grep_handler", "_glob_handler",
        "_file_write_handler", "_file_edit_handler",
        "_bash_handler",
    }

    def _functions_emitting_audit(self) -> set[str]:
        emitting = set()
        for path in HANDLERS.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                names = {
                    n.func.attr for n in ast.walk(fn)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                } | {
                    n.func.id for n in ast.walk(fn)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                }
                if names & {"record_sql_access", "record_tool_access", "emit", "_record"}:
                    emitting.add(fn.name)
        return emitting

    def test_no_data_touching_handler_is_silent(self):
        emitting = self._functions_emitting_audit()
        silent = self._MUST_RECORD - emitting
        assert not silent, f"handler(s) touching tenant data with no audit record: {sorted(silent)}"

    def test_the_registry_has_no_handler_this_test_forgot(self):
        """If a new tool is registered, this fails until someone decides
        whether it touches data — rather than defaulting to unaudited."""
        tree = ast.parse((HANDLERS.parent / "node.py").read_text(encoding="utf-8"))
        registered = {
            n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and n.id.endswith("_handler")
        }
        known = self._MUST_RECORD | {
            # Reaches tenant data only through _sandbox_sql, which records.
            "_text_to_sql_handler",
            # Dispatches to other handlers, which record for themselves —
            # recording here too would double-count one access.
            "_delegate_to_agent_handler",
            # Touch no tenant data: arithmetic, and a public encyclopaedia.
            "_calculator_handler", "_wikipedia_handler",
            # Reads the public web, not tenant storage. Worth noting that it
            # sends the question OUT to a third party — that is egress, a
            # different record from "who read what", and not one this trail
            # claims to keep.
            "_web_search_handler",
        }
        unclassified = registered - known
        assert not unclassified, (
            f"new tool handler(s) {sorted(unclassified)} — decide whether each touches "
            "tenant data and either wire an audit record or add it to the exempt list"
        )


class TestTheAuditDoesNotBecomeTheData:
    def test_detail_is_truncated_and_hashed_not_stored_whole(self, collected):
        from libs.shared import audit

        secret = "SELECT * FROM customers WHERE email = 'a@b.com' " + "x" * 5000
        audit.record_tool_access(
            tenant_id="t", user_id="u", action=audit.ACTION_SHELL_EXEC, detail=secret,
        )

        (event,) = collected
        assert len(event["statement_redacted"]) <= audit.MAX_DETAIL_CHARS
        assert len(event["statement_sha256"]) == 64
        # The hash is of the ORIGINAL, so repeat access still correlates.
        assert event["statement_sha256"] == audit.statement_hash(secret)

    def test_the_same_command_hashes_the_same_way(self, collected):
        from libs.shared import audit

        for _ in range(2):
            audit.record_tool_access(
                tenant_id="t", user_id="u", action=audit.ACTION_SHELL_EXEC, detail="ls -la /data",
            )
        assert collected[0]["statement_sha256"] == collected[1]["statement_sha256"]

    def test_a_written_file_records_its_size_not_its_contents(self):
        """file_write's `content` is the tenant's data. Putting it in the audit
        row would make the record the largest copy of what it protects."""
        source = (HANDLERS / "filesystem.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == "_file_write_handler"
        )
        def _bare_content_uses(node: ast.AST) -> list[str]:
            """`content` reaching the record other than as len(content).

            Passing the size is the point; passing the bytes is the bug. The
            distinction is what the test has to make, so it walks the call
            rather than searching for a name.
            """
            offenders = []
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == "len":
                    continue
                if isinstance(sub, ast.Name) and sub.id == "content":
                    parent_is_len = any(
                        isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                        and c.func.id == "len" and sub in ast.walk(c)
                        for c in ast.walk(node) if isinstance(c, ast.Call)
                    )
                    if not parent_is_len:
                        offenders.append(sub.id)
            return offenders

        for call in ast.walk(fn):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id == "_record"):
                continue
            for kw in call.keywords:
                assert not _bare_content_uses(kw.value), (
                    "file_write passes the written content to the audit record; "
                    "pass len(content) instead"
                )


class TestRefusalsAreRecorded:
    def _filesystem(self):
        pytest.importorskip("libs.shared.audit")
        sys.path.insert(0, str(RUNTIME))
        sys.path.insert(0, str(RUNTIME / "nodes" / "sandbox" / "tool_executor"))
        import nodes.sandbox.tool_executor.handlers.filesystem as fs
        return fs

    async def test_a_rejected_path_still_leaves_a_record(self, collected):
        """A traversal attempt is the event an investigation wants most, and the
        one a success-only trail throws away."""
        fs = self._filesystem()

        result, error = await fs._file_read_handler(
            {"arguments": {"file_path": "../../etc/passwd"}},
            {**CFG, "tool_config": {"file_read": {"allowed_dir": "/data/tenant-a"}}},
        )

        assert result is None and error is not None
        (event,) = collected
        assert event["action"] == "file_read"
        assert event["decision"] == "deny"
        assert event["user_id"] == "nguyen.van.a"
        assert event["tenant_id"] == CFG["tenant_id"]

    async def test_a_successful_read_records_the_path(self, collected, tmp_path):
        fs = self._filesystem()
        target = tmp_path / "orders.csv"
        target.write_text("id,amount\n1,10\n", encoding="utf-8")

        result, error = await fs._file_read_handler(
            {"arguments": {"file_path": str(target)}},
            {**CFG, "tool_config": {"file_read": {"allowed_dir": str(tmp_path)}}},
        )

        assert error is None and result is not None
        (event,) = collected
        assert event["decision"] == "allow"
        assert str(target) in event["target_objects"]

    async def test_the_record_carries_the_turn_it_belongs_to(self, collected, tmp_path):
        """Without session and agent, a row cannot be tied back to the
        conversation that produced it."""
        fs = self._filesystem()
        target = tmp_path / "f.txt"
        target.write_text("x", encoding="utf-8")

        await fs._file_read_handler(
            {"arguments": {"file_path": str(target)}},
            {**CFG, "tool_config": {"file_read": {"allowed_dir": str(tmp_path)}}},
        )

        (event,) = collected
        assert event["session_id"] == "sess-1"
        assert event["agent_id"] == "agent-1"
        assert event["auth_source"] == "gateway"


class TestTheSecondSqlPathIsRecordedLikeTheFirst:
    def test_execute_sql_uses_the_sql_recorder_not_the_generic_one(self):
        """record_sql_access redacts the literals, hashes the original and
        extracts the tables. A SQL statement recorded any other way is a WHERE
        clause sitting in the audit table."""
        tree = ast.parse((HANDLERS / "code_interpreter.py").read_text(encoding="utf-8"))
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == "_execute_sql_handler"
        )
        called = {
            n.func.attr for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        assert "record_sql_access" in called
        assert "record_tool_access" not in called

    def test_it_distinguishes_a_guard_rejection_from_a_fault(self):
        """The same distinction the text_to_sql path records, so "how often does
        the agent try to write?" covers both SQL surfaces rather than one."""
        source = (HANDLERS / "code_interpreter.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == "_execute_sql_handler"
        )
        attrs = {
            n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)
        }
        assert {"DECISION_DENY", "DECISION_ERROR", "DECISION_ALLOW"} <= attrs

    def test_sql_literals_are_redacted_by_that_recorder(self, collected):
        from libs.shared import audit

        audit.record_sql_access(
            tenant_id="t", user_id="u",
            sql="SELECT * FROM customers WHERE email = 'nguyen@example.com'",
        )

        (event,) = collected
        assert "nguyen@example.com" not in event["statement_redacted"]
        assert "customers" in event["target_objects"]


class TestItNeverFailsTheTurn:
    def test_a_missing_sink_drops_quietly(self, monkeypatch):
        from libs.shared import audit

        monkeypatch.setattr(audit, "_sink", None)
        audit.record_tool_access(tenant_id="t", user_id="u", action=audit.ACTION_CODE_EXEC)

    def test_a_broken_sink_does_not_propagate(self, monkeypatch):
        """An audit sink being down is an operational problem, not a reason to
        refuse a user's question."""
        from libs.shared import audit

        def explode(*a, **k):
            raise RuntimeError("kafka down")

        monkeypatch.setattr(audit, "_sink", explode)
        audit.record_tool_access(tenant_id="t", user_id="u", action=audit.ACTION_FILE_READ)


class TestTheConsumerAcceptsTheNewActions:
    def test_every_action_fits_the_stored_column(self):
        """`action` is String(32) and the consumer inserts it verbatim — a
        longer name would fail at write time, after the access happened."""
        from libs.shared import audit

        actions = [
            v for k, v in vars(audit).items()
            if k.startswith("ACTION_") and isinstance(v, str)
        ]
        assert len(actions) >= 7
        for action in actions:
            assert len(action) <= 32, f"{action!r} will not fit DataAccessLog.action"

    def test_the_consumer_does_not_allowlist_actions(self):
        """It inserts what it is given, so a new action needs no consumer
        change — which is what makes wiring a handler a one-file job."""
        source = (REPO_ROOT / "services" / "audit-consumer" / "data_access.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        required = next(
            (n.value for n in ast.walk(tree)
             if isinstance(n, ast.Assign)
             and any(getattr(t, "id", "") == "_REQUIRED" for t in n.targets)),
            None,
        )
        assert required is not None
        names = {c.value for c in ast.walk(required) if isinstance(c, ast.Constant)}
        assert "action" in names, "action must still be required"
        assert "sql_query" not in names, "the consumer must not allowlist specific actions"
