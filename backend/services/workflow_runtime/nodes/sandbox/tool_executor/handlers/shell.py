"""bash — a shell command, run in the sandbox container, never in this process.

This handler used to be `subprocess.run(command, shell=True)`: a model-authored
string handed to a shell, inside the process that holds every tenant's database
credentials and the LiteLLM key and serves every other conversation on the same
event loop. `tool_config.bash.enabled` gated it, but a config flag is not a
boundary — whatever the workflow_runtime container could reach, the command
could reach, with the runtime's own identity.

It goes to sandbox_executor's /execute/shell now, which runs it in the same
pooled container execute_python uses: no network, read-only root filesystem,
tmpfs /tmp, pids/memory/cpu caps, killed and wiped after every exec.

Two behaviours change, and both are the point rather than a regression: `cwd`
names a directory INSIDE the sandbox, and the host's filesystem, network and
credentials are simply not there. A command that depended on either was
reaching outside the isolation the platform claims to have.
"""
from typing import Any
import logging
import uuid

import httpx

from libs.shared import audit
from nodes.sandbox.tool_executor._sandbox_http import POLL_OVERHEAD_S, submit_and_poll
from nodes.sandbox.tool_executor._utils import _get_query, audit_ctx

logger = logging.getLogger(__name__)

# Same default the in-process version used for its subprocess timeout. The
# sandbox enforces it from outside the container with a SIGKILL, so unlike a
# shell trap there is nothing inside the command that can decline it.
_DEFAULT_TIMEOUT_S = 30


async def _bash_handler(call: dict[str, Any], cfg: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    tool_cfg = cfg.get("tool_config", {}).get("bash", {})
    if not tool_cfg.get("enabled", False):
        return None, {"tool_name": "bash", "error": "bash tool not enabled for this agent (set tool_config.bash.enabled=true)"}

    command = call.get("arguments", {}).get("command", "") or _get_query(call)
    if not command:
        return None, {"tool_name": "bash", "error": "no command provided"}

    cwd     = tool_cfg.get("cwd", None)
    timeout = int(call.get("arguments", {}).get("timeout", _DEFAULT_TIMEOUT_S))

    # A model-authored shell command is the least predictable access in the tool
    # executor, which makes it the one most worth having a record of. Recorded
    # before it runs: an investigation into what a command did starts from the
    # fact that it was submitted.
    audit.record_tool_access(
        action=audit.ACTION_SHELL_EXEC,
        target="bash",
        target_objects=[cwd] if cwd else [],
        detail=command,
        **audit_ctx(cfg),
    )

    body: dict[str, Any] = {"command": command, "timeout_s": timeout}
    if cwd:
        body["workdir"] = cwd

    try:
        async with httpx.AsyncClient(timeout=10) as http:
            result = await submit_and_poll(
                http, "/execute/shell", body, overall_timeout_s=timeout + POLL_OVERHEAD_S,
            )
    except Exception as e:
        return None, {"tool_name": "bash", "error": f"sandbox_executor unreachable: {e}"}

    if result.get("_http_status") == 503:
        return None, {
            "tool_name": "bash",
            "error": f"sandbox_executor is overloaded, try again shortly: {result.get('error', 'rejected')}",
        }

    if result.get("timed_out"):
        return None, {"tool_name": "bash", "error": f"timeout after {timeout}s"}

    # A non-zero exit code is an ANSWER, not a transport failure — `grep` that
    # matches nothing exits 1 — so it comes back as content with the code
    # appended, exactly as the in-process version reported it. Only a command
    # that could not be run at all is an error, and that is the case with no
    # exit code to report.
    if "exit_code" not in result:
        return None, {"tool_name": "bash", "error": result.get("error", "execution failed")}

    output = result.get("stdout", "") or ""
    stderr = result.get("stderr", "") or ""
    if stderr:
        output += ("\n--- stderr ---\n" if output else "") + stderr
    exit_code = result.get("exit_code", -1)
    if exit_code != 0:
        output += f"\n(exit code: {exit_code})"
    if result.get("is_truncated"):
        output += "\n[output truncated]"

    logger.info("bash | sandboxed cmd=%r exit=%s", command[:60], exit_code)
    return {
        "id": f"bash_{uuid.uuid4().hex[:8]}",
        "content": output or "(no output)",
        "source": "tool/bash",
        "metadata": {"exit_code": exit_code, "executed_via": "sandbox_executor"},
    }, None
