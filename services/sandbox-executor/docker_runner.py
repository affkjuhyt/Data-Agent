"""Ephemeral Docker sandbox execution (doc §1.3, §1.4).

Each call spins up one throwaway container from the pre-built
``sandbox-runner:latest`` image, runs the (already AST-validated) code, and
removes the container — no state survives between calls. Resource limits are
hard container flags the agent cannot override; the timeout is enforced by
this orchestrator stopping the container from the outside, not by a signal
handler inside it, so malicious code can't intercept/ignore it.
"""
import logging

import docker
from docker.errors import APIError, ContainerError, ImageNotFound

logger = logging.getLogger(__name__)

SANDBOX_IMAGE = "sandbox-runner:latest"
DEFAULT_TIMEOUT_S = 10
DEFAULT_MEMORY_MB = 256
DEFAULT_CPUS = 0.5
DEFAULT_PIDS_LIMIT = 50


class ExecutionTimeoutError(Exception):
    pass


class SandboxRunError(Exception):
    pass


_client = None


def _get_client():
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


def run_python_sandboxed(
    code: str,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    memory_mb: int = DEFAULT_MEMORY_MB,
    cpus: float = DEFAULT_CPUS,
) -> dict:
    """Returns {"stdout": str, "stderr": str, "exit_code": int, "timed_out": bool}."""
    client = _get_client()
    container = None
    try:
        container = client.containers.run(
            SANDBOX_IMAGE,
            ["python3", "-c", code],
            detach=True,
            network_disabled=True,        # --network none
            mem_limit=f"{memory_mb}m",    # --memory
            nano_cpus=int(cpus * 1e9),    # --cpus
            read_only=True,                # --read-only
            tmpfs={"/tmp": "size=64m"},    # --tmpfs /tmp:size=64m
            pids_limit=DEFAULT_PIDS_LIMIT,  # --pids-limit
            security_opt=["no-new-privileges"],
        )

        timed_out = False
        try:
            result = container.wait(timeout=timeout_s)
            exit_code = result.get("StatusCode", -1)
        except Exception:
            timed_out = True
            exit_code = -1
            try:
                container.stop(timeout=1)
            except APIError as e:
                logger.warning("sandbox | container.stop failed after timeout: %s", e)

        stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
        stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")

        if timed_out:
            raise ExecutionTimeoutError(f"execution exceeded {timeout_s}s timeout")

        return {"stdout": stdout, "stderr": stderr, "exit_code": exit_code, "timed_out": False}
    except ImageNotFound as e:
        raise SandboxRunError(f"sandbox image '{SANDBOX_IMAGE}' not found — build it first: {e}") from e
    except ContainerError as e:
        raise SandboxRunError(str(e)) from e
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except APIError as e:
                logger.warning("sandbox | container cleanup failed: %s", e)
