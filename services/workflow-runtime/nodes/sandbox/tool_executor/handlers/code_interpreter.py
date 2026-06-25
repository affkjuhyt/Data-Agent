"""execute_python / execute_sql — Code Interpreter Sandbox (sandbox_schema_docs.docx §1).

Unlike text_to_pandas (handlers/data.py), which execs LLM-generated pandas
code in-process, these two tools never run agent-authored code/SQL inside
workflow-runtime itself. Both call out to the sandbox-executor service over
HTTP — the only component in the platform that touches the Docker socket
(hub-and-spoke isolation, doc §1.2) or holds the read-only Snowflake
service-account credentials (doc §1.5).
"""
import logging
import os
import uuid

import httpx

logger = logging.getLogger(__name__)

_SANDBOX_EXECUTOR_URL = os.environ.get("SANDBOX_EXECUTOR_URL", "http://localhost:8500")


async def _execute_python_handler(call: dict, cfg: dict) -> tuple[dict | None, dict | None]:
    code = call.get("arguments", {}).get("code") or call.get("query", "")
    if not code:
        return None, {"tool_name": "execute_python", "error": "no code provided"}

    tool_cfg = cfg.get("tool_config", {}).get("execute_python", {})
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.post(
                f"{_SANDBOX_EXECUTOR_URL}/execute/python",
                json={
                    "code": code,
                    "timeout_s": tool_cfg.get("timeout_s", 10),
                    "memory_mb": tool_cfg.get("memory_mb", 256),
                    "cpus": tool_cfg.get("cpus", 0.5),
                },
            )
            resp.raise_for_status()
            result = resp.json()
    except Exception as e:
        return None, {"tool_name": "execute_python", "error": f"sandbox-executor unreachable: {e}"}

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


async def _execute_sql_handler(call: dict, cfg: dict) -> tuple[dict | None, dict | None]:
    sql = call.get("arguments", {}).get("sql") or call.get("query", "")
    if not sql:
        return None, {"tool_name": "execute_sql", "error": "no sql provided"}

    tool_cfg = cfg.get("tool_config", {}).get("execute_sql", {})
    connection = tool_cfg.get("connection", {})
    if not connection.get("account"):
        return None, {"tool_name": "execute_sql", "error": "Snowflake connection not configured"}

    try:
        async with httpx.AsyncClient(timeout=60) as http:
            resp = await http.post(
                f"{_SANDBOX_EXECUTOR_URL}/execute/sql",
                json={"sql": sql, "connection": connection},
            )
            resp.raise_for_status()
            result = resp.json()
    except Exception as e:
        return None, {"tool_name": "execute_sql", "error": f"sandbox-executor unreachable: {e}"}

    if not result.get("success"):
        return None, {"tool_name": "execute_sql", "error": result.get("error", "query failed")}

    import json as _json
    return {
        "id": f"sql_sb_{uuid.uuid4().hex[:8]}",
        "content": _json.dumps(result.get("rows", []), ensure_ascii=False, default=str),
        "source": "tool/execute_sql",
        "metadata": {"row_count": result.get("row_count"), "is_truncated": result.get("is_truncated", False)},
    }, None
