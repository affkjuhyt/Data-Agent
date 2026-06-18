import asyncio
import logging
import subprocess
import uuid

from nodes.sandbox.tool_executor._utils import _get_query

logger = logging.getLogger(__name__)


async def _bash_handler(call: dict, cfg: dict) -> tuple[dict | None, dict | None]:
    tool_cfg = cfg.get("tool_config", {}).get("bash", {})
    if not tool_cfg.get("enabled", False):
        return None, {"tool_name": "bash", "error": "bash tool not enabled for this agent (set tool_config.bash.enabled=true)"}

    command = call.get("arguments", {}).get("command", "") or _get_query(call)
    if not command:
        return None, {"tool_name": "bash", "error": "no command provided"}

    cwd     = tool_cfg.get("cwd", None)
    timeout = int(call.get("arguments", {}).get("timeout", 30))

    try:
        r = await asyncio.to_thread(
            subprocess.run, command, shell=True,
            capture_output=True, text=True, timeout=timeout, cwd=cwd,
        )
        output = r.stdout
        if r.stderr:
            output += ("\n--- stderr ---\n" if r.stdout else "") + r.stderr
        if r.returncode != 0:
            output += f"\n(exit code: {r.returncode})"
        logger.info("bash | cmd=%r exit=%d", command[:60], r.returncode)
        return {"id": f"bash_{uuid.uuid4().hex[:8]}", "content": output or "(no output)",
                "source": "tool/bash", "metadata": {"exit_code": r.returncode}}, None
    except asyncio.TimeoutError:
        return None, {"tool_name": "bash", "error": f"timeout after {timeout}s"}
    except Exception as e:
        return None, {"tool_name": "bash", "error": str(e)}
