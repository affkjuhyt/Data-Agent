"""execute_python / execute_sql — Code Interpreter Sandbox (sandbox_schema_docs.docx §1).

Unlike text_to_pandas (handlers/data.py), which execs LLM-generated pandas
code in-process, these two tools never run agent-authored code/SQL inside
workflow-runtime itself. Both call out to the sandbox-executor service over
HTTP — the only component in the platform that touches the Docker socket
(hub-and-spoke isolation, doc §1.2) or holds the read-only Snowflake
service-account credentials (doc §1.5).

docs/1.md "Kế hoạch 1" mục i: sandbox-executor's POST /execute/python and
/execute/sql no longer run the request inline — they enqueue and return
202 + {job_id} immediately, without holding one HTTP connection open for
the full acquire+exec/query duration. Both handlers below submit, then
poll GET .../{job_id} with backoff until a terminal status. The contract
this module exposes to the agent loop is unchanged: the tool-call still
blocks until a final result (mục i, "đã chốt mô hình đồng bộ") — only the
transport underneath changed.
"""
from typing import Any
import asyncio
import logging
import os
import time
import uuid

import httpx

logger = logging.getLogger(__name__)

_SANDBOX_EXECUTOR_URL = os.environ.get("SANDBOX_EXECUTOR_URL", "http://localhost:8500")

_POLL_INITIAL_DELAY_S = 0.2
_POLL_MAX_DELAY_S = 1.0
# Budget on top of the tool's own execution timeout to cover queueing delay
# (worker pull latency, or a circuit-breaker HALF_OPEN cooldown wait) that
# now sits in front of acquire+exec — didn't exist when the request ran
# inline, so it isn't part of timeout_s itself.
_POLL_OVERHEAD_S = 15


async def _submit_and_poll(http: httpx.AsyncClient, path: str, body: dict[str, Any], overall_timeout_s: float) -> dict[str, Any]:
    """POSTs to enqueue, then polls GET {path}/{job_id} with backoff until
    a terminal status or overall_timeout_s elapses. Returns the terminal
    response body with `_http_status` merged in, so callers can tell a
    circuit-breaker 503 (sandbox-executor overloaded, back off) apart from
    an ordinary handled failure (success: false) without re-deriving it.
    """
    resp = await http.post(f"{_SANDBOX_EXECUTOR_URL}{path}", json=body)
    if resp.status_code == 503:
        # Queue was already full (docs/1.md mục i backpressure) — rejected
        # before a job_id was even minted, nothing to poll.
        data: dict[str, Any] = resp.json()
        data["_http_status"] = 503
        return data
    resp.raise_for_status()
    job_id = resp.json()["job_id"]

    deadline = time.monotonic() + overall_timeout_s
    delay = _POLL_INITIAL_DELAY_S
    while True:
        poll = await http.get(f"{_SANDBOX_EXECUTOR_URL}{path}/{job_id}")
        data = poll.json()
        data["_http_status"] = poll.status_code
        if data.get("status") in ("queued", "running"):
            if time.monotonic() >= deadline:
                return {
                    "success": False,
                    "error": f"timed out waiting for sandbox-executor job {job_id} to finish",
                    "_http_status": 504,
                }
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, _POLL_MAX_DELAY_S)
            continue
        return data


async def _execute_python_handler(call: dict[str, Any], cfg: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    code = call.get("arguments", {}).get("code") or call.get("query", "")
    if not code:
        return None, {"tool_name": "execute_python", "error": "no code provided"}

    tool_cfg = cfg.get("tool_config", {}).get("execute_python", {})
    timeout_s = tool_cfg.get("timeout_s", 10)
    # memory_mb/cpus are omitted unless a workflow explicitly overrides
    # them — sandbox-executor's container pool has these fixed at creation
    # (SANDBOX_MEMORY_MB/SANDBOX_CPUS) and rejects any request whose value
    # doesn't match, so hardcoding a duplicate 256/0.5 default here would
    # silently break every call the moment an operator retunes that env
    # var on sandbox-executor without also editing this file.
    request_body = {"code": code, "timeout_s": timeout_s}
    if "memory_mb" in tool_cfg:
        request_body["memory_mb"] = tool_cfg["memory_mb"]
    if "cpus" in tool_cfg:
        request_body["cpus"] = tool_cfg["cpus"]

    try:
        async with httpx.AsyncClient(timeout=10) as http:
            result = await _submit_and_poll(
                http, "/execute/python", request_body, overall_timeout_s=timeout_s + _POLL_OVERHEAD_S
            )
    except Exception as e:
        return None, {"tool_name": "execute_python", "error": f"sandbox-executor unreachable: {e}"}

    if result.get("_http_status") == 503:
        return None, {
            "tool_name": "execute_python",
            "error": f"sandbox-executor is overloaded, try again shortly: {result.get('error', 'rejected')}",
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

    try:
        async with httpx.AsyncClient(timeout=10) as http:
            result = await _submit_and_poll(
                http, "/execute/sql", {"sql": sql, "connection": connection}, overall_timeout_s=60 + _POLL_OVERHEAD_S
            )
    except Exception as e:
        return None, {"tool_name": "execute_sql", "error": f"sandbox-executor unreachable: {e}"}

    if result.get("_http_status") == 503:
        return None, {
            "tool_name": "execute_sql",
            "error": f"sandbox-executor is overloaded, try again shortly: {result.get('error', 'rejected')}",
        }

    if not result.get("success"):
        return None, {"tool_name": "execute_sql", "error": result.get("error", "query failed")}

    import json as _json
    return {
        "id": f"sql_sb_{uuid.uuid4().hex[:8]}",
        "content": _json.dumps(result.get("rows", []), ensure_ascii=False, default=str),
        "source": "tool/execute_sql",
        "metadata": {"row_count": result.get("row_count"), "is_truncated": result.get("is_truncated", False)},
    }, None
