"""execute_python / execute_sql — Code Interpreter Sandbox (sandbox_schema_docs.docx §1).

These two tools never run agent-authored code/SQL inside workflow_runtime
itself. Both call out to the sandbox_executor service over HTTP — the only
component in the platform that touches the Docker socket (hub-and-spoke
isolation, doc §1.2) or holds the read-only Snowflake service-account
credentials (doc §1.5).

handlers/data.py's text_to_sql now takes the same route (it used to open its
own asyncpg connection and run LLM-written SQL unguarded), so the submit+poll
transport both use lives in _sandbox_http.py rather than here.

backend/services/sandbox_executor/ops/ "Kế hoạch 1" mục i: sandbox_executor's POST /execute/python and
/execute/sql no longer run the request inline — they enqueue and return
202 + {job_id} immediately, without holding one HTTP connection open for
the full acquire+exec/query duration. Both handlers below submit, then
poll GET .../{job_id} with backoff until a terminal status. The contract
this module exposes to the agent loop is unchanged: the tool-call still
blocks until a final result (mục i, "đã chốt mô hình đồng bộ") — only the
transport underneath changed.
"""
from typing import Any
import logging
import time
import uuid

import httpx

from libs.shared import audit
from services.workflow_runtime.application.nodes.sandbox.tool_executor._sandbox_http import POLL_OVERHEAD_S, submit_and_poll
from services.workflow_runtime.application.nodes.sandbox.tool_executor._utils import audit_ctx

logger = logging.getLogger(__name__)

_POLL_OVERHEAD_S = POLL_OVERHEAD_S


async def _execute_python_handler(call: dict[str, Any], cfg: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    code = call.get("arguments", {}).get("code") or call.get("query", "")
    if not code:
        return None, {"tool_name": "execute_python", "error": "no code provided"}

    # Recorded at submission rather than on success. Agent-authored code runs
    # against whatever the turn put in the sandbox — usually the rows a previous
    # query returned — so it is an access in its own right, and one that a
    # crashed or timed-out run does not undo. ACTION_CODE_EXEC existed for this
    # from the beginning and had no emitter.
    audit.record_tool_access(
        action=audit.ACTION_CODE_EXEC,
        target="execute_python",
        detail=code,
        **audit_ctx(cfg),
    )

    tool_cfg = cfg.get("tool_config", {}).get("execute_python", {})
    timeout_s = tool_cfg.get("timeout_s", 10)
    # memory_mb/cpus are omitted unless a workflow explicitly overrides
    # them — sandbox_executor's container pool has these fixed at creation
    # (SANDBOX_MEMORY_MB/SANDBOX_CPUS) and rejects any request whose value
    # doesn't match, so hardcoding a duplicate 256/0.5 default here would
    # silently break every call the moment an operator retunes that env
    # var on sandbox_executor without also editing this file.
    request_body = {"code": code, "timeout_s": timeout_s}
    if "memory_mb" in tool_cfg:
        request_body["memory_mb"] = tool_cfg["memory_mb"]
    if "cpus" in tool_cfg:
        request_body["cpus"] = tool_cfg["cpus"]

    try:
        async with httpx.AsyncClient(timeout=10) as http:
            result = await submit_and_poll(
                http, "/execute/python", request_body, overall_timeout_s=timeout_s + _POLL_OVERHEAD_S
            )
    except Exception as e:
        return None, {"tool_name": "execute_python", "error": f"sandbox_executor unreachable: {e}"}

    if result.get("_http_status") == 503:
        return None, {
            "tool_name": "execute_python",
            "error": f"sandbox_executor is overloaded, try again shortly: {result.get('error', 'rejected')}",
        }

    if not result.get("success"):
        return None, {"tool_name": "execute_python", "error": result.get("error", "execution failed"),
                       "stderr": result.get("stderr")}

    content = result.get("stdout", "")
    if result.get("is_truncated"):
        content += "\n[output truncated]"
    return {
        "id": f"py_{uuid.uuid4().hex[:8]}",
        "content": content,
        "source": "tool/execute_python",
        "metadata": {"exit_code": result.get("exit_code"), "stderr": result.get("stderr", "")},
    }, None


async def _execute_sql_handler(call: dict[str, Any], cfg: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    sql = call.get("arguments", {}).get("sql") or call.get("query", "")
    if not sql:
        return None, {"tool_name": "execute_sql", "error": "no sql provided"}

    tool_cfg = cfg.get("tool_config", {}).get("execute_sql", {})
    connection = tool_cfg.get("connection", {})
    if not connection.get("account"):
        return None, {"tool_name": "execute_sql", "error": "Snowflake connection not configured"}

    # The SECOND agent-callable SQL path, registered in node.py alongside
    # text_to_sql and emitting nothing. The other one was wired when the data
    # access trail was built; this one was missed, so "who queried what" stayed
    # literally unanswerable for every query that came through here.
    #
    # record_sql_access rather than record_tool_access: it redacts the literals,
    # hashes the original and extracts the table names, and a SQL statement
    # recorded any other way is a WHERE clause sitting in the audit table.
    ctx = audit_ctx(cfg)
    target = str(connection.get("database") or connection.get("account") or "snowflake")
    started = time.monotonic()

    def _record(decision: str, row_count: int | None, reason: str) -> None:
        audit.record_sql_access(
            sql=sql,
            target=target,
            decision=decision,
            row_count=row_count,
            latency_ms=int((time.monotonic() - started) * 1000),
            reason=reason,
            **ctx,
        )

    try:
        async with httpx.AsyncClient(timeout=10) as http:
            result = await submit_and_poll(
                http, "/execute/sql", {"sql": sql, "connection": connection}, overall_timeout_s=60 + _POLL_OVERHEAD_S
            )
    except Exception as e:
        _record(audit.DECISION_ERROR, None, f"sandbox_executor unreachable: {e}")
        return None, {"tool_name": "execute_sql", "error": f"sandbox_executor unreachable: {e}"}

    if result.get("_http_status") == 503:
        _record(audit.DECISION_ERROR, None, "sandbox_executor overloaded")
        return None, {
            "tool_name": "execute_sql",
            "error": f"sandbox_executor is overloaded, try again shortly: {result.get('error', 'rejected')}",
        }

    if not result.get("success"):
        # A guard rejection is a policy event, not a fault — the same
        # distinction the text_to_sql path records, so a count of "how often
        # does the agent try to write?" covers both SQL surfaces.
        rejected = bool(result.get("rejected_by_guard"))
        error_text = str(result.get("error", "query failed"))
        _record(audit.DECISION_DENY if rejected else audit.DECISION_ERROR, None, error_text)
        return None, {"tool_name": "execute_sql", "error": error_text}

    _record(audit.DECISION_ALLOW, result.get("row_count"),
            "truncated" if result.get("is_truncated") else "")

    import json as _json
    return {
        "id": f"sql_sb_{uuid.uuid4().hex[:8]}",
        "content": _json.dumps(result.get("rows", []), ensure_ascii=False, default=str),
        "source": "tool/execute_sql",
        "metadata": {"row_count": result.get("row_count"), "is_truncated": result.get("is_truncated", False)},
    }, None
