"""Python and shell job execution orchestration."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
import logging
from typing import Any


async def run_shell_job(
    payload: dict[str, Any],
    *,
    queue: Any,
    runner: Callable[..., dict[str, Any]],
    timeout_error: type[Exception],
    run_error: type[Exception],
    minio_store: Any,
    truncate_output: Callable[[str, int], tuple[str, bool]],
    max_output_chars: int,
    logger: logging.Logger,
) -> dict[str, Any]:
    command = payload["command"]
    job_id = payload["job_id"]
    minio_key = f"shell/{job_id}.sh"

    await asyncio.to_thread(minio_store.put_code, minio_key, command)
    try:
        result = await asyncio.to_thread(
            runner,
            command,
            payload["timeout_s"],
            on_output=lambda stream, text: queue.append_output(job_id, stream, text),
            workdir=payload.get("workdir"),
        )
    except timeout_error as exc:
        return {"success": False, "error": str(exc), "timed_out": True}
    except run_error as exc:
        logger.error("execute_shell | sandbox run failed: %s", exc)
        return {"success": False, "error": str(exc)}
    finally:
        await asyncio.to_thread(minio_store.delete_code, minio_key)

    stdout, truncated = truncate_output(result["stdout"], max_output_chars)
    return {
        "success": result["exit_code"] == 0,
        "stdout": stdout,
        "stderr": result["stderr"],
        "exit_code": result["exit_code"],
        "timed_out": False,
        "is_truncated": truncated,
    }


async def run_python_job(
    payload: dict[str, Any],
    *,
    queue: Any,
    runner: Callable[..., dict[str, Any]],
    timeout_error: type[Exception],
    run_error: type[Exception],
    minio_store: Any,
    validate_code: Callable[[str], None],
    security_error: type[Exception],
    truncate_output: Callable[[str, int], tuple[str, bool]],
    max_output_chars: int,
    logger: logging.Logger,
) -> dict[str, Any]:
    code = payload["code"]
    job_id = payload["job_id"]

    try:
        validate_code(code)
    except security_error as exc:
        logger.warning("execute_python | rejected by AST validation: %s", exc)
        return {"success": False, "error": f"security validation failed: {exc}"}
    except SyntaxError as exc:
        return {"success": False, "error": f"syntax error: {exc}"}

    minio_key = f"python/{job_id}.py"
    await asyncio.to_thread(minio_store.put_code, minio_key, code)
    try:
        result = await asyncio.to_thread(
            runner,
            code,
            payload["timeout_s"],
            payload["memory_mb"],
            payload["cpus"],
            on_output=lambda stream, text: queue.append_output(job_id, stream, text),
            files={
                name: base64.b64decode(encoded)
                for name, encoded in (payload.get("files") or {}).items()
            },
        )
    except timeout_error as exc:
        return {"success": False, "error": str(exc), "timed_out": True}
    except run_error as exc:
        logger.error("execute_python | sandbox run failed: %s", exc)
        return {"success": False, "error": str(exc)}
    finally:
        await asyncio.to_thread(minio_store.delete_code, minio_key)

    stdout, truncated = truncate_output(result["stdout"], max_output_chars)
    return {
        "success": result["exit_code"] == 0,
        "stdout": stdout,
        "stderr": result["stderr"],
        "exit_code": result["exit_code"],
        "timed_out": False,
        "is_truncated": truncated,
    }

